#!/usr/bin/env python3
"""SQLite fixture isolation controls and one-shot SIGBUS forensic collection.

Evidence is external; SQLite integrity runs on a fresh local copy including WAL,
never on the retained first-scene files. The two filesystem arms are diagnostic,
not performance measurements or acceptance of SQLite on a network filesystem.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import faulthandler
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

import acceptance as A
import bootstrap_from_infra as B

OLD = Path("/hf3fs-jd/data/tmp/opencad-config-suppression-owner-core-20261012-v1/"
           "20261011T223755361719Z/acceptance-smoke")
DEPENDENCIES = (
    "oss-alternatives/packages/cadcore/tests/test_store.py",
    "oss-alternatives/packages/cadcore/cadcore/store.py",
    "oss-alternatives/packages/vault/freechat_vault/store.py",
)


def filesystem(path: Path) -> str:
    return subprocess.check_output(
        ["stat", "-f", "-c", "%T", str(path)], text=True).strip()


def source_binding() -> dict:
    manifest = A.source_manifest(A.ROOT)
    return {
        "root": str(A.ROOT),
        "head": subprocess.check_output(
            ["git", "--no-replace-objects", "-C", str(A.ROOT), "rev-parse", "HEAD"],
            text=True).strip(),
        "status": subprocess.check_output(
            ["git", "--no-replace-objects", "-C", str(A.ROOT), "status", "--porcelain"],
            text=True),
        "files": manifest,
        "sha256": B.digest(manifest),
    }


def fs_actor() -> int:
    faulthandler.enable(all_threads=True)

    def observe(event, args):
        if event == "sqlite3.connect" and os.fsdecode(args[0]) != ":memory:":
            path = Path(os.fsdecode(args[0])).absolute()
            print("SQLITE_FIRST_SCENE " + json.dumps({
                "path": str(path), "parent_filesystem": filesystem(path.parent),
                "declared_runtime": os.environ.get("OPENCAD_TEST_RUNTIME_ROOT"),
                "tempdir": tempfile.gettempdir(),
            }, sort_keys=True), flush=True)

    sys.addaudithook(observe)
    # This explicit diagnostic actor observes the original fixture without
    # installing the repaired acceptance guard or normalizing its filesystem.
    A.scan(A.ROOT)
    sys.path.insert(0, str(A.PACKAGES / "cadcore"))
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "tests.test_store.StoreTestCase.test_27_document_survives_reopen")
    result = unittest.TextTestRunner(verbosity=2, resultclass=A.CountedResult).run(suite)
    print("INDEPENDENT_TEST_COUNTS " + json.dumps({
        "tests": result.testsRun, "passed": result.passed,
        "errors": len(result.errors), "failures": len(result.failures),
        "successful": result.wasSuccessful(),
    }, sort_keys=True))
    return 0 if result.wasSuccessful() and result.testsRun == 1 else 1


def retained_bytes(directory: Path) -> dict:
    return {name: B.regular_bytes(directory / name) for name in
            ("vault.sqlite3", "vault.sqlite3-wal", "vault.sqlite3-shm")}


def integrity_copy(local: Path, report: dict) -> None:
    original = OLD / "tmp/cadcore-store-wrc1wzp6/A"
    before = retained_bytes(original)
    report["retained_database"] = {
        "source": str(original), "source_filesystem": filesystem(original),
        "before": {name: {"bytes": len(data), "sha256": B.sha_bytes(data)}
                   for name, data in before.items()},
        "copy_root": str(local / "integrity"),
        "original_opened_by_sqlite": False,
    }
    copied = local / "integrity"
    copied.mkdir()
    for name, data in before.items():
        B.exclusive_bytes(copied / name, data)
    connection = None
    try:
        connection = sqlite3.connect((copied / "vault.sqlite3").as_uri() + "?mode=ro",
                                    uri=True)
        connection.execute("PRAGMA query_only=ON")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        report["retained_database"].update(
            integrity=integrity, foreign_key_violations=len(foreign_keys),
            tables=tables, wal_included=True, immutable_mode=False)
        if integrity != ["ok"] or foreign_keys or not tables:
            raise ValueError("retained database copy integrity/schema check failed")
    finally:
        if connection is not None:
            connection.close()
        after = retained_bytes(original)
        report["retained_database"]["after"] = {
            name: {"bytes": len(data), "sha256": B.sha_bytes(data)}
            for name, data in after.items()}
        if before != after:
            raise ValueError("retained first-scene database drift")


def forensics(out: Path, local: Path) -> int:
    out = out.resolve()
    if not out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()):
        raise ValueError("forensic evidence must be in the authorized 3FS tmp scope")
    if not local.is_absolute() or ".." in local.parts or local.is_symlink():
        raise ValueError("unsafe forensic local root")
    local = local.resolve()
    if not local.is_relative_to(Path("/data/tmp")) or local == Path("/data/tmp"):
        raise ValueError("forensic local root must be a new /data/tmp child")
    out.mkdir(parents=True, exist_ok=False)
    local.mkdir(exist_ok=False)
    if filesystem(local).startswith("fuse"):
        raise ValueError("forensic integrity copy must use local persistent storage")
    report = {"verdict": "FAIL", "purpose": "FORENSICS_NOT_PERFORMANCE",
              "started_at": B.now(), "arms": [], "original_failure": str(OLD),
              "direct_sigbus_cause": "UNATTRIBUTED", "production_ready": False}
    before = source_binding()
    report["source_before"] = before
    try:
        original_manifest = json.loads((OLD / "result.json").read_text())["source_before"]
        for name in DEPENDENCIES:
            if original_manifest[name] != before["files"][name]:
                raise ValueError(f"original dependency differs: {name}")
        report["old_dependencies_byte_equal"] = True
        for name, root in (("hf3fs", out / "filesystem-arm"),
                           ("ext4", local / "filesystem-arm")):
            root.mkdir()
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONFAULTHANDLER="1", TMPDIR=str(root),
                       OPENCAD_TEST_RUNTIME_ROOT=str(local))
            log = out / (name + ".log")
            argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--fs-actor"]
            with log.open("xb") as handle:
                process = subprocess.run(argv, cwd=A.ROOT, env=env,
                                         stdout=handle, stderr=subprocess.STDOUT)
                handle.write(f"\nFORENSIC_ORIGINAL_CHILD_RC rc={process.returncode}\n".encode())
            text = log.read_text()
            report["arms"].append({
                "name": name, "actual_rc": process.returncode, "argv": argv,
                "filesystem": filesystem(root), "tmpdir": str(root),
                "declared_runtime": str(local), "log": str(log),
                "log_sha256": B.sha_bytes(log.read_bytes()),
                "sqlite_path_observed": "SQLITE_FIRST_SCENE " in text,
                "native_stack_present": "Fatal Python error: Bus error" in text,
                "test_counts_marker_present": "INDEPENDENT_TEST_COUNTS " in text,
            })
            if not report["arms"][-1]["sqlite_path_observed"]:
                raise ValueError(f"filesystem actor did not observe a SQLite open: {name}")
            if process.returncode not in (0, -7):
                raise ValueError(f"unexpected filesystem actor failure: {name} rc={process.returncode}")
        integrity_copy(local, report)
        report["sigbus_reproduced"] = any(arm["actual_rc"] == -7 for arm in report["arms"])
        report["verdict"] = "COLLECTED"
    except Exception as exc:
        report["first_cause"] = {"type": type(exc).__name__, "detail": str(exc),
                                 "traceback": traceback.format_exc()}
    finally:
        after = source_binding()
        report["source_after"] = after
        report["source_drift"] = sorted(
            name for name in before["files"].keys() | after["files"].keys()
            if before["files"].get(name) != after["files"].get(name))
        if (report["source_drift"] or before["head"] != after["head"] or
                before["status"] != after["status"]):
            report["verdict"] = "FAIL"
            report.setdefault("first_cause", {"detail": "forensic source drift"})
        report["finished_at"] = B.now()
        B.exclusive_json(out / "result.json", report)
        print(f"SQLITE_FORENSICS verdict={report['verdict']} evidence={out}")
    return 0 if report["verdict"] == "COLLECTED" else 1


def environment_actor() -> int:
    guard = A.configure_runtime()
    runtime = os.environ.get("OPENCAD_TEST_RUNTIME_ROOT")
    actual = tempfile.gettempdir()
    matched = runtime == actual
    print("INDEPENDENT_TEST_COUNTS " + json.dumps({
        "tests": 1, "passed": int(matched), "failures": int(not matched),
        "errors": 0, "skipped": [], "expected_failures": [],
        "unexpected_successes": [], "successful": matched,
    }, sort_keys=True))
    print("ENVIRONMENT_BINDING " + json.dumps(
        {"runtime": runtime, "TMPDIR": os.environ.get("TMPDIR"),
         "tempfile": actual, "equal": matched}, sort_keys=True))
    A.runtime_marker(guard)
    return 0 if matched else 3


def guard_actor(database: str, drift: bool) -> int:
    guard = A.configure_runtime()
    if drift:
        tempfile.tempdir = str(A.LOCAL_RUNTIME_PARENT)
    try:
        db = sqlite3.connect(database, uri=database.startswith("file:"))
        db.execute("CREATE TABLE fixture(value INTEGER)")
        db.close()
    except ValueError:
        A.runtime_marker(guard)
        return 3
    A.runtime_marker(guard)
    return 0


CONTROL_OUT = None
CONTROL_LOCAL = None


class RuntimeIsolationTests(unittest.TestCase):
    def setUp(self):
        parent = CONTROL_LOCAL or Path(os.environ["OPENCAD_TEST_RUNTIME_ROOT"])
        self.root = Path(tempfile.mkdtemp(prefix="case-", dir=parent))
        evidence_parent = CONTROL_OUT or Path(os.environ["OPENCAD_EVIDENCE_ROOT"])
        self.evidence = evidence_parent / self._testMethodName
        self.evidence.mkdir()

    def actor(self, database: str, drift=False) -> tuple[int, dict]:
        env = dict(os.environ, OPENCAD_TEST_RUNTIME_ROOT=str(self.root))
        argv = [sys.executable, "-B", str(Path(__file__).resolve()),
                "--guard-actor", database]
        if drift:
            argv.append("--tempfile-drift")
        log = self.evidence / "actor.log"
        with log.open("xb") as handle:
            process = subprocess.run(argv, env=env, cwd=A.ROOT,
                                     stdout=handle, stderr=subprocess.STDOUT)
            handle.write(f"\nACTUAL_CHILD_RC rc={process.returncode}\n".encode())
        lines = [json.loads(line.removeprefix("INDEPENDENT_RUNTIME_BINDING "))
                 for line in log.read_text().splitlines()
                 if line.startswith("INDEPENDENT_RUNTIME_BINDING ")]
        self.assertEqual(len(lines), 1)
        result = {"actual_rc": process.returncode,
                  "log_sha256": B.sha_bytes(log.read_bytes()), "binding": lines[0]}
        B.exclusive_json(self.evidence / "actor.json", result)
        return process.returncode, lines[0]

    def test_actual_child_tempdir_equals_declared_runtime(self):
        (self.evidence / "tmp").mkdir()
        result = A.child([str(Path(__file__).resolve()), "--environment-actor"],
                         A.ROOT, self.evidence, "environment", self.root)
        self.assertEqual(result["rc"], 0, "actual child inherited non-local TMPDIR")
        self.assertTrue(result["counts"]["successful"])

    def test_local_sqlite_real_connection_observed(self):
        path = self.root / "actual.sqlite3"
        rc, binding = self.actor(str(path))
        self.assertEqual(rc, 0)
        self.assertTrue(path.is_file())
        self.assertEqual(binding["sqlite_opens"], 1)
        self.assertEqual(set(binding["sqlite_paths"]), {str(path)})

    def test_hf3fs_connection_rejected_before_creation(self):
        path = self.evidence / "forbidden.sqlite3"
        rc, binding = self.actor(str(path))
        self.assertEqual(rc, 3)
        self.assertTrue(binding["rejected"])
        self.assertFalse(path.exists())

    def test_hf3fs_uri_rejected_before_creation(self):
        path = self.evidence / "forbidden.sqlite3"
        rc, binding = self.actor(path.as_uri())
        self.assertEqual(rc, 3)
        self.assertTrue(binding["rejected"])
        self.assertFalse(path.exists())

    def test_local_declaration_does_not_authorize_other_local_root(self):
        path = CONTROL_LOCAL or self.root.parent
        target = path / (self.root.name + "-outside.sqlite3")
        rc, binding = self.actor(str(target))
        self.assertEqual(rc, 3)
        self.assertTrue(binding["rejected"])
        self.assertFalse(target.exists())

    def test_actual_tempfile_drift_is_not_hidden_by_local_declaration(self):
        path = self.root / "forbidden.sqlite3"
        rc, binding = self.actor(str(path), drift=True)
        self.assertEqual(rc, 3)
        self.assertTrue(binding["rejected"])
        self.assertFalse(path.exists())

    def test_symlink_database_rejected(self):
        target = self.root / "target.sqlite3"
        link = self.root / "linked.sqlite3"
        link.symlink_to(target)
        rc, binding = self.actor(str(link))
        self.assertEqual(rc, 3)
        self.assertTrue(binding["rejected"])
        self.assertFalse(target.exists())

    def test_symlink_wal_sidecar_rejected(self):
        target = self.root / "unrelated"
        target.write_bytes(b"untouched\n")
        path = self.root / "database.sqlite3"
        Path(str(path) + "-wal").symlink_to(target)
        rc, binding = self.actor(str(path))
        self.assertEqual(rc, 3)
        self.assertTrue(binding["rejected"])
        self.assertFalse(path.exists())
        self.assertEqual(target.read_bytes(), b"untouched\n")

    def test_symlink_parent_rejected(self):
        target = self.root / "real"
        target.mkdir()
        link = self.root / "linked"
        link.symlink_to(target, target_is_directory=True)
        rc, binding = self.actor(str(link / "forbidden.sqlite3"))
        self.assertEqual(rc, 3)
        self.assertTrue(binding["rejected"])
        self.assertFalse((target / "forbidden.sqlite3").exists())

    def test_network_uri_authority_rejected(self):
        path = self.root / "forbidden.sqlite3"
        rc, binding = self.actor("file://remote" + str(path))
        self.assertEqual(rc, 3)
        self.assertTrue(binding["rejected"])
        self.assertFalse(path.exists())

    def test_empty_sqlite_filename_rejected(self):
        rc, binding = self.actor("")
        self.assertEqual(rc, 3)
        self.assertTrue(binding["rejected"])

    def test_local_file_uri_positive(self):
        path = self.root / "uri.sqlite3"
        rc, binding = self.actor(path.as_uri())
        self.assertEqual(rc, 0)
        self.assertEqual(binding["sqlite_opens"], 1)
        self.assertTrue(path.exists())

    def test_runtime_root_hf3fs_rejected(self):
        with self.assertRaisesRegex(ValueError, "/data/tmp"):
            A.local_runtime_path(self.evidence / "runtime", fresh=True)

    def test_runtime_root_relative_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            A.local_runtime_path(Path("relative-runtime"))

    def test_runtime_root_dotdot_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            A.local_runtime_path(self.root / ".." / "escape")

    def test_runtime_root_symlink_alias_rejected(self):
        link = self.root / "linked-root"
        link.symlink_to(self.evidence, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            A.local_runtime_path(link / "new")

    def test_runtime_existing_root_cannot_be_reused(self):
        with self.assertRaises(FileExistsError):
            A.local_runtime_path(self.root, fresh=True)

    def test_runtime_root_long_path_rejected(self):
        with self.assertRaisesRegex(ValueError, "short-path"):
            A.local_runtime_path(self.root / ("long" * 30), short=True)

    def test_network_mount_under_local_scope_rejected(self):
        with patch.object(A.subprocess, "check_output", return_value="fuseblk\n"):
            with self.assertRaisesRegex(ValueError, "persistent"):
                A.local_runtime_path(self.root)

    def test_repository_inside_local_scope_rejected(self):
        repo = self.root / "repository"
        repo.mkdir()
        (repo / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "repository"):
            A.local_runtime_path(repo / "runtime")

    def rejected_cli(self, runtime: Path | None, *, empty=False):
        out = self.evidence / "cli"
        env = dict(os.environ)
        env.pop("OPENCAD_TEST_RUNTIME_ROOT", None)
        if empty:
            env["OPENCAD_TEST_RUNTIME_ROOT"] = ""
        argv = [sys.executable, "-B", str(A.ROOT / "tools/acceptance.py"),
                "--stage", "scan", "--out", str(out)]
        if runtime is not None:
            argv.extend(("--runtime-root", str(runtime)))
        log = self.evidence / "cli.log"
        with log.open("xb") as handle:
            process = subprocess.run(argv, env=env, cwd=A.ROOT,
                                     stdout=handle, stderr=subprocess.STDOUT)
            handle.write(f"\nACTUAL_CHILD_RC rc={process.returncode}\n".encode())
        report = json.loads((out / "result.json").read_text())
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["actual_rc"], 1)
        self.assertTrue(report["first_cause"])
        self.assertEqual(report["steps"], [])
        return report

    def test_real_cli_hf3fs_runtime_rejected_before_child(self):
        path = self.evidence / "forbidden-runtime"
        self.rejected_cli(path)
        self.assertFalse(path.exists())

    def test_real_cli_existing_local_root_not_overwritten(self):
        sentinel = self.root / "sentinel"
        sentinel.write_bytes(b"unchanged\n")
        self.rejected_cli(self.root)
        self.assertEqual(sentinel.read_bytes(), b"unchanged\n")

    def test_real_cli_empty_explicit_runtime_declaration_rejected(self):
        self.rejected_cli(None, empty=True)

    def test_real_cli_symlink_alias_rejected_before_creation(self):
        link = self.root / "alias"
        link.symlink_to(self.evidence, target_is_directory=True)
        self.rejected_cli(link / "forbidden")
        self.assertFalse((self.evidence / "forbidden").exists())


def selftest(out: Path, local: Path, case: str | None) -> int:
    global CONTROL_OUT, CONTROL_LOCAL
    out = out.resolve()
    local = local.absolute()
    if not out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()):
        raise ValueError("selftest evidence outside authorized 3FS tmp")
    if not local.is_relative_to(Path("/data/tmp")) or local == Path("/data/tmp"):
        raise ValueError("selftest runtime outside authorized local scope")
    out.mkdir(parents=True, exist_ok=False)
    local.mkdir(exist_ok=False)
    CONTROL_OUT, CONTROL_LOCAL = out, local
    before = source_binding()
    report = {"verdict": "FAIL", "started_at": B.now(), "source_before": before,
              "first_cause": None}
    log = out / "selftest.log"
    with log.open("x") as handle, redirect_stdout(handle), redirect_stderr(handle):
        suite = (unittest.defaultTestLoader.loadTestsFromName(
            "RuntimeIsolationTests." + case, sys.modules[__name__]) if case else
            unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeIsolationTests))
        result = unittest.TextTestRunner(verbosity=2, resultclass=A.CountedResult).run(suite)
        report.update(tests=result.testsRun, passed=result.passed,
                      failures=len(result.failures), errors=len(result.errors),
                      skipped=len(result.skipped), actual_rc=0 if result.wasSuccessful() else 1)
        if result.wasSuccessful() and result.testsRun > 0:
            report["verdict"] = "PASS"
        else:
            failed = result.failures + result.errors
            report["first_cause"] = (
                {"test": str(failed[0][0]), "detail": failed[0][1]}
                if failed else {"detail": "empty selftest"})
    after = source_binding()
    report["source_after"] = after
    report["source_drift"] = sorted(
        name for name in before["files"].keys() | after["files"].keys()
        if before["files"].get(name) != after["files"].get(name))
    if (report["source_drift"] or before["head"] != after["head"] or
            before["status"] != after["status"]):
        report["verdict"] = "FAIL"
        report["actual_rc"] = 1
        report["first_cause"] = report["first_cause"] or {"detail": "selftest source drift"}
    report["log_sha256"] = B.sha_bytes(log.read_bytes())
    report["finished_at"] = B.now()
    B.exclusive_json(out / "result.json", report)
    print(f"SQLITE_RUNTIME_SELFTEST verdict={report['verdict']} "
          f"tests={report['tests']} passed={report['passed']} evidence={out}")
    return report["actual_rc"]


def verify_result(saved: Path, out: Path) -> int:
    out = out.resolve()
    if not out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()):
        raise ValueError("readback evidence outside authorized 3FS tmp")
    out.mkdir(parents=True, exist_ok=False)
    current = source_binding()
    report = {"verdict": "FAIL", "started_at": B.now(), "input": str(saved),
              "source_before": current, "checks": [], "first_cause": None}

    def require(label, condition):
        if not condition:
            raise ValueError(label)
        report["checks"].append(label)

    try:
        raw = B.regular_bytes(saved)
        suite = json.loads(raw)
        report["input_sha256"] = B.sha_bytes(raw)
        require("complete smoke PASS/actual_rc", suite["verdict"] == "PASS" and
                type(suite["actual_rc"]) is int and suite["actual_rc"] == 0 and
                suite["stage"] == "smoke")
        require("source files bidirectional/current", suite["source_before"] ==
                suite["source_after"] == current["files"] and not suite["source_drift"])
        require("source head/status bidirectional/current",
                suite["source_head_before"] == suite["source_head_after"] == current["head"] and
                suite["source_status_before"] == suite["source_status_after"] == current["status"])
        require("source digest", suite["source_sha256"] == suite["source_after_sha256"] ==
                B.digest(current["files"]))
        root = A.local_runtime_path(Path(suite["test_runtime"]["root"]), short=True)
        require("runtime exists local", root.is_dir())
        steps = suite["steps"]
        labels = ["sqlite-runtime-negative-controls", "bootstrap-negative-controls",
                  "http-selftest", "test_docmodel.py", "test_store.py", "dxf"]
        require("complete smoke stage sequence", [step["label"] for step in steps] == labels)
        totals = dict(tests=0, passed=0, failures=0, errors=0, skipped=0, http_checks=0)
        for step in steps:
            label = step["label"]
            require(label + ": original rc and no errors",
                    type(step["rc"]) is int and step["rc"] == 0 and
                    not step.get("marker_error") and not step.get("runtime_error"))
            log_raw = B.regular_bytes(saved.parent / (label + ".log"))
            text = log_raw.decode()
            require(label + ": log SHA", B.sha_bytes(log_raw) == step["log_sha256"])
            require(label + ": raw child exit marker",
                    text.splitlines().count("INDEPENDENT_CHILD_RC rc=0") == 1)
            bindings = [json.loads(line.removeprefix("INDEPENDENT_RUNTIME_BINDING "))
                        for line in text.splitlines()
                        if line.startswith("INDEPENDENT_RUNTIME_BINDING ")]
            binding = step["sqlite_runtime"]
            require(label + ": actual runtime binding", bindings == [binding] and
                    binding["root"] == binding["TMPDIR"] == binding["tempfile"] == str(root) and
                    binding["filesystem"] in A.PERSISTENT_FILESYSTEMS and not binding["rejected"])
            for path, fs in binding["sqlite_paths"].items():
                require(label + ": SQLite path " + path,
                        Path(path).is_relative_to(root) and fs in A.PERSISTENT_FILESYSTEMS)
            if label in ("test_store.py", "dxf"):
                require(label + ": nonempty SQLite observations",
                        binding["sqlite_opens"] > 0 and bool(binding["sqlite_paths"]))
            if label == "http-selftest":
                require(label + ": exact HTTP marker",
                        text.splitlines().count(
                            "OPENCAD_SERVER selftest verdict=PASS checks=15 failed=0") == 1 and
                        step["http_checks"] == 15)
                totals["http_checks"] += 15
                continue
            summary = step["counts"]
            markers = [json.loads(line.removeprefix("INDEPENDENT_TEST_COUNTS "))
                       for line in text.splitlines()
                       if line.startswith("INDEPENDENT_TEST_COUNTS ")]
            require(label + ": complete count marker", markers == [summary] and
                    summary["tests"] == summary["discovered"] > 0 and
                    A.counts_reconcile(summary) and summary["successful"] and
                    not summary["failures"] and not summary["errors"] and
                    not summary["unexpected_successes"])
            for key in ("tests", "passed", "failures", "errors"):
                totals[key] += summary[key]
            totals["skipped"] += len(summary["skipped"])
        require("total counts reconcile", suite["totals"] == totals)
        report["totals"] = totals
        report["verdict"] = "PASS"
    except Exception as exc:
        report["first_cause"] = {"type": type(exc).__name__, "detail": str(exc),
                                 "traceback": traceback.format_exc()}
    after = source_binding()
    report["source_after"] = after
    if current != after:
        report["verdict"] = "FAIL"
        report["first_cause"] = report["first_cause"] or {"detail": "readback source drift"}
    report["actual_rc"] = 0 if report["verdict"] == "PASS" else 1
    report["finished_at"] = B.now()
    B.exclusive_json(out / "result.json", report)
    print(f"SQLITE_SMOKE_READBACK verdict={report['verdict']} "
          f"checks={len(report['checks'])} evidence={out}")
    return report["actual_rc"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fs-actor", action="store_true")
    parser.add_argument("--forensics", action="store_true")
    parser.add_argument("--environment-actor", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--guard-actor")
    parser.add_argument("--tempfile-drift", action="store_true")
    parser.add_argument("--case")
    parser.add_argument("--verify-result", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    args = parser.parse_args()
    if args.verify_result:
        if args.out is None:
            parser.error("--verify-result requires --out")
        raise SystemExit(verify_result(args.verify_result, args.out))
    if args.fs_actor:
        raise SystemExit(fs_actor())
    if args.environment_actor:
        raise SystemExit(environment_actor())
    if args.guard_actor is not None:
        raise SystemExit(guard_actor(args.guard_actor, args.tempfile_drift))
    if args.selftest:
        if args.out is None or args.runtime_root is None:
            parser.error("--selftest requires --out and --runtime-root")
        raise SystemExit(selftest(args.out, args.runtime_root, args.case))
    if args.forensics:
        if args.out is None or args.runtime_root is None:
            parser.error("--forensics requires --out and --runtime-root")
        raise SystemExit(forensics(args.out, args.runtime_root))
    unittest.main(argv=[sys.argv[0]], verbosity=2)
