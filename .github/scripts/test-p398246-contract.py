#!/usr/bin/env python3
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "p398246.yml"
TARGETS = ["linux-x64", "windows-x64", "macos-x64", "macos-arm64"]
RUNNERS = ["ubuntu-22.04", "windows-2022", "macos-15-intel", "macos-14"]
JOBS = ["validate", "build", "prepare-bundle", "publish"]
REQUIRED_FILES = [
    "Cargo.toml",
    "rust-toolchain.toml",
    "release.toml",
    "release.py",
    "docs/usage.md",
    "bundle-tool.lock",
    "src/main.rs",
]


def extract_jobs(text):
    lines = text.splitlines()
    jobs = {}
    current = None
    body = []
    for line in lines:
        match = re.match(r"^  ([a-z][a-z0-9-]*)\s*:\s*$", line)
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


class P398246ContractTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.jobs = extract_jobs(self.text)

    def test_event_permissions_concurrency(self):
        self.assertIn("types: [build_398246]", self.text)
        self.assertRegex(self.text, r"(?m)^permissions: \{\}\s*$")
        self.assertRegex(self.text, r"(?m)^  group: p398246\s*$")
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertNotIn("github.event.client_payload.tag", self.text)

    def test_job_order_without_fetch(self):
        for name in JOBS:
            self.assertIn(name, self.jobs)
        self.assertNotIn("fetch", self.jobs)
        self.assertIn("needs: validate", self.jobs["build"])
        self.assertIn("needs: [validate, build]", self.jobs["prepare-bundle"])
        self.assertIn("needs: [validate, prepare-bundle]", self.jobs["publish"])
        self.assertNotIn("src-", self.text)
        self.assertNotIn("secrets: inherit", self.text)

    def test_matrix_four_native_runners(self):
        for target, runner in zip(TARGETS, RUNNERS):
            self.assertIn("target: " + target, self.jobs["build"])
            self.assertIn(runner, self.jobs["build"])

    def test_build_verifies_fetches_and_parses_lock(self):
        build = self.jobs["build"]
        self.assertIn("verify-tag.py", build)
        self.assertIn("fetch-source.py", build)
        self.assertIn("assert-files.py", build)
        self.assertIn("parse-lock.py", build)
        self.assertNotIn("check-bootstrap-window.py", self.text)
        self.assertIn("bootstrap mode is not allowed", build)
        self.assertIn("download-executor.py", build)
        self.assertIn("--platform ${{ matrix.target }}", build)
        self.assertIn("name: pkg-${{ matrix.target }}", build)
        self.assertIn("retention-days: 1", build)
        self.assertIn("if-no-files-found: error", build)

    def test_prepare_bundle_uses_repo_file_and_assemble_shape(self):
        prepare = self.jobs["prepare-bundle"]
        self.assertIn("read-repo-file.py", prepare)
        self.assertIn("download-executor.py", prepare)
        self.assertNotIn("packages/pkg-linux-x64/assets/bundle-tool-linux-x64", prepare)
        self.assertIn("bootstrap mode is not allowed", prepare)
        self.assertIn("bundle-tool assemble", prepare)
        self.assertNotIn("--usage-dir", self.text)
        assemble_lines = [line for line in prepare.splitlines() if "assemble" in line]
        self.assertTrue(any("bundle-tool assemble" in line for line in assemble_lines))
        self.assertFalse(any("--name" in line for line in assemble_lines))
        self.assertIn("name: release-bundle", prepare)
        self.assertIn("retention-days: 1", prepare)

    def test_publish_secret_mapping(self):
        publish = self.jobs["publish"]
        self.assertIn("api: ${{ secrets.SOURCE_GIT_API }}", publish)
        self.assertIn("project: ${{ secrets.SOURCE_GIT_PROJECT_398246 }}", publish)
        self.assertIn("token: ${{ secrets.SOURCE_GIT_TOKEN }}", publish)
        self.assertNotIn("secrets: inherit", publish)

    def test_assert_files_list(self):
        self.assertIn("--files " + ",".join(REQUIRED_FILES), self.text)

    def test_actions_pinned(self):
        pairs = [
            ("actions/checkout", "11bd71901bbe5b1630ceea73d27597364c9af683"),
            ("actions/upload-artifact", "50769540e7f4bd5e21e526ee35c689e35e0d6874"),
            ("actions/download-artifact", "fa0a91b85d4f404e444e00e005971372dc801d16"),
        ]
        for action, sha in pairs:
            self.assertIn(action + "@" + sha, self.text)

    def test_static_security_and_token_scope(self):
        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", self.text))
        for forbidden in ("set -x", "printenv", "GITHUB_TOKEN", "secrets: inherit"):
            self.assertNotIn(forbidden, self.text)
        plugin = None
        for chunk in self.jobs["build"].split("- name:"):
            if "--platform ${{ matrix.target }}" in chunk and "assemble" not in chunk:
                plugin = chunk
                break
        self.assertIsNotNone(plugin)
        self.assertNotIn("SOURCE_GIT_", plugin)

    def test_token_only_in_fetch_steps(self):
        self.assertNotIn("SOURCE_GIT_TOKEN", self.jobs["validate"])
        self.assertIn("token: ${{ secrets.SOURCE_GIT_TOKEN }}", self.jobs["publish"])
        self.assertNotIn("SOURCE_GIT_TOKEN:", self.jobs["publish"])
        self.assertIn("SOURCE_GIT_TOKEN", self.jobs["build"])
        self.assertIn("SOURCE_GIT_TOKEN", self.jobs["prepare-bundle"])


    def test_bootstrap_is_never_allowed(self):
        self.assertNotIn("check-bootstrap-window.py", self.text)
        self.assertIn("bootstrap mode is not allowed", self.text)
        self.assertNotIn("steps.lock.outputs.mode == 'bootstrap'", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
