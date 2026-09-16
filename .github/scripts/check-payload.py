#!/usr/bin/env python3
import json
import os
import re
import sys


def die(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


TAG_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RID_RE = re.compile(
    r"^b-(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z$"
)


def main():
    raw = os.environ.get("PAYLOAD", "")
    try:
        payload = json.loads(raw)
    except Exception as error:
        die(f"payload is not valid JSON: {error}")
    if not isinstance(payload, dict) or set(payload.keys()) != {"tag", "sha", "rid"}:
        die("payload must contain exactly tag, sha and rid")
    tag = payload["tag"]
    sha = payload["sha"]
    rid = payload["rid"]
    if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
        die(f"tag format is not allowed: {tag!r}")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        die(f"sha format is not allowed: {sha!r}")
    if not isinstance(rid, str) or not RID_RE.fullmatch(rid):
        die(f"rid format is not allowed: {rid!r}")
    version = tag[1:]
    prefix = f"b-{version}-{sha[:12]}-"
    if not rid.startswith(prefix):
        die("request id does not match tag and sha")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"tag={tag}\n")
            handle.write(f"sha={sha}\n")
            handle.write(f"rid={rid}\n")
    print(f"tag={tag} sha={sha} rid={rid}")


if __name__ == "__main__":
    main()
