#!/usr/bin/env python3
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RETRY_STATUS = {429, 500, 502, 503, 504}


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
            die(f"tag lookup failed: HTTP {error.code}")
        except (TimeoutError, OSError, urllib.error.URLError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            die("tag lookup failed after retryable network errors")
        else:
            if status != 200:
                die(f"tag lookup failed: HTTP {status}")
            try:
                return json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                die("tag lookup returned invalid JSON")
    die("tag lookup failed")


def main():
    api = env("SOURCE_GIT_API").rstrip("/")
    token = env("SOURCE_GIT_TOKEN")
    project = env("SOURCE_GIT_PROJECT")
    tag = env("TAG")
    expected = env("SHA")
    if not SHA_RE.fullmatch(expected):
        die("sha must be a 40-character lowercase hex value")
    url = (
        f"{api}/projects/{urllib.parse.quote(project, safe='')}"
        f"/repository/tags/{urllib.parse.quote(tag, safe='')}"
    )
    data = http_json(url, token)
    if not isinstance(data, dict):
        die("tag lookup returned non-object JSON")
    actual = (data.get("commit") or {}).get("id")
    if actual != expected:
        die(f"tag SHA mismatch: {actual} != {expected}")
    print(f"tag {tag} points at {actual}")


if __name__ == "__main__":
    main()
