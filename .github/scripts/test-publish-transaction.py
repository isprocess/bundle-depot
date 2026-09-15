#!/usr/bin/env python3
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
TAG = "v0.2.0"
VERSION = "0.2.0"
SHA = "0123456789abcdef0123456789abcdef01234567"
RID = "b-0.2.0-0123456789ab-20260914T120000Z"
ASSET_NAMES = [
    "bundle-tool-linux-x64.bin",
    "bundle-tool-macos-arm64.bin",
    "bundle-tool-macos-x64.bin",
    "bundle-tool-usage.md",
    "bundle-tool-windows-x64.exe",
    "git-tool-linux-x64.bin",
    "git-tool-macos-arm64.bin",
    "git-tool-macos-x64.bin",
    "git-tool-usage.md",
    "git-tool-windows-x64.exe",
]


def extract_inline_script(workflow_path: Path) -> str:
    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    start = None
    for index, line in enumerate(lines):
        if "python3 - <<'PY'" in line:
            start = index + 1
            break
    if start is None:
        raise AssertionError("inline python heredoc is missing")
    body = []
    for line in lines[start:]:
        if line.strip() == "PY":
            return "\n".join(body) + "\n"
        body.append(line[10:] if line.startswith("          ") else line)
    raise AssertionError("inline python heredoc terminator is missing")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_valid_bundle(root: Path, tag: str = TAG, sha: str = SHA, rid: str = RID) -> Path:
    bundle = root / "release-bundle"
    assets = bundle / "assets"
    assets.mkdir(parents=True)
    file_meta = {}
    for name in ASSET_NAMES:
        data = f"{name}\n{sha}\n".encode("utf-8")
        (assets / name).write_bytes(data)
        file_meta[name] = {"size": len(data), "sha256": sha256_bytes(data)}

    def entry(name: str, platform: str) -> dict:
        meta = file_meta[name]
        return {"name": name, "platform": platform, "size": meta["size"], "sha256": meta["sha256"]}

    manifest_name = f"git-doc-{VERSION}-manifest.json"
    checksums_name = f"git-doc-{VERSION}-checksums.txt"
    manifest = {
        "contract_version": "release.v3",
        "tag": tag,
        "version": VERSION,
        "source_commit_sha": sha,
        "request_id": rid,
        "components": [
            {"name": "git-tool", "version": VERSION, "files": [
                entry("git-tool-linux-x64.bin", "linux-x64"),
                entry("git-tool-macos-arm64.bin", "macos-arm64"),
                entry("git-tool-macos-x64.bin", "macos-x64"),
                entry("git-tool-usage.md", "any"),
                entry("git-tool-windows-x64.exe", "windows-x64"),
            ]},
            {"name": "bundle-tool", "version": VERSION, "files": [
                entry("bundle-tool-linux-x64.bin", "linux-x64"),
                entry("bundle-tool-macos-arm64.bin", "macos-arm64"),
                entry("bundle-tool-macos-x64.bin", "macos-x64"),
                entry("bundle-tool-usage.md", "any"),
                entry("bundle-tool-windows-x64.exe", "windows-x64"),
            ]},
        ],
    }
    write_json(assets / manifest_name, manifest)
    targets = sorted(ASSET_NAMES + [manifest_name])
    checksums = [f"{sha256_bytes((assets / name).read_bytes())}  {name}" for name in targets]
    (assets / checksums_name).write_text("\n".join(checksums) + "\n", encoding="utf-8")
    write_json(bundle / "publish.json", {
        "schema": "publish.v1",
        "kind": "files",
        "tag": tag,
        "sha": sha,
        "rid": rid,
        "manifest": manifest_name,
        "checksums": checksums_name,
    })
    (bundle / "release-notes.md").write_text("release notes\n", encoding="utf-8")
    return bundle


def run_inline(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8", delete=False) as handle:
        handle.write(script)
        path = handle.name
    try:
        return subprocess.run(["python3", path], text=True, capture_output=True, env=env, check=False)
    finally:
        Path(path).unlink(missing_ok=True)


@dataclass
class SourceState:
    tag_sha: str = SHA
    release: dict | None = None
    attachments: dict[str, int] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)
    failures: dict[str, list[int]] = field(default_factory=dict)
    tag_reads: int = 0
    flip_sha_after_tag_reads: int | None = None


