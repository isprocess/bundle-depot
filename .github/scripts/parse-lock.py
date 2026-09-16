#!/usr/bin/env python3
import argparse
import os
import re
import sys
from pathlib import Path

SCHEMA = "bundle-tool-lock.v1"
PLATFORMS = ["linux-x64", "windows-x64", "macos-x64", "macos-arm64"]
TAG_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def die(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_scalar(value):
    value = value.strip()
    quote = chr(34)
    slash = chr(92)
    if len(value) >= 2 and value[0] == quote and value[-1] == quote:
        inner = value[1:-1]
        if slash in inner or quote in inner:
            die("lock string escapes are not allowed")
        return inner
    if re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)
    die(f"lock value is not supported: {value}")


def parse_toml_tables(text):
    if text.startswith("﻿"):
        die("lock has UTF-8 BOM")
    root = {}
    assets = []
    current = root
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[assets]]":
            current = {}
            assets.append(current)
            continue
        if line.startswith("[") or "=" not in line:
            die("lock format is invalid")
        key, value = line.split("=", 1)
        key = key.strip()
        if key in current:
            die(f"lock contains duplicate key {key}")
        current[key] = parse_scalar(value.strip())
    if assets:
        root["assets"] = assets
    return root


def parse_lock(path):
    file_path = Path(path)
    if not file_path.is_file() or file_path.is_symlink():
        die("lock must be a regular file")
    root = parse_toml_tables(file_path.read_text(encoding="utf-8"))
    if root.get("schema") != SCHEMA:
        die("lock schema must be bundle-tool-lock.v1")
    mode = root.get("mode")
    if mode == "bootstrap":
        extra = [key for key in root.keys() if key not in ("schema", "mode")]
        if extra:
            die("bootstrap lock contains mixed fields")
        return {"mode": "bootstrap"}
    if mode != "release":
        die("lock mode must be bootstrap or release")
    allowed = {"schema", "mode", "project", "name", "tag", "source_sha", "assets"}
    extra = [key for key in root.keys() if key not in allowed]
    if extra:
        die("release lock contains unknown fields")
    for key in ("project", "name", "tag", "source_sha", "assets"):
        if key not in root:
            die("release lock contains mixed fields")
    project = root["project"]
    if not isinstance(project, int) or project <= 0:
        die("project must be a positive integer")
    name = root["name"]
    tag = root["tag"]
    source_sha = root["source_sha"]
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        die("lock name is not allowed")
    if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
        die("lock tag is not allowed")
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        die("lock source_sha is not allowed")
    assets = root["assets"]
    if not isinstance(assets, list) or len(assets) != 4:
        die("release lock must contain exactly four assets")
    parsed = []
    for index, item in enumerate(assets):
        if set(item.keys()) != {"platform", "name", "sha256"}:
            die("lock asset fields are wrong")
        platform = item["platform"]
        asset_name = item["name"]
        digest = item["sha256"]
        if platform != PLATFORMS[index]:
            die("lock assets must use canonical platform order")
        if not isinstance(asset_name, str) or not asset_name or "/" in asset_name or chr(92) in asset_name:
            die("lock asset name is not allowed")
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            die("lock sha256 is not allowed")
        parsed.append({"platform": platform, "name": asset_name, "sha256": digest})
    return {
        "mode": "release",
        "project": project,
        "name": name,
        "tag": tag,
        "source_sha": source_sha,
        "assets": parsed,
    }


def emit(lines):
    text = "".join(f"{line}\n" for line in lines)
    sys.stdout.write(text)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(text)


def main():
    parser = argparse.ArgumentParser(description="parse bundle-tool.lock")
    parser.add_argument("--lock-file", required=True)
    args = parser.parse_args()
    lock = parse_lock(args.lock_file)
    lines = [f"mode={lock['mode']}"]
    if lock["mode"] == "release":
        lines.extend([
            f"project={lock['project']}",
            f"name={lock['name']}",
            f"tag={lock['tag']}",
            f"source_sha={lock['source_sha']}",
        ])
        for asset in lock["assets"]:
            lines.append(f"asset_{asset['platform']}_name={asset['name']}")
            lines.append(f"asset_{asset['platform']}_sha256={asset['sha256']}")
    emit(lines)


if __name__ == "__main__":
    main()
