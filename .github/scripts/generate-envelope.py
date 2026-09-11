#!/usr/bin/env python3
"""
Generate the publish.json envelope.

Fields are written in a fixed order with hand-built JSON, so the output byte
order does not depend on dict iteration. Every string value must match
[A-Za-z0-9._/@:+-] and is therefore emitted without JSON escaping.
"""
import argparse
import json
import re
import sys
from pathlib import Path


ALLOWED_CHARS = re.compile(r'^[A-Za-z0-9._/@:+-]+$')


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def validate_string(value, field_name):
    if not ALLOWED_CHARS.match(value):
        die(f"{field_name} contains characters that are not allowed: {value}")


def main():
    parser = argparse.ArgumentParser(description='generate publish.json envelope')
    parser.add_argument('--schema', required=True, help='schema value, e.g. publish.v1')
    parser.add_argument('--kind', required=True, choices=['files', 'container'], help='kind value')
    parser.add_argument('--tag', required=True, help='tag value, e.g. v1.2.3')
    parser.add_argument('--sha', required=True, help='40 hex character commit SHA')
    parser.add_argument('--rid', required=True, help='request id, e.g. b-1.2.3-...')
    parser.add_argument('--manifest', required=True, help='manifest file name')
    parser.add_argument('--checksums', required=True, help='checksums file name')
    parser.add_argument('--output', required=True, help='output file path')
    parser.add_argument('--manifest-path', help='manifest file path used to cross-check fields')

    args = parser.parse_args()

    if not re.match(r'^[0-9a-f]{40}$', args.sha):
        die(f"sha format is not allowed: {args.sha}")

    if not re.match(r'^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$', args.tag):
        die(f"tag format is not allowed: {args.tag}")

    validate_string(args.schema, 'schema')
    validate_string(args.kind, 'kind')
    validate_string(args.tag, 'tag')
    validate_string(args.sha, 'sha')
    validate_string(args.rid, 'rid')
    validate_string(args.manifest, 'manifest')
    validate_string(args.checksums, 'checksums')

    if args.manifest_path:
        manifest_file = Path(args.manifest_path)
        if not manifest_file.exists():
            die(f"manifest file is missing: {args.manifest_path}")

        try:
            with open(manifest_file, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
        except json.JSONDecodeError as e:
            die(f"manifest is not valid JSON: {e}")

        required_fields = {
            'contract_version': 'release.v2',
            'kind': args.kind,
            'tag': args.tag,
            'source_commit_sha': args.sha,
            'request_id': args.rid,
        }

        for field, expected in required_fields.items():
            if field not in manifest:
                die(f"manifest is missing field: {field}")
            if manifest[field] != expected:
                die(f"manifest {field} mismatch: expected {expected}, got {manifest[field]}")

    envelope = (
        '{"schema":"' + args.schema + '",'
        '"kind":"' + args.kind + '",'
        '"tag":"' + args.tag + '",'
        '"sha":"' + args.sha + '",'
        '"rid":"' + args.rid + '",'
        '"manifest":"' + args.manifest + '",'
        '"checksums":"' + args.checksums + '"}'
    )

    try:
        parsed = json.loads(envelope)
        if len(parsed) != 7:
            die(f"generated JSON has the wrong field count: {len(parsed)}")
    except json.JSONDecodeError as e:
        die(f"generated JSON is invalid: {e}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(envelope)

    print(f"ok: wrote {args.output}")


if __name__ == '__main__':
    main()
