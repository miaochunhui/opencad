#!/usr/bin/env python3
"""Local real Git and independently reconstructed API objects, no live secret."""
import argparse
import base64
import hashlib
import json
import os
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
        self.git("config", "core.filemode", "false")
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
        self.rebuilt_trees = []

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
            env = {**os.environ, "GIT_INDEX_FILE": str(self.root / "api-index")}
            self.checked(["read-tree", payload["base_tree"]], env)
            for entry in payload["tree"]:
                if entry["sha"] is None:
                    prior = self.checked(["ls-files", "--stage", "--", entry["path"]],
                                         env).stdout.decode().splitlines()
                    self.assertEqual(len(prior), 1, "deletion must exist in base index")
                    self.assertEqual(prior[0].split()[0], entry["mode"])
                    self.assertEqual(entry["type"], "blob")
                    self.checked(["update-index", "--force-remove", "--", entry["path"]],
                                 env)
                else:
                    self.checked(["update-index", "--add", "--cacheinfo",
                                  entry["mode"], entry["sha"], entry["path"]], env)
            result = self.checked(["write-tree"], env)
            sha = result.stdout.decode().strip()
            self.rebuilt_trees.append({"base_tree": payload["base_tree"],
                                       "entries": payload["tree"], "actual_tree": sha})
            self.evidence.save("reconstructed-trees.json", self.rebuilt_trees)
            return {"sha": sha}
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

    def commit(self, message):
        self.git("commit", "-m", message)
        self.head = self.git("rev-parse", "HEAD").stdout.decode().strip()

    def deletion_fixture(self, path="ordinary.txt", executable=False, replacement=None):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ordinary fixture\n")
        self.git("add", "--", path)
        if executable:
            self.git("update-index", "--chmod=+x", "--", path)
        self.commit("Added ordinary file")
        self.tip = self.head
        self.git("rm", "--", path)
        if replacement:
            target = self.source / replacement
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("new fixture\n")
            self.git("add", "--", replacement)
        self.commit("Deleted ordinary file")

    def assert_deletion(self, path, mode="100644"):
        result = self.run_api()
        self.assertEqual(result["state"], "SYNCED")
        self.assertEqual(self.tip, self.head)
        trees = [payload for method, suffix, payload in self.calls if suffix == "/trees"]
        deleted = [entry for payload in trees for entry in payload["tree"]
                   if entry["sha"] is None]
        self.assertEqual(deleted, [{"path": path, "mode": mode, "type": "blob", "sha": None}])
        env = {**os.environ, "GIT_INDEX_FILE": str(self.root / "api-index")}
        actual = self.checked(["ls-files", "-z"], env).stdout.split(b"\0")
        self.assertNotIn(path.encode(), actual)
        self.assertIn(b"LICENSE", actual)
        self.assertIn(b"oss-alternatives/apps/opencad/server.py", actual)
        self.assertEqual(self.checked(["write-tree"], env).stdout,
                         self.git("rev-parse", self.head + "^{tree}").stdout)

    def test_delete_only_keeps_other_files(self):
        self.deletion_fixture()
        self.assert_deletion("ordinary.txt")
        self.assertFalse(any(suffix == "/blobs" for method, suffix, payload in self.calls))

    def test_nested_deletion(self):
        self.deletion_fixture("nested/deeper/old.txt")
        self.assert_deletion("nested/deeper/old.txt")

    def test_executable_deletion(self):
        self.deletion_fixture("nested/old-script", executable=True)
        self.assert_deletion("nested/old-script", "100755")

    def test_add_and_delete_mixed_tree(self):
        self.deletion_fixture("nested/old.txt")
        (self.source / "nested").mkdir(parents=True, exist_ok=True)
        (self.source / "nested/new.txt").write_text("new fixture\n")
        self.git("add", "--", "nested/new.txt")
        self.commit("Added replacement file")
        self.assert_deletion("nested/old.txt")
        env = {**os.environ, "GIT_INDEX_FILE": str(self.root / "api-index")}
        self.assertIn(b"nested/new.txt", self.checked(["ls-files", "-z"], env).stdout.split(b"\0"))

    def test_add_and_delete_in_one_commit(self):
        self.deletion_fixture(replacement="new.txt")
        self.assert_deletion("ordinary.txt")
        trees = [payload for method, suffix, payload in self.calls if suffix == "/trees"]
        self.assertEqual(len(trees), 1)
        self.assertEqual({entry["path"] for entry in trees[0]["tree"]},
                         {"ordinary.txt", "new.txt"})

    def test_deletion_plan_get_only(self):
        self.deletion_fixture()
        result = self.run_api(False)
        self.assertEqual(result["state"], "PLANNED")
        self.assertFalse(result["all_three_synced"])
        self.assertEqual(self.roles, [])
        self.assertTrue(self.calls and all(row[0] == "GET" for row in self.calls))

    def test_deletion_tree_mismatch_never_patches(self):
        self.deletion_fixture()
        def mismatch(evidence, method, suffix, payload=None):
            result = self.request(evidence, method, suffix, payload)
            if suffix == "/trees":
                result["sha"] = "0" * 40
            return result
        old = self.tip
        with self.assertRaisesRegex(sync.Refused, "OBJECT_SHA_MISMATCH_tree"):
            self.run_api(request=mismatch)
        self.assertEqual(self.tip, old)
        self.assertFalse(any(row[0] == "PATCH" for row in self.calls))
        self.assertFalse(any(row[1] == "/commits" for row in self.calls))

    def test_deleted_historical_secret_still_refused(self):
        (self.source / "ordinary.txt").write_text("password='" + "synthetic-only-secret" + "'\n")
        self.git("add", "--", "ordinary.txt")
        self.commit("Historical unsafe fixture")
        self.git("rm", "--", "ordinary.txt")
        self.commit("Deleted unsafe fixture")
        with self.assertRaisesRegex(sync.Refused, "HISTORY_SECRET_FOUND"):
            self.run_api()
        self.assertEqual(self.roles, [])
        self.assertEqual(self.calls, [])
        hit = json.loads((self.evidence.out / "history-hit.json").read_text())
        self.assertEqual(hit["type"], "blob")
        self.assertEqual(hit["line"], 1)

    def test_required_license_deletion_refused(self):
        self.git("rm", "--", "LICENSE")
        self.commit("Deleted required license")
        with self.assertRaisesRegex(sync.Refused, "OPENCAD_SOURCE_REQUIRED"):
            self.run_api()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.roles, [])

    def test_required_server_deletion_refused(self):
        self.git("rm", "--", "oss-alternatives/apps/opencad/server.py")
        self.commit("Deleted required server")
        with self.assertRaisesRegex(sync.Refused, "OPENCAD_SOURCE_REQUIRED"):
            self.run_api()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.roles, [])

    def test_symlink_deletion_refused(self):
        (self.source / "ordinary-link").symlink_to("LICENSE")
        self.git("add", "--", "ordinary-link")
        self.commit("Added symlink")
        self.tip = self.head
        self.git("rm", "--", "ordinary-link")
        self.commit("Deleted symlink")
        with self.assertRaisesRegex(sync.Refused, "NONREGULAR_ENTRY_REFUSED"):
            self.run_api()
        self.assertEqual(self.roles, [])
        self.assertTrue(all(row[0] == "GET" for row in self.calls))

    def test_gitlink_deletion_refused(self):
        (self.source / "submodule").mkdir()
        self.git("update-index", "--add", "--cacheinfo", "160000", self.first, "submodule")
        self.commit("Added gitlink")
        self.tip = self.head
        self.git("update-index", "--force-remove", "--", "submodule")
        self.commit("Deleted gitlink")
        with self.assertRaisesRegex(sync.Refused, "NONREGULAR_ENTRY_REFUSED"):
            self.run_api()
        self.assertEqual(self.roles, [])
        self.assertTrue(all(row[0] == "GET" for row in self.calls))

    def test_missing_deletion_parent_refused(self):
        self.deletion_fixture()
        original = sync.git
        def missing(evidence, source, *args, **kwargs):
            if args[:2] == ("--literal-pathspecs", "ls-tree") and args[-1] == "ordinary.txt":
                return subprocess.CompletedProcess(args, 0, b"", b"")
            return original(evidence, source, *args, **kwargs)
        with patch.object(sync, "git", side_effect=missing):
            with self.assertRaisesRegex(sync.Refused, "DELETION_PARENT_ENTRY_REQUIRED"):
                self.run_api()
        self.assertEqual(self.roles, [])
        self.assertTrue(all(row[0] == "GET" for row in self.calls))

    def test_symlink_addition_refused(self):
        (self.source / "ordinary-link").symlink_to("LICENSE")
        self.git("add", "--", "ordinary-link")
        self.commit("Added symlink")
        with self.assertRaisesRegex(sync.Refused, "NONREGULAR_ENTRY_REFUSED"):
            self.run_api()
        self.assertEqual(self.roles, [])

    def test_symlink_replacement_refused(self):
        (self.source / "ordinary-link").symlink_to("LICENSE")
        self.git("add", "--", "ordinary-link")
        self.commit("Added symlink")
        self.tip = self.head
        self.git("rm", "--", "ordinary-link")
        (self.source / "ordinary-link").write_text("regular replacement\n")
        self.git("add", "--", "ordinary-link")
        self.commit("Replaced symlink with regular file")
        with self.assertRaisesRegex(sync.Refused, "NONREGULAR_ENTRY_REFUSED"):
            self.run_api()
        self.assertEqual(self.roles, [])

    def test_gitlink_addition_refused(self):
        (self.source / "submodule").mkdir()
        self.git("update-index", "--add", "--cacheinfo", "160000", self.first, "submodule")
        self.commit("Added gitlink")
        with self.assertRaisesRegex(sync.Refused, "NONREGULAR_ENTRY_REFUSED"):
            self.run_api()
        self.assertEqual(self.roles, [])

    def test_deletions_count_toward_file_capacity(self):
        names = ["ordinary-%02d.txt" % i for i in range(api.MAX_FILES + 1)]
        for name in names:
            (self.source / name).write_text("ordinary fixture\n")
        self.git("add", "--", *names)
        self.commit("Added file capacity fixtures")
        self.tip = self.head
        self.git("rm", "--", *names)
        self.commit("Deleted too many files")
        with self.assertRaisesRegex(sync.Refused, "API_FILE_CAPACITY"):
            self.run_api()
        self.assertEqual(self.roles, [])

    def test_deleted_blob_capacity_still_refused(self):
        (self.source / "ordinary.txt").write_bytes(b"x" * (api.MAX_BLOB + 1))
        self.git("add", "--", "ordinary.txt")
        self.commit("Added oversized fixture")
        self.tip = self.head
        self.git("rm", "--", "ordinary.txt")
        self.commit("Deleted oversized fixture")
        with self.assertRaisesRegex(sync.Refused, "API_BLOB_CAPACITY"):
            self.run_api()
        self.assertEqual(self.roles, [])

    def test_commit_capacity_unchanged(self):
        self.assertEqual(api.MAX_COMMITS, 8)
        for index in range(api.MAX_COMMITS):
            (self.source / "LICENSE").write_text("fixture %d\n" % index)
            self.git("add", "--", "LICENSE")
            self.commit("Ordinary tail")
        with self.assertRaisesRegex(sync.Refused, "API_COMMIT_CAPACITY"):
            self.run_api()
        self.assertEqual(self.roles, [])

    def test_deletion_commit_mismatch_never_patches(self):
        self.deletion_fixture()
        def mismatch(evidence, method, suffix, payload=None):
            result = self.request(evidence, method, suffix, payload)
            if method == "POST" and suffix == "/commits":
                result["sha"] = "0" * 40
            return result
        old = self.tip
        with self.assertRaisesRegex(sync.Refused, "OBJECT_SHA_MISMATCH_commit"):
            self.run_api(request=mismatch)
        self.assertEqual(self.tip, old)
        self.assertFalse(any(row[0] == "PATCH" for row in self.calls))

    def test_root_commit_header_refused(self):
        with self.assertRaisesRegex(sync.Refused, "LINEAR_NONROOT_COMMIT_REQUIRED"):
            api.local_commit(self.evidence, self.source, self.first)

    def test_signature_header_refused(self):
        raw = self.git("cat-file", "commit", self.head).stdout
        raw = raw.replace(b"\nauthor ", b"\ngpgsig unsupported-fixture\n continuation\nauthor ", 1)
        process = subprocess.run(["git", "-C", str(self.source), "hash-object", "-t",
                                  "commit", "-w", "--stdin"], input=raw, capture_output=True)
        self.assertEqual(process.returncode, 0)
        sha = process.stdout.decode().strip()
        with self.assertRaisesRegex(sync.Refused, "SIGNED_EXTENDED_OR_MERGE_REFUSED"):
            api.local_commit(self.evidence, self.source, sha)

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
        with self.assertRaisesRegex(sync.Refused, "API_OBJECT_SCOPE"):
            api.request(self.evidence, "DELETE", "/refs/heads/main")
        with self.assertRaisesRegex(sync.Refused, "API_OBJECT_SCOPE"):
            api.request(self.evidence, "PATCH", "/refs/heads/other",
                        {"sha": self.head, "force": False})
        self.assertEqual(self.evidence.calls, [])


