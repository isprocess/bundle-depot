#!/usr/bin/env python3
import argparse
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

MIN_ARCHIVE_BYTES = 1000
RETRY_STATUS = {429, 500, 502, 503, 504}


def die(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def env(name):
    value = os.environ.get(name)
    if not value:
        die(f"missing required environment variable {name}")
    return value


def download(url, token, dest):
    for attempt in range(4):
        request = urllib.request.Request(url, method="GET")
        request.add_header("PRIVATE-TOKEN", token)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
                dest.write_bytes(data)
                return
        except urllib.error.HTTPError as error:
            error.read()
            if error.code in RETRY_STATUS and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            die(f"archive download failed: HTTP {error.code}")
        except (TimeoutError, OSError, urllib.error.URLError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            die("archive download failed after retryable network errors")
    die("archive download failed")


def is_unsafe(name):
    if not name or name.startswith("/") or name.startswith("\\"):
        return True
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    if re.match(r"^[A-Za-z]:", normalized):
        return True
    parts = Path(normalized).parts
    return any(part in ("..", "") for part in parts)


def common_prefix(names):
    files = [name.replace("\\", "/") for name in names if name and not name.endswith("/")]
    if not files:
        die("archive contains no files")
    prefixes = set()
    for name in files:
        parts = name.split("/")
        if len(parts) == 1:
            prefixes.add("")
        else:
            prefixes.add(parts[0])
    if len(prefixes) == 1:
        prefix = prefixes.pop()
        if prefix:
            return prefix + "/"
    return ""


def extract_zip(archive, output):
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zfile:
        names = zfile.namelist()
        if not names:
            die("archive contains no files")
        if any(is_unsafe(name) for name in names):
            die("archive contains unsafe entry names")
        prefix = common_prefix(names)
        for info in zfile.infolist():
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            relative = name[len(prefix):] if prefix and name.startswith(prefix) else name
            if not relative or is_unsafe(relative):
                die("archive contains unsafe entry names")
            dest = (output / relative).resolve()
            try:
                dest.relative_to(output.resolve())
            except ValueError:
                die("archive contains unsafe entry names")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zfile.read(info))


def main():
    parser = argparse.ArgumentParser(description="fetch and extract source archive")
    parser.add_argument("--output", required=True, help="directory to extract into")
    args = parser.parse_args()
    api = env("SOURCE_GIT_API").rstrip("/")
    token = env("SOURCE_GIT_TOKEN")
    project = env("SOURCE_GIT_PROJECT")
    sha = env("SHA")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        die("sha must be a 40-character lowercase hex value")
    output = Path(args.output)
    url = (
        f"{api}/projects/{urllib.parse.quote(project, safe='')}"
        f"/repository/archive?sha={urllib.parse.quote(sha, safe='')}"
    )
    handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    archive = Path(handle.name)
    handle.close()
    try:
        download(url, token, archive)
        if archive.stat().st_size < MIN_ARCHIVE_BYTES:
            die("archive is too small")
        extract_zip(archive, output)
        print("ok: source extracted")
    finally:
        archive.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
