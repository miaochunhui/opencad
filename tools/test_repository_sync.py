#!/usr/bin/env python3
"""Real local Git acceptance and synthetic API negatives; no live secrets."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).with_name("repository_sync.py")
spec = importlib.util.spec_from_file_location("repository_sync", TOOL)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
ROOT = None


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / self._testMethodName
        self.root.mkdir()
        self.source = self.root / "source"
        self.source.mkdir()
        self.git(self.source, "init")
        self.git(self.source, "config", "user.name", "Fixture")
        self.git(self.source, "config", "user.email", "fixture@example.invalid")
        (self.source / "oss-alternatives/apps/opencad").mkdir(parents=True)
        (self.source / "oss-alternatives/apps/opencad/server.py").write_text("# fixture\n")
        (self.source / "LICENSE").write_text("fixture license\n")
        self.git(self.source, "add", "LICENSE", "oss-alternatives/apps/opencad/server.py")
        self.git(self.source, "commit", "-m", "Initial")
        self.first = self.git(self.source, "rev-parse", "HEAD").stdout.strip().decode()
        self.remotes = {}
        for role in api.TARGETS:
            remote = self.root / (role + ".git")
            self.git(self.root, "init", "--bare", str(remote))
            self.remotes[role] = str(remote)

    def git(self, root, *args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def sync(self, name, commit=None, roles=None):
        roles = roles or list(api.TARGETS)
        argv = [sys.executable, "-I", "-B", str(TOOL), "--action", "sync",
                "--source", str(self.source), "--commit", commit or self.first,
                "--out", str(self.root / name)]
        for role in roles:
            argv += ["--target", role, "--local-bare", role + "=" + self.remotes[role]]
        result = subprocess.run(argv, capture_output=True)
        (self.root / (name + ".launcher.json")).write_bytes(api.encode({
            "actual_rc": result.returncode, "stdout": result.stdout.decode(),
            "stderr": result.stderr.decode()}))
        return result

    def tip(self, role):
        return self.git(Path(self.remotes[role]), "rev-parse", "refs/heads/main").stdout.strip().decode()

    def test_three_targets_primary_first_and_fast_forward(self):
        config = (self.source / ".git/config").read_bytes()
        result = self.sync("first", roles=["github", "gitee", "gitlab"])
        self.assertEqual(result.returncode, 0, result.stdout.decode())
        report = json.loads((self.root / "first/result.json").read_bytes())
        self.assertEqual([item["role"] for item in report["targets"]], list(api.TARGETS))
        self.assertTrue(report["all_three_synced"])
        for role in api.TARGETS:
            self.assertEqual(self.tip(role), self.first)
        (self.source / "LICENSE").write_text("updated fixture\n")
        self.git(self.source, "add", "LICENSE")
        self.git(self.source, "commit", "-m", "Second")
        second = self.git(self.source, "rev-parse", "HEAD").stdout.strip().decode()
        self.assertEqual(self.sync("second", second).returncode, 0)
        for role in api.TARGETS:
            self.assertEqual(self.tip(role), second)
        self.assertEqual(config, (self.source / ".git/config").read_bytes())

    def test_divergence_blocks_all_targets(self):
        self.git(self.source, "push", self.remotes["gitee"], self.first + ":refs/heads/main")
        (self.source / "LICENSE").write_text("alien\n")
        self.git(self.source, "add", "LICENSE")
        self.git(self.source, "commit", "-m", "Alien")
        self.git(self.source, "push", self.remotes["gitee"], "HEAD:refs/heads/main")
        result = self.sync("divergence", self.first)
        self.assertEqual(result.returncode, 1)
        # Wrong source HEAD is rejected before any publication.
        self.assertIn(b"HEAD_NOT_PINNED", result.stdout)
        self.assertEqual(self.git(Path(self.remotes["gitlab"]), "for-each-ref").stdout, b"")
        current = self.git(self.source, "rev-parse", "HEAD").stdout.strip().decode()
        self.git(self.source, "push", self.remotes["gitlab"], current + ":refs/heads/main")
        with patch.object(api, "snapshot", return_value=(self.first, b"config")):
            evidence = api.Evidence(self.root / "ancestry")
            with self.assertRaisesRegex(api.Refused, "REMOTE_DIVERGED"):
                api.synchronize(evidence, self.source, self.first, list(api.TARGETS),
                                mutate=True, local=self.remotes)
        self.assertEqual(self.git(Path(self.remotes["github"]), "for-each-ref").stdout, b"")

    def test_dirty_source_and_removed_secret_fail_closed(self):
        (self.source / "untracked").write_text("dirty\n")
        result = self.sync("dirty")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"SOURCE_DIRTY", result.stdout)
        token = "ghp_" + "X" * 36
        (self.source / "untracked").write_text(token)
        self.git(self.source, "add", "untracked")
        self.git(self.source, "commit", "-m", "Synthetic secret")
        self.git(self.source, "rm", "untracked")
        self.git(self.source, "commit", "-m", "Removed")
        current = self.git(self.source, "rev-parse", "HEAD").stdout.strip().decode()
        result = self.sync("history", current)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"HISTORY_SECRET_FOUND", result.stdout)
        self.assertNotIn(token.encode(), result.stdout + result.stderr)
        for role in api.TARGETS:
            self.assertEqual(self.git(Path(self.remotes[role]), "for-each-ref").stdout, b"")

    def test_existing_master_preserved(self):
        self.git(self.source, "push", self.remotes["gitee"], self.first + ":refs/heads/master")
        self.assertEqual(self.sync("preserve").returncode, 0)
        master = self.git(Path(self.remotes["gitee"]), "rev-parse", "refs/heads/master").stdout
        self.assertEqual(master.strip().decode(), self.first)

    def test_rejected_primary_push_stops_mirrors(self):
        hook = Path(self.remotes["gitlab"]) / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o700)
        result = self.sync("reject")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"PUSH_RC_1", result.stdout)
        partial = json.loads((self.root / "reject/partial.json").read_bytes())
        self.assertTrue(partial[0]["push_attempted"])
        self.assertFalse(partial[1]["push_attempted"])
        for role in api.TARGETS:
            self.assertEqual(self.git(Path(self.remotes[role]), "for-each-ref").stdout, b"")

    def test_github_identity_create_visibility_permission(self):
        evidence = api.Evidence(self.root / "api", "synthetic-memory")
        calls = []
        def response(method, path, token, payload=None):
            calls.append((method, path, payload))
            if path == "/user":
                return 200, {"login": "MiaoChunHui"}
            if len(calls) == 2:
                return 404, {}
            return (201 if method == "POST" else 200), {
                "full_name": "miaochunhui/opencad", "private": False,
                "archived": False, "clone_url": api.TARGETS["github"],
                "permissions": {"push": True}}
        with patch.object(api, "api", side_effect=response):
            self.assertEqual(api.github_repo(evidence, True), "PRESENT_PUBLIC_WRITABLE")
        self.assertEqual(calls[2], ("POST", "/user/repos",
                                   {"name": "opencad", "private": False, "auto_init": False}))
        with patch.object(api, "api", return_value=(200, {"login": "OtherAccount"})):
            with self.assertRaisesRegex(api.Refused, "GITHUB_ACCOUNT_MISMATCH"):
                api.github_repo(evidence, True)

    def test_token_and_wrong_helper_target(self):
        token = "ghp_" + "T" * 36
        self.assertEqual(api.parse_token(("PAT=" + token).encode()), token)
        with self.assertRaisesRegex(api.Refused, "TOKEN_AMBIGUOUS"):
            api.parse_token((token + "\n" + token).encode())
        argv = [sys.executable, "-I", "-B", str(TOOL), "--credential-helper", "get"]
        env = os.environ.copy()
        env["OPENCAD_SYNC_TOOL_SHA"] = api.tool_sha()
        result = subprocess.run(argv, env=env, input=b"protocol=https\nhost=evil.invalid\n"
                                b"path=miaochunhui/opencad.git\n\n", capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"CREDENTIAL_HELPER_REFUSED\n")

    def test_password_prompt_not_secret_literal_but_real_literal_rejected(self):
        prompt = b'first = getpass.getpass("Password' + b': ")\nraise AuthError("mismatch")\n'
        self.assertIsNone(api.SECRET.search(prompt))
        literal = b'password' + b' = "' + b'Z' * 16 + b'"\n'
        self.assertIsNotNone(api.SECRET.search(literal))

    def test_follow_tags_configuration_does_not_publish_unscanned_tags(self):
        self.git(self.source, "config", "push.followTags", "true")
        secret = "ghp_" + "Q" * 36
        self.git(self.source, "tag", "-a", "private-tag", "-m", secret)
        result = self.sync("tags")
        self.assertEqual(result.returncode, 0, result.stdout.decode())
        for role in api.TARGETS:
            self.assertEqual(self.git(Path(self.remotes[role]), "for-each-ref",
                                      "refs/tags").stdout, b"")

    def test_replace_refs_refuse_and_original_object_is_scanned(self):
        secret = "ghp_" + "R" * 36
        (self.source / "secret").write_text(secret)
        self.git(self.source, "add", "secret")
        self.git(self.source, "commit", "-m", "Synthetic original")
        commit = self.git(self.source, "rev-parse", "HEAD").stdout.strip().decode()
        oid = self.git(self.source, "rev-parse", "HEAD:secret").stdout.strip().decode()
        clean = self.git(self.source, "rev-parse", "HEAD:LICENSE").stdout.strip().decode()
        self.git(self.source, "replace", oid, clean)
        result = self.sync("replace", commit)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"REPLACE_REFS_REFUSED", result.stdout)
        evidence = api.Evidence(self.root / "original-scan")
        with self.assertRaisesRegex(api.Refused, "HISTORY_SECRET_FOUND"):
            api.scan_history(evidence, self.source, commit)
        for role in api.TARGETS:
            self.assertEqual(self.git(Path(self.remotes[role]), "for-each-ref").stdout, b"")

    def test_mirror_only_refuses(self):
        result = self.sync("mirror-only", roles=["gitee", "github"])
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"PRIMARY_REQUIRED", result.stdout)

    def test_source_pre_push_hook_not_bypassed(self):
        hook = self.source / ".git/hooks/pre-push"
        hook.write_text("#!/bin/sh\nexit 23\n")
        hook.chmod(0o700)
        result = self.sync("source-hook")
        self.assertEqual(result.returncode, 1)
        partial = json.loads((self.root / "source-hook/partial.json").read_bytes())
        self.assertNotEqual(partial[0]["actual_push_rc"], 0)
        self.assertFalse(partial[1]["push_attempted"])
        self.assertEqual(self.git(Path(self.remotes["gitlab"]), "for-each-ref").stdout, b"")

    def test_post_push_output_refusal_preserves_partial_and_readback(self):
        evidence = api.Evidence(self.root / "post-output")
        original = evidence.run
        def refusing(argv, role=None, check=True):
            child = original(argv, role, check)
            if "push" in argv:
                raise api.Refused("SYNTHETIC_OUTPUT_REFUSAL")
            return child
        with patch.object(evidence, "run", side_effect=refusing):
            with self.assertRaisesRegex(api.Refused, "SYNTHETIC_OUTPUT_REFUSAL"):
                api.synchronize(evidence, self.source, self.first, list(api.TARGETS),
                                mutate=True, local=self.remotes)
        partial = json.loads((evidence.out / "partial.json").read_bytes())
        self.assertEqual(partial[0]["actual_push_rc"], 0)
        self.assertEqual(partial[0]["readback"], self.first)
        self.assertFalse(partial[1]["push_attempted"])

    def test_final_remote_drift_cannot_claim_all_three(self):
        evidence = api.Evidence(self.root / "final-drift")
        original = api.heads
        counts = {}
        def drifting(evidence, source, role, url, allow_create=False):
            counts[role] = counts.get(role, 0) + 1
            result = original(evidence, source, role, url, allow_create)
            if role == "gitlab" and counts[role] == 4:
                return {"refs/heads/main": "0" * 40}
            return result
        with patch.object(api, "heads", side_effect=drifting):
            with self.assertRaisesRegex(api.Refused, "FINAL_REMOTE_DRIFT"):
                api.synchronize(evidence, self.source, self.first, list(api.TARGETS),
                                mutate=True, local=self.remotes)
        self.assertFalse((evidence.out / "result.json").exists())

    def test_second_target_launch_failure_does_not_borrow_primary_rc(self):
        evidence = api.Evidence(self.root / "second-launch")
        original = evidence.run
        def fail_launch(argv, role=None, check=True):
            if "push" in argv and role == "gitee":
                raise OSError("synthetic launch failure")
            return original(argv, role, check)
        with patch.object(evidence, "run", side_effect=fail_launch):
            with self.assertRaisesRegex(api.Refused, "OSError"):
                api.synchronize(evidence, self.source, self.first, list(api.TARGETS),
                                mutate=True, local=self.remotes)
        partial = json.loads((evidence.out / "partial.json").read_bytes())
        self.assertEqual(partial[0]["actual_push_rc"], 0)
        self.assertIsNone(partial[1]["actual_push_rc"])
        self.assertFalse(partial[2]["push_attempted"])
        self.assertEqual(self.git(Path(self.remotes["gitee"]), "for-each-ref").stdout, b"")


def main():
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    api.Evidence(args.out)
    ROOT = args.out
    before = (api.tool_sha(), api.digest(Path(__file__).read_bytes()))
    with (ROOT / "tests.log").open("w") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(SyncTests))
    after = (api.tool_sha(), api.digest(Path(__file__).read_bytes()))
    passed = result.wasSuccessful() and before == after
    report = {"verdict": "PASS" if passed else "FAIL", "at": api.stamp(),
              "tests": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skips": len(result.skipped),
              "source_stable": before == after,
              "scope": "LOCAL_GIT_AND_SYNTHETIC_API_NO_LIVE_SECRETS"}
    (ROOT / "result.json").write_bytes(api.encode(report))
    print(api.encode(report).decode(), end="", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
