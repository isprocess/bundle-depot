#!/usr/bin/env python3
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

SCRIPTS = Path(__file__).resolve().parent
PRODUCTION = [
    "check-payload.py",
    "verify-tag.py",
    "fetch-source.py",
    "parse-lock.py",
    "download-executor.py",
    "check-bootstrap-window.py",
    "read-repo-file.py",
]
TAG = "v0.3.0"
SHA = "0123456789abcdef0123456789abcdef01234567"
RID = "b-0.3.0-0123456789ab-20260916T120000Z"
DIGEST = "a" * 64


def run_script(name, env=None, args=None, cwd=None):
    command = ["python3", str(SCRIPTS / name)]
    if args:
        command.extend(args)
    merged = {"PATH": os.environ.get("PATH", ""), "LANG": "C"}
    if env:
        merged.update(env)
    return subprocess.run(command, text=True, capture_output=True, env=merged, cwd=cwd, check=False)


def bootstrap_lock():
    return 'schema = "bundle-tool-lock.v1"\nmode = "bootstrap"\n'


def release_lock(project=42, name="bundle-tool"):
    assets = []
    for platform, asset in (
        ("linux-x64", "bundle-tool-linux-x64.bin"),
        ("windows-x64", "bundle-tool-windows-x64.exe"),
        ("macos-x64", "bundle-tool-macos-x64.bin"),
        ("macos-arm64", "bundle-tool-macos-arm64.bin"),
    ):
        assets.append(
            f'[[assets]]\nplatform = "{platform}"\nname = "{asset}"\nsha256 = "{DIGEST}"\n'
        )
    return (
        'schema = "bundle-tool-lock.v1"\n'
        'mode = "release"\n'
        f"project = {project}\n"
        f'name = "{name}"\n'
        f'tag = "{TAG}"\n'
        f'source_sha = "{SHA}"\n\n'
        + "\n".join(assets)
    )


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    payload = buffer.getvalue()
    if len(payload) < 1000:
        payload += b"\x00" * (1000 - len(payload))
        # padding after zip end would break zip; ensure content is large instead
    return payload


def make_large_zip(entries):
    padded = []
    for name, data in entries:
        if len(data) < 1200:
            data = data + (b"X" * (1200 - len(data)))
        padded.append((name, data))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in padded:
            archive.writestr(name, data)
    return buffer.getvalue()


def envelope(name="bundle-tool", state="release", published_once=True, sha=SHA, tag=TAG):
    flag = "true" if published_once else "false"
    return (
        "<!-- release-envelope.v2\n"
        '{"schema":"release-envelope.v2","state":"' + state + '","published_once":' + flag +
        ',"kind":"files","name":"' + name + '","tag":"' + tag +
        '","source_commit_sha":"' + sha + '","request_id":"' + RID +
        '","meta":"bundle-tool-v0.3.0-meta.zip"}\n'
        "-->\n"
    )


class SourceState:
    def __init__(self):
        self.tag_sha = SHA
        self.archive = b""
        self.files = {}
        self.releases = {}
        self.attachments = {}
        self.project = {"default_branch": "master"}
        self.requests = []


