#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_BYTES = 200_000_000


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


def http_get(url, token, timeout=120):
    for attempt in range(4):
        request = urllib.request.Request(url, method="GET")
        request.add_header("PRIVATE-TOKEN", token)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                if len(data) > MAX_BYTES:
                    die("attachment exceeds size limit")
                return response.status, data
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
            die("download failed after retryable network errors")
    die("download failed")


def main():
    parser = argparse.ArgumentParser(description="download locked bundle-tool executor")
    parser.add_argument("--lock-file", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    lock = load_parse_lock()(args.lock_file)
    if lock["mode"] != "release":
        die("executor download requires a release lock")
    asset = next((item for item in lock["assets"] if item["platform"] == args.platform), None)
    if asset is None:
        die(f"lock does not contain platform {args.platform}")
    api = env("SOURCE_GIT_API").rstrip("/")
    token = env("SOURCE_GIT_TOKEN")
    project = str(lock["project"])
    tag = lock["tag"]
    name = asset["name"]
    quoted = (
        f"{api}/projects/{urllib.parse.quote(project, safe='')}"
        f"/releases/{urllib.parse.quote(tag, safe='')}"
        f"/attachments/{urllib.parse.quote(name, safe='')}"
    )
    status, data = http_get(quoted, token)
    if status != 200:
        die(f"attachment download failed: HTTP {status}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != asset["sha256"]:
        die("executor sha256 mismatch")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    output.chmod(output.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"ok: wrote {output.name}")


if __name__ == "__main__":
    main()
