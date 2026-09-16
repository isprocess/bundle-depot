#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RETRY_STATUS = {429, 500, 502, 503, 504}
TAG_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ENVELOPE_PREFIX = "<!-- release-envelope.v2\n"
ENVELOPE_SUFFIX = "\n-->"
ENVELOPE_KEYS = [
    "schema",
    "state",
    "published_once",
    "kind",
    "name",
    "tag",
    "source_commit_sha",
    "request_id",
    "meta",
]


def die(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def env(name):
    value = os.environ.get(name)
    if not value:
        die(f"missing required environment variable {name}")
    return value


def load_parse_lock():
    path = Path(__file__).resolve().with_name("parse-lock.py")
    spec = importlib.util.spec_from_file_location("parse_lock_mod", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_lock


def http_get(url, token):
    for attempt in range(4):
        request = urllib.request.Request(url, method="GET")
        request.add_header("PRIVATE-TOKEN", token)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            body = error.read()
            if error.code in RETRY_STATUS and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return error.code, body
        except (TimeoutError, OSError, urllib.error.URLError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            die("request failed after retryable network errors")
    die("request failed")


def http_json(url, token, ok=(200,)):
    status, body = http_get(url, token)
    if status not in ok:
        die(f"request failed: HTTP {status}")
    if status == 404:
        return status, None
    if not body:
        return status, None
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        die("response is not valid JSON")
    return status, value


def parse_tag(tag):
    match = TAG_RE.fullmatch(tag)
    if not match:
        die("tag is not a supported release tag")
    return tuple(int(part) for part in match.groups())


def parse_envelope(description):
    if not isinstance(description, str) or not description.startswith(ENVELOPE_PREFIX):
        return None
    rest = description[len(ENVELOPE_PREFIX):]
    end = rest.find(ENVELOPE_SUFFIX)
    if end < 0:
        return None
    raw = rest[:end]
    try:
        value = json.loads(raw, object_pairs_hook=lambda pairs: pairs)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list) or [key for key, _ in value] != ENVELOPE_KEYS:
        return None
    env = {key: item for key, item in value}
    if env.get("schema") != "release-envelope.v2":
        return None
    if env.get("state") not in ("staging", "release"):
        return None
    if not isinstance(env.get("published_once"), bool):
        return None
    for key in ("kind", "name", "tag", "source_commit_sha", "request_id", "meta"):
        if not isinstance(env.get(key), str):
            return None
    return env


def project_url(api, project, suffix):
    return f"{api}/projects/{urllib.parse.quote(str(project), safe='')}{suffix}"


def list_releases(api, token, project):
    items = []
    page = 1
    while True:
        url = project_url(api, project, f"/releases?page={page}&per_page=100")
        status, value = http_json(url, token)
        if not isinstance(value, list):
            die("release list returned non-array JSON")
        items.extend(value)
        if len(value) < 100:
            break
        page += 1
        if page > 50:
            die("release list pagination exceeded")
    return items


def default_branch_lock_mode(api, token, project, parse_lock):
    status, project_info = http_json(project_url(api, project, ""), token)
    if not isinstance(project_info, dict):
        die("project lookup returned invalid JSON")
    branch = project_info.get("default_branch")
    if not isinstance(branch, str) or not branch:
        die("project default branch is missing")
    query = urllib.parse.urlencode({"file_path": "bundle-tool.lock", "ref": branch})
    url = project_url(
        api,
        project,
        f"/repository/files?{query}",
    )
    status, value = http_json(url, token, ok=(200, 404))
    if status == 404:
        return None
    import base64
    import tempfile

    content = value.get("content") if isinstance(value, dict) else None
    if value.get("encoding") != "base64" or not isinstance(content, str):
        die("default-branch lock encoding is unsupported")
    try:
        data = base64.b64decode(content, validate=False)
    except Exception:
        die("default-branch lock is not valid base64")
    with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
        handle.write(data)
        temp_path = handle.name
    try:
        parsed = parse_lock(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)
    return parsed["mode"]


def main():
    parser = argparse.ArgumentParser(description="check bootstrap window")
    parser.add_argument("--project", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--lock-file", required=True)
    args = parser.parse_args()
    parse_lock = load_parse_lock()
    lock = parse_lock(args.lock_file)
    if lock["mode"] != "bootstrap":
        die("bootstrap window requires a bootstrap lock")
    version = parse_tag(args.tag)
    if version < (0, 3, 0):
        die("bootstrap tag must be at least v0.3.0")
    if not SHA_RE.fullmatch(args.sha):
        die("sha must be a 40-character lowercase hex value")
    api = env("SOURCE_GIT_API").rstrip("/")
    token = env("SOURCE_GIT_TOKEN")
    tag_url = project_url(
        api,
        args.project,
        f"/releases/{urllib.parse.quote(args.tag, safe='')}",
    )
    status, current = http_json(tag_url, token, ok=(200, 404))
    if status == 200:
        if not isinstance(current, dict):
            die("release lookup returned invalid JSON")
        envelope = parse_envelope(current.get("description", ""))
        if envelope is None:
            die("existing Release has no managed envelope")
        if envelope["name"] != args.name or envelope["tag"] != args.tag:
            die("existing Release identity does not match")
        if envelope["source_commit_sha"] != args.sha:
            die("existing Release source SHA does not match")
        print("ok: bootstrap recovery allowed")
        return
    for item in list_releases(api, token, args.project):
        if not isinstance(item, dict):
            continue
        envelope = parse_envelope(item.get("description", ""))
        if envelope is None:
            continue
        if envelope["name"] == args.name and envelope["published_once"] is True:
            die("bootstrap window is closed")
    mode = default_branch_lock_mode(api, token, args.project, parse_lock)
    if mode == "release":
        die("bootstrap window is closed")
    print("ok: bootstrap window is open")


if __name__ == "__main__":
    main()