def start_stub(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def send_json(self, status, value):
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_bytes(self, status, data, content_type="application/octet-stream"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            state.requests.append("GET " + self.path)
            if path.endswith("/repository/tags/v0.3.0") or path.endswith("/repository/tags/v0.2.9"):
                tag = path.rsplit("/", 1)[-1]
                self.send_json(200, {"name": tag, "commit": {"id": state.tag_sha}})
                return
            if "/repository/archive" in path:
                self.send_bytes(200, state.archive, "application/zip")
                return
            if path.endswith("/repository/files"):
                file_path = query.get("file_path", [""])[0]
                ref = query.get("ref", [""])[0]
                key = (file_path, ref)
                if key not in state.files and file_path in state.files:
                    key = file_path
                if key not in state.files:
                    self.send_json(404, {"message": "not found"})
                    return
                import base64
                content = state.files[key]
                if isinstance(content, str):
                    content = content.encode("utf-8")
                self.send_json(200, {
                    "file_path": file_path,
                    "encoding": "base64",
                    "content": base64.b64encode(content).decode("ascii"),
                })
                return
            if path.endswith("/releases") or "/releases?" in (path + "?"):
                items = []
                for tag, item in state.releases.items():
                    row = dict(item)
                    row.setdefault("tag_name", tag)
                    items.append(row)
                self.send_json(200, items if items else [])
                return
            if "/releases/" in path and path.endswith("/attachments/bundle-tool-linux-x64.bin"):
                data = state.attachments.get("bundle-tool-linux-x64.bin", b"executor-bytes\n")
                self.send_bytes(200, data)
                return
            if "/releases/" in path and "/attachments/" in path:
                name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
                if name not in state.attachments:
                    self.send_json(404, {"message": "missing"})
                    return
                self.send_bytes(200, state.attachments[name])
                return
            if "/releases/" in path:
                tag = urllib.parse.unquote(path.rsplit("/", 1)[-1])
                if tag not in state.releases:
                    self.send_json(404, {"message": "not found"})
                    return
                self.send_json(200, state.releases[tag])
                return
            if path.endswith("/projects/42") or re.search(r"/projects/\d+$", path):
                self.send_json(200, state.project)
                return
            self.send_json(404, {"message": "unknown"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return f"http://127.0.0.1:{server.server_port}", stop


class NeutralScriptTest(unittest.TestCase):
    def test_production_scripts_exist(self):
        for name in PRODUCTION:
            self.assertTrue((SCRIPTS / name).is_file(), name)

    def test_check_payload_accepts_exact_fields_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "github_output"
            result = run_script("check-payload.py", {
                "PAYLOAD": json.dumps({"tag": TAG, "sha": SHA, "rid": RID}),
                "GITHUB_OUTPUT": str(output),
            })
            self.assertEqual(result.returncode, 0, result.stderr)
            text = output.read_text(encoding="utf-8")
            self.assertIn(f"tag={TAG}\n", text)
            self.assertIn(f"sha={SHA}\n", text)
            self.assertIn(f"rid={RID}\n", text)
            self.assertIn(TAG, result.stdout)
            self.assertIn(SHA, result.stdout)
            self.assertIn(RID, result.stdout)

    def test_check_payload_rejects_extra_or_bad_rid_prefix(self):
        extra = run_script("check-payload.py", {
            "PAYLOAD": json.dumps({"tag": TAG, "sha": SHA, "rid": RID, "extra": "1"}),
        })
        self.assertNotEqual(extra.returncode, 0)
        bad = run_script("check-payload.py", {
            "PAYLOAD": json.dumps({
                "tag": TAG,
                "sha": SHA,
                "rid": "b-0.2.0-0123456789ab-20260916T120000Z",
            }),
        })
        self.assertNotEqual(bad.returncode, 0)

    def test_verify_tag_fails_on_sha_mismatch(self):
        state = SourceState()
        state.tag_sha = "f" * 40
        api, stop = start_stub(state)
        try:
            result = run_script("verify-tag.py", {
                "SOURCE_GIT_API": api,
                "SOURCE_GIT_TOKEN": "test-token",
                "SOURCE_GIT_PROJECT": "42",
                "TAG": TAG,
                "SHA": SHA,
            })
        finally:
            stop()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mismatch", (result.stderr + result.stdout).lower())
        self.assertNotIn("test-token", result.stderr + result.stdout)

    def test_verify_tag_accepts_matching_sha(self):
        state = SourceState()
        api, stop = start_stub(state)
        try:
            result = run_script("verify-tag.py", {
                "SOURCE_GIT_API": api,
                "SOURCE_GIT_TOKEN": "test-token",
                "SOURCE_GIT_PROJECT": "42",
                "TAG": TAG,
                "SHA": SHA,
            })
        finally:
            stop()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fetch_source_rejects_parent_and_absolute_entries(self):
        for unsafe in ("../evil", "/tmp/evil"):
            state = SourceState()
            state.archive = make_large_zip([(unsafe, b"nope\n")])
            api, stop = start_stub(state)
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "src"
                    result = run_script("fetch-source.py", {
                        "SOURCE_GIT_API": api,
                        "SOURCE_GIT_TOKEN": "test-token",
                        "SOURCE_GIT_PROJECT": "42",
                        "SHA": SHA,
                    }, args=["--output", str(out)], cwd=tmp)
            finally:
                stop()
            self.assertNotEqual(result.returncode, 0, unsafe)
            self.assertIn("unsafe", (result.stderr + result.stdout).lower())

    def test_fetch_source_extracts_and_deletes_archive(self):
        state = SourceState()
        state.archive = make_large_zip([("repo/README.md", b"hello source\n")])
        api, stop = start_stub(state)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                out = tmpdir / "src"
                result = run_script("fetch-source.py", {
                    "SOURCE_GIT_API": api,
                    "SOURCE_GIT_TOKEN": "test-token",
                    "SOURCE_GIT_PROJECT": "42",
                    "SHA": SHA,
                    "TMPDIR": str(tmpdir / "tmp"),
                }, args=["--output", str(out)], cwd=str(tmpdir))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((out / "README.md").is_file())
                zips = list(tmpdir.rglob("*.zip"))
                self.assertEqual(zips, [])
        finally:
            stop()

    def test_parse_lock_distinguishes_modes_and_rejects_mixed_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            boot = root / "boot.lock"
            rel = root / "rel.lock"
            mixed = root / "mixed.lock"
            boot.write_text(bootstrap_lock(), encoding="utf-8")
            rel.write_text(release_lock(), encoding="utf-8")
            mixed.write_text(bootstrap_lock() + "project = 42\n", encoding="utf-8")
            boot_result = run_script("parse-lock.py", args=["--lock-file", str(boot)])
            rel_result = run_script("parse-lock.py", args=["--lock-file", str(rel)])
            mixed_result = run_script("parse-lock.py", args=["--lock-file", str(mixed)])
            self.assertEqual(boot_result.returncode, 0, boot_result.stderr)
            self.assertIn("mode=bootstrap", boot_result.stdout)
            self.assertEqual(rel_result.returncode, 0, rel_result.stderr)
            self.assertIn("mode=release", rel_result.stdout)
            self.assertIn("project=42", rel_result.stdout)
            self.assertNotEqual(mixed_result.returncode, 0)

    def test_download_executor_verifies_sha256_and_writes_file(self):
        payload = b"executor-bytes\n" + b"Y" * 64
        digest = hashlib.sha256(payload).hexdigest()
        lock = release_lock().replace(DIGEST, digest)
        state = SourceState()
        state.attachments["bundle-tool-linux-x64.bin"] = payload
        state.releases[TAG] = {
            "tag_name": TAG,
            "description": envelope(),
            "attachments": [{"name": "bundle-tool-linux-x64.bin", "size": len(payload)}],
        }
        api, stop = start_stub(state)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                lock_path = Path(tmp) / "bundle-tool.lock"
                out = Path(tmp) / "bundle-tool"
                lock_path.write_text(lock, encoding="utf-8")
                result = run_script("download-executor.py", {
                    "SOURCE_GIT_API": api,
                    "SOURCE_GIT_TOKEN": "test-token",
                }, args=["--lock-file", str(lock_path), "--platform", "linux-x64", "--output", str(out)])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(out.read_bytes(), payload)
                self.assertTrue(out.stat().st_mode & stat.S_IXUSR)
        finally:
            stop()

    def test_read_repo_file_decodes_base64_contents(self):
        state = SourceState()
        state.files[("bundle-tool.lock", SHA)] = bootstrap_lock()
        api, stop = start_stub(state)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "bundle-tool.lock"
                result = run_script("read-repo-file.py", {
                    "SOURCE_GIT_API": api,
                    "SOURCE_GIT_TOKEN": "test-token",
                    "SOURCE_GIT_PROJECT": "42",
                    "SHA": SHA,
                }, args=["--path", "bundle-tool.lock", "--output", str(out)])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(out.read_text(encoding="utf-8"), bootstrap_lock())
                self.assertTrue(any("file_path=bundle-tool.lock" in req for req in state.requests))
                self.assertFalse(any("/repository/files/bundle-tool.lock" in req for req in state.requests))
        finally:
            stop()

    def test_check_bootstrap_window_uses_argv_and_allows_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "bundle-tool.lock"
            lock_path.write_text(bootstrap_lock(), encoding="utf-8")
            state = SourceState()
            state.releases[TAG] = {
                "tag_name": TAG,
                "description": envelope(published_once=True),
                "attachments": [],
            }
            api, stop = start_stub(state)
            try:
                result = run_script("check-bootstrap-window.py", {
                    "SOURCE_GIT_API": api,
                    "SOURCE_GIT_TOKEN": "test-token",
                }, args=[
                    "--project", "42",
                    "--name", "bundle-tool",
                    "--tag", TAG,
                    "--sha", SHA,
                    "--lock-file", str(lock_path),
                ])
            finally:
                stop()
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_bootstrap_window_rejects_old_tag_and_closed_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "bundle-tool.lock"
            lock_path.write_text(bootstrap_lock(), encoding="utf-8")
            old = run_script("check-bootstrap-window.py", {
                "SOURCE_GIT_API": "http://127.0.0.1:9",
                "SOURCE_GIT_TOKEN": "test-token",
            }, args=[
                "--project", "42",
                "--name", "bundle-tool",
                "--tag", "v0.2.9",
                "--sha", SHA,
                "--lock-file", str(lock_path),
            ])
            self.assertNotEqual(old.returncode, 0)
            state = SourceState()
            state.releases["v0.4.0"] = {
                "tag_name": "v0.4.0",
                "description": envelope(tag="v0.4.0", published_once=True),
                "attachments": [],
            }
            state.files[("bundle-tool.lock", "master")] = bootstrap_lock()
            api, stop = start_stub(state)
            try:
                closed = run_script("check-bootstrap-window.py", {
                    "SOURCE_GIT_API": api,
                    "SOURCE_GIT_TOKEN": "test-token",
                }, args=[
                    "--project", "42",
                    "--name", "bundle-tool",
                    "--tag", TAG,
                    "--sha", SHA,
                    "--lock-file", str(lock_path),
                ])
            finally:
                stop()
            self.assertNotEqual(closed.returncode, 0)

    def test_new_scripts_are_neutral(self):
        for name in PRODUCTION:
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"[\u4e00-\u9fff]", text), name)
            for forbidden in ("git.code.tencent.com", "398245", "398246", "396177", "glpat-", "ghp_", "gho_", "github_pat_"):
                self.assertNotIn(forbidden, text, f"{name} contains {forbidden}")
            source = text.replace(" ", "")
            self.assertIn("--project", (SCRIPTS / "check-bootstrap-window.py").read_text(encoding="utf-8"))
            self.assertNotIn("/repository/files/", text, name)
        window = (SCRIPTS / "check-bootstrap-window.py").read_text(encoding="utf-8")
        self.assertIn("--name", window)
        self.assertIn("--tag", window)
        self.assertIn("--sha", window)
        self.assertIn("--lock-file", window)
        reader = (SCRIPTS / "read-repo-file.py").read_text(encoding="utf-8")
        self.assertIn("file_path", reader)
        self.assertIn("/repository/files?", reader)


if __name__ == "__main__":
    unittest.main(verbosity=2)