def source_fingerprints():
    source = Path(__file__).resolve().parents[1]
    process = subprocess.run(["git", "-C", str(source), "ls-files", "-z"],
                             capture_output=True, check=True)
    return {name: sync.digest((source / name).read_bytes())
            for name in process.stdout.decode().split("\0") if name}


def source_binding():
    source = Path(__file__).resolve().parents[1]
    values = {"root": str(source)}
    for name, args in (("head", ["rev-parse", "HEAD"]),
                       ("branch", ["branch", "--show-current"]),
                       ("status", ["status", "--porcelain=v1", "-z"])):
        process = subprocess.run(["git", "-C", str(source), *args],
                                 capture_output=True, check=True)
        values[name] = process.stdout.decode().strip()
    return values


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sync.Evidence(args.out)
    ROOT = args.out
    started = sync.stamp()
    before = (api.own_sha(), sync.tool_sha(), sync.digest(Path(__file__).read_bytes()))
    files_before = source_fingerprints()
    binding_before = source_binding()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ApiTests)
    discovered = suite.countTestCases()
    with (ROOT / "tests.log").open("x") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2, failfast=True).run(
            suite)
    after = (api.own_sha(), sync.tool_sha(), sync.digest(Path(__file__).read_bytes()))
    files_after = source_fingerprints()
    binding_after = source_binding()
    drift = sorted(name for name in files_before.keys() | files_after.keys()
                   if files_before.get(name) != files_after.get(name))
    stable = before == after and not drift and binding_before == binding_after
    passed = result.wasSuccessful() and stable and result.testsRun == discovered > 0
    report = {"verdict": "PASS" if passed else "FAIL", "at": sync.stamp(),
              "tests": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skips": len(result.skipped),
              "actual_rc": 0 if passed else 1,
              "source_before": before, "source_after": after,
              "source_files_before": files_before, "source_files_after": files_after,
              "source_drift": drift, "source_file_count": len(files_before),
              "source_binding_before": binding_before, "source_binding_after": binding_after,
              "started_at": started, "discovered": discovered,
              "passed": result.testsRun - len(result.failures) - len(result.errors)
              - len(result.skipped),
              "log_sha256": sync.digest((ROOT / "tests.log").read_bytes()),
              "source_stable": stable,
              "first_failure": (result.failures + result.errors)[0][0].id()
              if result.failures or result.errors else None,
              "scope": "LOCAL_GIT_INDEPENDENT_OBJECT_RECONSTRUCTION_NO_LIVE_SECRET"}
    (ROOT / "result.json").write_bytes(sync.encode(report))
    print("EXACT_API_TESTS verdict=%s rc=%d tests=%d failures=%d errors=%d drift=%d "
          "at=%s result=%s" % (report["verdict"], report["actual_rc"], result.testsRun,
                               len(result.failures), len(result.errors), len(drift),
                               report["at"], ROOT / "result.json"), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
