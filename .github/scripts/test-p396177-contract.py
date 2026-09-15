#!/usr/bin/env python3
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "p396177.yml"
TARGETS = ["linux-x64", "windows-x64", "macos-x64", "macos-arm64"]
JOBS = ["validate", "fetch", "build", "prepare-bundle", "publish"]
REQUIRED_FILES = [
    "README.md",
    "AGENTS.md",
    "Cargo.toml",
    "rust-toolchain.toml",
    "docs/git-tool.md",
    "docs/bundle-tool.md",
    "tools/git-tool/release.toml",
    "tools/git-tool/release.py",
    "tools/bundle-tool/release.toml",
    "tools/bundle-tool/release.py",
]
REQUIRED_GLOBS = ["git-tool/Cargo.toml", "bundle-tool/Cargo.toml"]


def extract_jobs(text: str) -> dict:
    lines = text.splitlines()
    jobs = {}
    current = None
    body = []
    for line in lines:
        match = re.match(r"^  ([a-z][a-z0-9-]*):\s*$", line)
        if match:
            if current:
                jobs[current] = "\n".join(body)
            current = match.group(1)
            body = []
            continue
        if current:
            if line and not line.startswith("  "):
                jobs[current] = "\n".join(body)
                current = None
                body = []
            else:
                body.append(line)
    if current:
        jobs[current] = "\n".join(body)
    return jobs


class P396177ContractTest(unittest.TestCase):
    def test_jobs_and_dependencies(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        jobs = extract_jobs(text)
        for name in JOBS:
            self.assertIn(name, jobs)
        self.assertIn("needs: validate", jobs["fetch"])
        self.assertIn("needs: validate", jobs["build"])
        self.assertIn("needs: [validate, build]", jobs["prepare-bundle"])
        self.assertIn("needs: [validate, prepare-bundle]", jobs["publish"])

    def test_matrix_has_four_platforms(self) -> None:
        jobs = extract_jobs(WORKFLOW.read_text(encoding="utf-8"))
        for target in TARGETS:
            self.assertIn(f"target: {target}", jobs["build"])

    def test_token_only_in_fetch_steps(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        jobs = extract_jobs(text)
        for name in ("validate", "build", "prepare-bundle"):
            self.assertNotIn("SOURCE_GIT_TOKEN", jobs[name])
        self.assertIn("SOURCE_GIT_TOKEN", jobs["fetch"])

    def test_artifacts_retention(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count("retention-days: 1"), 3)

    def test_bundle_tool_invocations(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count(chr(34) + "$BT" + chr(34) + " run"), 2)
        self.assertEqual(text.count("bundle-tool assemble"), 1)
        self.assertIn("--source tools/git-tool", text)
        self.assertIn("--source tools/bundle-tool", text)

    def test_publish_secrets_mapping(self) -> None:
        jobs = extract_jobs(WORKFLOW.read_text(encoding="utf-8"))
        self.assertIn("api: ${{ secrets.SOURCE_GIT_API }}", jobs["publish"])
        self.assertIn("project: ${{ secrets.SOURCE_GIT_PROJECT_396177 }}", jobs["publish"])
        self.assertIn("token: ${{ secrets.SOURCE_GIT_TOKEN }}", jobs["publish"])

    def test_static_security(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for forbidden in (
            "secrets: inherit",
            "printenv",
            "set -x",
            "github-api",
            "release.v2",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", text))

    def test_assert_files_list(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        files_arg = "--files " + ",".join(REQUIRED_FILES)
        globs_arg = "--globs " + ",".join(REQUIRED_GLOBS)
        self.assertIn(files_arg, text)
        self.assertIn(globs_arg, text)

    def test_actions_pinned(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for action, sha in (
            ("actions/checkout", "11bd71901bbe5b1630ceea73d27597364c9af683"),
            ("actions/upload-artifact", "50769540e7f4bd5e21e526ee35c689e35e0d6874"),
            ("actions/download-artifact", "fa0a91b85d4f404e444e00e005971372dc801d16"),
        ):
            self.assertIn(f"{action}@{sha}", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