def injected_status(state: SourceState, method: str, path: str) -> int | None:
    for suffix, statuses in list(state.failures.items()):
        if not statuses:
            continue
        parts = suffix.split(" ", 1)
        if len(parts) == 2 and parts[0] == method and path.endswith(parts[1]):
            return statuses.pop(0)
        if f"{method} {path}".endswith(suffix):
            return statuses.pop(0)
    return None


def start_stub(state: SourceState) -> tuple[str, Callable[[], None]]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def send_json(self, status: int, value: dict) -> None:
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            state.requests.append("GET " + path)
            injected = injected_status(state, "GET", path)
            if injected is not None:
                self.send_json(injected, {"message": "injected"})
                return
            if path == "/projects/396177/repository/tags/v0.2.0":
                state.tag_reads += 1
                sha = "f" * 40 if state.flip_sha_after_tag_reads is not None and state.tag_reads > state.flip_sha_after_tag_reads else state.tag_sha
                self.send_json(200, {"name": TAG, "commit": {"id": sha}})
                return
            if path == "/projects/396177/releases/v0.2.0":
                if state.release is None:
                    self.send_json(404, {"message": "not found"})
                else:
                    value = dict(state.release)
                    value["assets"] = {"links": [{"name": name, "size": size} for name, size in sorted(state.attachments.items())]}
                    self.send_json(200, value)
                return
            self.send_json(404, {"message": "unknown"})

        def read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise AssertionError("request body must be a JSON object")
            return value

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            state.requests.append("POST " + path)
            injected = injected_status(state, "POST", path)
            if injected is not None:
                self.send_json(injected, {"message": "injected"})
                return
            if path == "/projects/396177/releases":
                body = self.read_json_body()
                state.release = {
                    "tag_name": body["tag_name"],
                    "name": body["name"],
                    "description": body["description"],
                    "type": body["type"],
                }
                self.send_json(201, state.release)
                return
            if path == "/projects/396177/releases/v0.2.0/attachments":
                name = self.headers.get("X-Test-Filename")
                size = int(self.headers.get("X-Test-Size", "-1"))
                if not name or size < 0:
                    self.send_json(400, {"message": "missing test upload headers"})
                    return
                state.attachments[name] = size
                self.send_json(201, {"name": name, "size": size})
                return
            self.send_json(404, {"message": "unknown"})

        def do_PUT(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            state.requests.append("PUT " + path)
            injected = injected_status(state, "PUT", path)
            if injected is not None:
                self.send_json(injected, {"message": "injected"})
                return
            if path == "/projects/396177/releases/v0.2.0":
                body = self.read_json_body()
                current = state.release or {"tag_name": TAG, "name": TAG}
                current["description"] = body["description"]
                current["type"] = body["type"]
                state.release = current
                self.send_json(200, current)
                return
            self.send_json(404, {"message": "unknown"})

        def do_DELETE(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            state.requests.append("DELETE " + path)
            injected = injected_status(state, "DELETE", path)
            if injected is not None:
                self.send_json(injected, {"message": "injected"})
                return
            prefix = "/projects/396177/releases/v0.2.0/attachments/"
            if path.startswith(prefix):
                name = urllib.parse.unquote(path[len(prefix):])
                state.attachments.pop(name, None)
                self.send_json(200, {"name": name})
                return
            self.send_json(404, {"message": "unknown"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return f"http://127.0.0.1:{server.server_port}", stop


def run_publish(bundle: Path, state: SourceState) -> subprocess.CompletedProcess[str]:
    api, stop = start_stub(state)
    try:
        env = {
            "SOURCE_API": api,
            "SOURCE_PROJECT": "396177",
            "SOURCE_TOKEN": "test-token",
            "PUBLISH_TAG": TAG,
            "PUBLISH_SHA": SHA,
            "PUBLISH_RID": RID,
            "PUBLISH_BUNDLE": str(bundle),
            "PATH": os.environ.get("PATH", ""),
        }
        return run_inline(extract_inline_script(WORKFLOW), env)
    finally:
        stop()


def expected_remote_assets(bundle: Path) -> dict[str, int]:
    return {path.name: path.stat().st_size for path in sorted((bundle / "assets").iterdir())}


def managed_description(state: str = "release", sha: str = SHA) -> str:
    return (
        "<!-- release-envelope.v1\n"
        '{"schema":"release-envelope.v1","state":"' + state + '","kind":"files","tag":"' + TAG + '",'
        '"source_commit_sha":"' + sha + '","request_id":"' + RID + '","manifest":"git-doc-0.2.0-manifest.json"}\n'
        "-->\n\nGenerated summary\n"
    )


class PublishTransactionTest(unittest.TestCase):
    def run_publish(self, bundle: Path, state: SourceState) -> subprocess.CompletedProcess[str]:
        return run_publish(bundle, state)

    def run_publish_local(self, bundle: Path) -> subprocess.CompletedProcess[str]:
        script = extract_inline_script(WORKFLOW)
        env = {
            "SOURCE_API": "http://127.0.0.1:9",
            "SOURCE_PROJECT": "396177",
            "SOURCE_TOKEN": "test-token",
            "PUBLISH_TAG": TAG,
            "PUBLISH_SHA": SHA,
            "PUBLISH_RID": RID,
            "PUBLISH_BUNDLE": str(bundle),
            "PATH": os.environ.get("PATH", ""),
        }
        return run_inline(script, env)

    def assert_local_failure(self, mutate, expected: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_valid_bundle(Path(tmp))
            mutate(bundle)
            result = self.run_publish_local(bundle)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr + result.stdout)

    def test_local_validation_rejects_duplicate_publish_key_before_network(self) -> None:
        def mutate(bundle: Path) -> None:
            (bundle / "publish.json").write_text(
                '{"schema":"publish.v1","schema":"publish.v1","kind":"files","tag":"' + TAG +
                '","sha":"' + SHA + '","rid":"' + RID +
                '","manifest":"git-doc-0.2.0-manifest.json","checksums":"git-doc-0.2.0-checksums.txt"}\n',
                encoding="utf-8",
            )
        self.assert_local_failure(mutate, "publish.json contains duplicate keys")

    def test_local_validation_rejects_docker_kind(self) -> None:
        def mutate(bundle: Path) -> None:
            data = json.loads((bundle / "publish.json").read_text(encoding="utf-8"))
            data["kind"] = "docker"
            write_json(bundle / "publish.json", data)
        self.assert_local_failure(mutate, "publish.json kind must be files")

    def test_local_validation_rejects_missing_checksum_entry(self) -> None:
        def mutate(bundle: Path) -> None:
            path = bundle / "assets" / "git-doc-0.2.0-checksums.txt"
            lines = path.read_text(encoding="utf-8").splitlines()
            path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        self.assert_local_failure(mutate, "checksums coverage mismatch")

    def test_local_validation_rejects_notes_bom(self) -> None:
        def mutate(bundle: Path) -> None:
            (bundle / "release-notes.md").write_bytes(b"\xef\xbb\xbfbad\n")
        self.assert_local_failure(mutate, "release-notes.md has UTF-8 BOM")
    def test_existing_release_with_different_sha_fails_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_valid_bundle(Path(tmp))
            state = SourceState(release={
                "description": '<!-- release-envelope.v1\n'
                '{"schema":"release-envelope.v1","state":"release","kind":"files","tag":"v0.2.0",'
                '"source_commit_sha":"ffffffffffffffffffffffffffffffffffffffff","request_id":"b-0.2.0-ffffffffffff-20260914T120000Z",'
                '"manifest":"git-doc-0.2.0-manifest.json"}\n'
                '-->\n',
                "type": "release",
            })
            result = self.run_publish(bundle, state)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source SHA does not match", result.stderr + result.stdout)
            self.assertFalse(any(item.startswith("PUT ") or item.startswith("POST ") for item in state.requests))

    def test_existing_release_without_envelope_fails_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_valid_bundle(Path(tmp))
            state = SourceState(release={"description": "manual body\n", "type": "release"})
            result = self.run_publish(bundle, state)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("managed envelope", result.stderr + result.stdout)
            self.assertFalse(any(item.startswith("PUT ") or item.startswith("POST ") for item in state.requests))

    def test_valid_release_v3_bundle_publishes_exact_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_valid_bundle(Path(tmp))
            state = SourceState()
            result = self.run_publish(bundle, state)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIsNotNone(state.release)
            self.assertEqual(state.release["type"], "release")
            self.assertTrue(state.release["description"].startswith("<!-- release-envelope.v1\n"))
            self.assertIn("<!-- release-notes.v1 -->", state.release["description"])
            self.assertEqual(state.attachments, expected_remote_assets(bundle))

    def test_rerun_replaces_existing_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_valid_bundle(Path(tmp))
            state = SourceState(
                release={"description": managed_description(), "type": "release"},
                attachments={"stale.bin": 12},
            )
            result = self.run_publish(bundle, state)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertNotIn("stale.bin", state.attachments)
            self.assertEqual(state.attachments, expected_remote_assets(bundle))

    def test_second_tag_check_failure_leaves_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_valid_bundle(Path(tmp))
            state = SourceState(flip_sha_after_tag_reads=1)
            result = self.run_publish(bundle, state)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("second tag/SHA check failed", result.stderr + result.stdout)
            self.assertIsNotNone(state.release)
            self.assertEqual(state.release["type"], "")
            self.assertEqual(state.attachments, expected_remote_assets(bundle))

    def test_retryable_500_eventually_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_valid_bundle(Path(tmp))
            state = SourceState(failures={"GET /repository/tags/v0.2.0": [500]})
            result = self.run_publish(bundle, state)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(state.release["type"], "release")

    def test_non_retryable_403_fails_without_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_valid_bundle(Path(tmp))
            state = SourceState(failures={"GET /repository/tags/v0.2.0": [403]})
            result = self.run_publish(bundle, state)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("HTTP 403", result.stderr + result.stdout)
            self.assertIsNone(state.release)
            self.assertEqual(state.attachments, {})

    def test_output_does_not_contain_secret_or_header_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_valid_bundle(Path(tmp))
            state = SourceState(failures={"GET /repository/tags/v0.2.0": [403]})
            result = self.run_publish(bundle, state)
            output = result.stderr + result.stdout
            for forbidden in ("test-token", "PRIVATE-TOKEN", "SOURCE_TOKEN"):
                self.assertNotIn(forbidden, output)

    def test_workflow_static_security_contract(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "workflow_call:",
            "actions/download-artifact@fa0a91b85d4f404e444e00e005971372dc801d16",
        ):
            self.assertIn(required, text)
        for forbidden in (
            "secrets: inherit",
            "actions/checkout",
            "softprops/action-gh-release",
            "gh release",
            "git push",
            "git fetch",
            "printenv",
            "set -x",
            "DOCKERHUB",
            "GITHUB_TOKEN",
            "GONGFENG_TOKEN",
            "release.v2",
            "container",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", text))

    def test_inline_script_can_be_extracted_and_compiled(self) -> None:
        script = extract_inline_script(WORKFLOW)
        self.assertIn("import ", script)
        compile(script, "publish-inline.py", "exec")

    def test_workflow_step_uses_neutral_publish_environment(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for name in ("SOURCE_API", "SOURCE_PROJECT", "SOURCE_TOKEN", "PUBLISH_TAG", "PUBLISH_SHA", "PUBLISH_RID", "PUBLISH_BUNDLE"):
            self.assertIn(name + ":", text)
        for old_name in ("GONGFENG_API", "GONGFENG_PROJECT", "GONGFENG_TOKEN"):
            self.assertNotIn(old_name, text)
        for old_assignment in (
            "TAG: ${{ inputs.tag }}",
            "SHA: ${{ inputs.sha }}",
            "RID: ${{ inputs.rid }}",
            "BUNDLE: bundle",
        ):
            self.assertNotIn("\n          " + old_assignment, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
