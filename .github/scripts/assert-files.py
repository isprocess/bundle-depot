#!/usr/bin/env python3
"""
Assert that required business files exist.

Exact paths must resolve; every glob must match at least one file under the
given root. Any missing path or unmatched pattern fails the step.
"""
import argparse
import sys
from pathlib import Path


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='assert required files exist')
    parser.add_argument('--root', required=True, help='search root')
    parser.add_argument('--files', help='comma separated exact file list')
    parser.add_argument('--globs', help='comma separated glob list')

    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        die(f"root does not exist: {args.root}")
    if not root.is_dir():
        die(f"root is not a directory: {args.root}")

    missing = []
    matched = []

    if args.files:
        for name in args.files.split(','):
            name = name.strip()
            if not name:
                continue

            path = root / name
            if path.exists():
                matched.append(str(path.relative_to(root)))
                print(f"found: {name}")
            else:
                missing.append(f"missing file: {name}")
                print(f"missing: {name}", file=sys.stderr)

    if args.globs:
        for pattern in args.globs.split(','):
            pattern = pattern.strip()
            if not pattern:
                continue

            hits = list(root.rglob(pattern))
            if hits:
                for hit in hits:
                    rel = hit.relative_to(root)
                    matched.append(str(rel))
                    print(f"matched {pattern}: {rel}")
            else:
                missing.append(f"pattern matched nothing: {pattern}")
                print(f"no match: {pattern}", file=sys.stderr)

    if missing:
        print(f"\n{len(missing)} item(s) missing:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        sys.exit(1)

    print(f"\nok: all file checks passed, {len(matched)} item(s) matched")


if __name__ == '__main__':
    main()
