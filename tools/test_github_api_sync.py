#!/usr/bin/env python3
"""Local real Git and independently reconstructed API objects, no live secret."""
import argparse
import base64
import hashlib
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import github_api_sync as api
import repository_sync as sync

ROOT = None


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / self._testMethodName
        self.root.mkdir()
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.source / "oss-alternatives/apps/opencad").mkdir(parents=True)
        (self.source / "oss-alternatives/apps/opencad/server.py").write_text("# fixture\n")
        (self.source / "LICENSE").write_text("fixture\n")
        self.git("add", "LICENSE", "oss-alternatives/apps/opencad/server.py")
        self.git("commit", "-m", "Initial")
        self.first = self.git("rev-parse", "HEAD").stdout.decode().strip()
        (self.source / "LICENSE").write_text("changed fixture\n")
        self.git("add", "LICENSE")
        self.git("commit", "-m", "Second")
        self.head = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.evidence = sync.Evidence(self.root / "evidence", "synthetic-memory")
        self.tip, self.calls = self.first, []
        self.roles = []

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.source), *args], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def request(self, evidence, method, suffix, payload=None):
        self.calls.append((method, suffix, payload))
        if method == "GET" and suffix == "/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": self.tip}}
        if method == "GET":
            return {"sha": suffix.removeprefix("/commits/")}
        if suffix == "/blobs":
            raw = base64.b64decode(payload["content"], validate=True)
            sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            return {"sha": sha}
        if suffix == "/trees":
            # A separate Git index reconstructs the requested tree, not the oracle.
            env = {"GIT_INDEX_FILE": str(self.root / "api-index")}
            import os
            env = {**os.environ, **env}
            self.checked(["read-tree", payload["base_tree"]], env)
            for entry in payload["tree"]:
                self.checked(["update-index", "--add", "--cacheinfo",
                              entry["mode"], entry["sha"], entry["path"]], env)
            result = self.checked(["write-tree"], env)
            return {"sha": result.stdout.decode().strip()}
        if suffix == "/commits":
            from datetime import datetime
            def header(key):
                record = payload[key]
                date = datetime.fromisoformat(record["date"])
                return (key + " " + record["name"] + " <" + record["email"] + "> "
                        + str(int(date.timestamp())) + " " + date.strftime("%z"))
            raw = ("tree " + payload["tree"] + "\nparent " + payload["parents"][0]
                   + "\n" + header("author") + "\n" + header("committer") + "\n\n"
                   + payload["message"]).encode()
            return {"sha": hashlib.sha1(b"commit " + str(len(raw)).encode()
                                       + b"\0" + raw).hexdigest()}
        self.assertEqual(payload["force"], False)
        self.tip = payload["sha"]
        return {"ref": "refs/heads/main", "object": {"sha": self.tip}}

    def checked(self, args, env):
        result = subprocess.run(["git", "-C", str(self.source), *args],
                                env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def synchronize(self, evidence, source, commit, targets, mutate=False):
        self.assertEqual(targets, ["gitlab", "gitee"])
        if mutate:
            self.roles.extend(targets)
        return {"verdict": "PASS"}

    def run_api(self, mutate=True, request=None):
        with patch.object(api, "request", side_effect=request or self.request), \
             patch.object(sync, "github_repo", return_value="PRESENT_PUBLIC_WRITABLE"), \
             patch.object(sync, "load_token", return_value="synthetic-memory"), \
             patch.object(sync, "synchronize", side_effect=self.synchronize), \
             patch.object(sync, "heads", return_value={"refs/heads/main": self.head}):
            return api.run(self.evidence, self.source, self.head, mutate)

    def test_exact_objects_and_primary_order(self):
        result = self.run_api()
        self.assertTrue(result["all_three_synced"])
        self.assertEqual(self.tip, self.head)
        self.assertEqual(self.roles, ["gitlab", "gitee"])
        updates = [row for row in self.calls if row[0] == "PATCH"]
        self.assertEqual(updates, [("PATCH", "/refs/heads/main",
                                  {"sha": self.head, "force": False})])

    def test_plan_never_writes(self):
        result = self.run_api(False)
        self.assertEqual(result["state"], "PLANNED")
        self.assertFalse(result["all_three_synced"])
        self.assertEqual(self.roles, [])
        self.assertTrue(all(row[0] == "GET" for row in self.calls))

    def test_object_mismatch_never_updates_ref(self):
        def mismatch(evidence, method, suffix, payload=None):
            result = self.request(evidence, method, suffix, payload)
            if suffix == "/blobs":
                result["sha"] = "0" * 40
            return result
        with self.assertRaisesRegex(sync.Refused, "OBJECT_SHA_MISMATCH_blob"):
            self.run_api(request=mismatch)
        self.assertEqual(self.tip, self.first)
        self.assertFalse(any(row[0] == "PATCH" for row in self.calls))

    def test_deleted_file_rejected_before_primary(self):
        (self.source / "ordinary.txt").write_text("ordinary fixture\n")
        self.git("add", "ordinary.txt")
        self.git("commit", "-m", "Added ordinary file")
        self.git("rm", "ordinary.txt")
        self.git("commit", "-m", "Deleted")
        self.head = self.git("rev-parse", "HEAD").stdout.decode().strip()
        with self.assertRaisesRegex(sync.Refused, "DELETION_OR_AMBIGUOUS_ENTRY_REFUSED"):
            self.run_api()
        self.assertEqual(self.roles, [])

    def test_dirty_source_refused(self):
        (self.source / "untracked").write_text("dirty\n")
        with self.assertRaisesRegex(sync.Refused, "SOURCE_DIRTY"):
            self.run_api()
        self.assertEqual(self.calls, [])

    def test_remote_drift_refuses_update(self):
        reads = 0
        def drifting(evidence, method, suffix, payload=None):
            nonlocal reads
            if suffix == "/ref/heads/main":
                reads += 1
                if reads == 3:
                    self.tip = "0" * 40
            return self.request(evidence, method, suffix, payload)
        with self.assertRaisesRegex(sync.Refused, "GITHUB_REMOTE_DRIFT"):
            self.run_api(request=drifting)
        self.assertFalse(any(row[0] == "PATCH" for row in self.calls))

    def test_merge_headers_refused(self):
        raw = self.git("cat-file", "commit", self.head).stdout
        raw = raw.replace(b"\nauthor ", b"\nparent " + self.first.encode() + b"\nauthor ", 1)
        result = subprocess.run(["git", "-C", str(self.source), "hash-object", "-t",
                                 "commit", "-w", "--stdin"], input=raw, capture_output=True)
        self.assertEqual(result.returncode, 0)
        sha = result.stdout.decode().strip()
        with self.assertRaisesRegex(sync.Refused, "SIGNED_EXTENDED_OR_MERGE_REFUSED"):
            api.local_commit(self.evidence, self.source, sha)

    def test_primary_failure_stops_api_objects(self):
        with patch.object(api, "request", side_effect=self.request), \
             patch.object(sync, "github_repo", return_value="PRESENT_PUBLIC_WRITABLE"), \
             patch.object(sync, "load_token", return_value="synthetic-memory"), \
             patch.object(sync, "synchronize", side_effect=[{}, sync.Refused("PRIMARY_FAILED")]):
            with self.assertRaisesRegex(sync.Refused, "PRIMARY_FAILED"):
                api.run(self.evidence, self.source, self.head, True)
        self.assertTrue(all(row[0] == "GET" for row in self.calls))

    def test_scope_and_force_rejected_before_request(self):
        with self.assertRaisesRegex(sync.Refused, "API_OBJECT_SCOPE"):
            api.request(self.evidence, "POST", "/other-repository")
        with self.assertRaisesRegex(sync.Refused, "NON_FORCE_MAIN_REQUIRED"):
            api.request(self.evidence, "PATCH", "/refs/heads/main",
                        {"sha": self.head, "force": True})
        self.assertEqual(self.evidence.calls, [])


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sync.Evidence(args.out)
    ROOT = args.out
    before = (api.own_sha(), sync.tool_sha(), sync.digest(Path(__file__).read_bytes()))
    with (ROOT / "tests.log").open("x") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ApiTests))
    after = (api.own_sha(), sync.tool_sha(), sync.digest(Path(__file__).read_bytes()))
    passed = result.wasSuccessful() and before == after and result.testsRun > 0
    report = {"verdict": "PASS" if passed else "FAIL", "at": sync.stamp(),
              "tests": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skips": len(result.skipped),
              "source_stable": before == after,
              "scope": "LOCAL_GIT_INDEPENDENT_OBJECT_RECONSTRUCTION_NO_LIVE_SECRET"}
    (ROOT / "result.json").write_bytes(sync.encode(report))
    print(sync.encode(report).decode(), end="", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
