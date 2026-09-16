#!/usr/bin/env python3
import argparse
import base64
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
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def die(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def env(name):
    value = os.environ.get(name)
    if not value:
        die(f"missing required environment variable {name}")
    return value


def http_json(url, token):
    for attempt in range(4):
        request = urllib.request.Request(url, method="GET")
        request.add_header("PRIVATE-TOKEN", token)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            error.read()
            if error.code in RETRY_STATUS and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            die(f"file lookup failed: HTTP {error.code}")
        except (TimeoutError, OSError, urllib.error.URLError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            die("file lookup failed after retryable network errors")
        else:
            if status != 200:
                die(f"file lookup failed: HTTP {status}")
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                die("file lookup returned invalid JSON")
            if not isinstance(value, dict):
                die("file lookup returned non-object JSON")
            return value
    die("file lookup failed")


def main():
    parser = argparse.ArgumentParser(description="read one repository file at a commit")
    parser.add_argument("--path", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.path or args.path.startswith("/") or ".." in Path(args.path).parts:
        die("path is not allowed")
    api = env("SOURCE_GIT_API").rstrip("/")
    token = env("SOURCE_GIT_TOKEN")
    project = env("SOURCE_GIT_PROJECT")
    sha = env("SHA")
    if not SHA_RE.fullmatch(sha):
        die("sha must be a 40-character lowercase hex value")
    url = (
        f"{api}/projects/{urllib.parse.quote(project, safe='')}"
        f"/repository/files/{urllib.parse.quote(args.path, safe='')}?ref={urllib.parse.quote(sha, safe='')}"
    )
    value = http_json(url, token)
    encoding = value.get("encoding")
    content = value.get("content")
    if encoding != "base64" or not isinstance(content, str):
        die("file lookup returned unsupported encoding")
    try:
        data = base64.b64decode(content, validate=False)
    except Exception:
        die("file content is not valid base64")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    print(f"ok: wrote {args.path}")


if __name__ == "__main__":
    main()
