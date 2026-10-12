#!/usr/bin/env python3
"""Offline reviewer controls; fresh external evidence and only owned temporary fixtures.

No product tests, browser, HTTP, GPU, remote operations or original evidence writes.
Run directly with --out and --all-evidence to retain the test log and verdict.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import gateway_publication_review as review


class DynamicClosureTests(unittest.TestCase):
    all_root = None
    source = None
    baseline = None
    original_failure = None
    sqlite_source = None
    failed_all_root = None
    failed_source = None
    inputs = {}
    control_count = 0

    @classmethod
    def read(cls, path):
        path = review.external(Path(path))
        raw = review.regular(path)
        cls.inputs[str(path)] = review.binding(raw)
        return raw

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(cls.read(cls.all_root / "result.json"))
        cls.files = {name: cls.read(cls.all_root / name) for name in
                     ("acceptance.log", *(label + ".log" for label in review.LABELS))}
        cls.previous = json.loads(cls.read(cls.baseline / "result.json"))

    def all_review(self, report=None, files=None, baseline=None, read=None):
        return review.review_all(report or self.report, files or self.files, self.source,
                                 self.all_root, baseline or self.previous, read or self.read)

    def reject(self, function, code):
        with self.assertRaisesRegex(review.Refused, "^" + code):
            function()
        type(self).control_count += 1

    def changed_log(self, mutate):
        report, files = deepcopy(self.report), dict(self.files)
        label = "bootstrap-negative-controls"
        files[label + ".log"] = mutate(files[label + ".log"])
        report["steps"][0]["log_sha256"] = review.sha(files[label + ".log"])
        return report, files

    def test_real_all_dynamic_positive(self):
        result = self.all_review()
        self.assertEqual(result["totals"], self.report["totals"])
        for row in result["steps"]:
            if row["label"] != "http-selftest":
                self.assertEqual(len(row["discovered_test_ids"]), row["tests"])
                self.assertEqual(row["test_identity_difference"], {"missing": [], "extra": []})

    def test_single_tail_positive_and_changed_sha_negative(self):
        parent = review.git(self.source, ["rev-parse", self.head + "^"]).decode().strip()
        value = {"commit": self.head, "github_before": parent,
                 "tail": [self.head], "max_commits": 8}
        self.assertEqual(review.review_transport_tail(value, self.source), [self.head])
        changed = deepcopy(value)
        changed["tail"][0] = ("0" if self.head[0] != "0" else "1") + self.head[1:]
        self.assertNotEqual(changed["tail"], value["tail"])
        self.assertTrue(review.SHA.fullmatch(changed["tail"][0]))
        self.reject(lambda: review.review_transport_tail(changed, self.source),
                    "TRANSPORT_EXACT_TAIL")

    def test_multiple_tail_positive_and_real_order_negative(self):
        parent = review.git(self.source, ["rev-parse", self.head + "^^"]).decode().strip()
        tail = review.git(self.source, ["rev-list", "--reverse",
            parent + ".." + self.head]).decode().splitlines()
        self.assertEqual(len(tail), 2)
        value = {"commit": self.head, "github_before": parent, "tail": tail, "max_commits": 8}
        self.assertEqual(review.review_transport_tail(value, self.source), tail)
        changed = deepcopy(value)
        changed["tail"].reverse()
        self.assertNotEqual(changed["tail"], tail)
        self.reject(lambda: review.review_transport_tail(changed, self.source),
                    "TRANSPORT_EXACT_TAIL")

    def test_original_failure_retained_and_same_inputs_repaired(self):
        failed = json.loads(self.read(self.original_failure))
        self.assertEqual((failed["verdict"], failed["actual_rc"], failed["first_cause"]),
                         ("FAIL", 1, "TOTALS_MISMATCH"))
        for name, row in failed["inputs"].items():
            self.assertEqual(review.binding(self.read(Path(name))), row)
        self.assertEqual(self.all_review()["source_sha256"], failed["source_sha256"])

    def test_log_delete_rehash_marker_footer_totals_cannot_hide_missing_test(self):
        report, files = deepcopy(self.report), dict(self.files)
        text = files["bootstrap-negative-controls.log"].decode()
        starts = list(review.TEST_START.finditer(text))
        removed = text[starts[0].start():starts[1].start()]
        text = text.replace(removed, "", 1)
        counts = report["steps"][0]["counts"]
        old = json.dumps(counts, sort_keys=True)
        for key in ("discovered", "tests", "passed"):
            counts[key] -= 1
        text = text.replace(old, json.dumps(counts, sort_keys=True))
        text = text.replace("Ran %d tests" % (counts["tests"] + 1),
                            "Ran %d tests" % counts["tests"])
        for key in ("tests", "passed"):
            report["totals"][key] -= 1
        files["bootstrap-negative-controls.log"] = text.encode()
        report["steps"][0]["log_sha256"] = review.sha(text.encode())
        self.reject(lambda: self.all_review(report, files), "TEST_IDENTITY_SET_MISMATCH")

    def test_log_test_rename_rehash_rejected(self):
        report, files = self.changed_log(lambda raw: raw.replace(b"test_copy_and_unrelated",
                                                                b"test_counterfeit_unrelated", 1))
        self.reject(lambda: self.all_review(report, files), "TEST_IDENTITY_SET_MISMATCH")

    def test_duplicate_test_rehash_rejected(self):
        def mutate(raw):
            text = raw.decode()
            starts = list(review.TEST_START.finditer(text))
            block = text[starts[0].start():starts[1].start()]
            return (text[:starts[1].start()] + block + text[starts[1].start():]).encode()
        report, files = self.changed_log(mutate)
        self.reject(lambda: self.all_review(report, files), "DUPLICATE_TEST_LOG")

    def test_totals_only_and_discovered_mutations_rejected(self):
        for field in ("tests", "passed", "skipped"):
            report = deepcopy(self.report)
            report["totals"][field] += 1
            self.reject(lambda r=report: self.all_review(r), "TOTALS_MISMATCH")
        report = deepcopy(self.report)
        report["steps"][0]["counts"]["discovered"] -= 1
        self.reject(lambda: self.all_review(report), "COUNT_LOG_BINDING")

    def test_empty_marker_footer_duplicate_terminal_and_cli_scope(self):
        mutations = (
            (lambda raw: b"\n".join(line for line in raw.splitlines()
                                    if not line.startswith(b"INDEPENDENT_TEST_COUNTS ")),
             "COUNT_LOG_BINDING"),
            (lambda raw: raw + b"\nINDEPENDENT_CHILD_RC rc=0\n", "CHILD_EXIT"),
            (lambda raw: raw.replace(b"\nOK\n", b"\nOK\nRan 20 tests in 1.0s\n", 1),
             "UNITTEST_FOOTER"),
            (lambda raw: raw.replace(b" ... ok\n", b" ... ERROR\n", 1), "TEST_NOT_PASS"),
        )
        for mutate, code in mutations:
            report, files = self.changed_log(mutate)
            self.reject(lambda r=report, f=files: self.all_review(r, f), code)
        report = deepcopy(self.report)
        report["steps"][0]["argv"] += ["--case", "one_test"]
        self.reject(lambda: self.all_review(report), "FULL_DISCOVERY_ARGUMENTS")

    def test_test_block_multiline_stdout_positive_and_missing_terminal_negative(self):
        text = "test_a (tests.Example)\nDescription ... diagnostic\nmore output\nok\n"
        self.assertEqual(review.test_log_results(text, "fixture")[0]["outcome"], "passed")
        self.reject(lambda: review.test_log_results(text.replace("\nok\n", "\n"), "fixture"),
                    "TEST_TERMINAL_SET")
        self.reject(lambda: review.test_log_results(text + "ok\n", "fixture"), "TEST_TERMINAL_SET")

    def test_static_discovery_member_removed_source_hash_recomputed_rejected(self):
        sources = deepcopy(self.report["source_before"])
        name = "tools/test_bootstrap.py"
        sources[name]["sha256"] = "0" * 64
        self.reject(lambda: review.static_test_inventory(self.source, "bootstrap-negative-controls",
                                                         sources), "TEST_SOURCE_BINDING")

    def test_conditional_load_tests_is_source_derived_and_unknown_hook_fails(self):
        with tempfile.TemporaryDirectory(prefix="gateway-discovery-", dir="/data/tmp") as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            path = root / "tools/test_bootstrap.py"
            raw = (b"import unittest\nHTTP_SOURCE = None\n"
                   b"class A(unittest.TestCase):\n def test_a(self): pass\n"
                   b"class B(unittest.TestCase):\n def test_b(self): pass\n"
                   b"def load_tests(loader, tests, pattern):\n"
                   b" suite = loader.loadTestsFromTestCase(A)\n"
                   b" if HTTP_SOURCE is not None:\n"
                   b"  suite.addTests(loader.loadTestsFromTestCase(B))\n"
                   b" return suite\n")
            path.write_bytes(raw)
            source = {"tools/test_bootstrap.py": review.binding(raw)}
            discovered = review.static_test_inventory(root, "bootstrap-negative-controls", source)
            self.assertEqual(discovered, ["test_a (test_bootstrap.A)"])
            changed = raw.replace(b"HTTP_SOURCE = None", b"HTTP_SOURCE = True")
            path.write_bytes(changed)
            source["tools/test_bootstrap.py"] = review.binding(changed)
            self.assertEqual(review.static_test_inventory(root, "bootstrap-negative-controls", source),
                             ["test_a (test_bootstrap.A)", "test_b (test_bootstrap.B)"])
            self.assertNotEqual(discovered, review.static_test_inventory(
                root, "bootstrap-negative-controls", source))
            changed = raw.replace(b"HTTP_SOURCE is not None", b"arbitrary_runtime_selection()")
            path.write_bytes(changed)
            source["tools/test_bootstrap.py"] = review.binding(changed)
            self.reject(lambda: review.static_test_inventory(root, "bootstrap-negative-controls", source),
                        "UNSUPPORTED_DISCOVERY_CONDITION")

    def test_http_marker_and_result_rehash_cannot_hide_missing_source_check(self):
        report, files = deepcopy(self.report), dict(self.files)
        step = report["steps"][1]
        count = step["http_checks"]
        files["http-selftest.log"] = files["http-selftest.log"].replace(
            ("checks=%d" % count).encode(), ("checks=%d" % (count - 1)).encode())
        step["log_sha256"] = review.sha(files["http-selftest.log"])
        step["http_checks"] -= 1
        report["totals"]["http_checks"] -= 1
        self.reject(lambda: self.all_review(report, files), "HTTP_SOURCE_MARKER_COUNT")

    def test_delegated_receipt_missing_and_duplicate_identity_rejected(self):
        def missing(path):
            if "20261011T225727102524Z-discovery" in str(path):
                raise review.Refused("DELEGATED_MISSING")
            return self.read(path)
        self.reject(lambda: self.all_review(read=missing), "DELEGATED_MISSING")
        report, files = deepcopy(self.report), dict(self.files)
        text = files["web.log"].decode()
        start = next(match for match in review.TEST_START.finditer(text)
                     if "test_advanced" not in match[1])
        forged = "test_async_refresh_is_completion_driven_not_fixed_delay " \
                 "(tests.test_advanced_ui_review.StaticReviewTests) ... ok\n"
        text = text[:start.start()] + forged + text[start.start():]
        files["web.log"] = text.encode()
        report["steps"][3]["log_sha256"] = review.sha(text.encode())
        self.reject(lambda: self.all_review(report, files), "DUPLICATE_COMBINED_TEST")

    def test_actual_baseline_skip_sets_are_exact(self):
        self.assertEqual(self.all_review()["original_skips"],
                         [{"label": step["label"], **skip}
                          for step in self.previous["steps"] if step.get("counts")
                          for skip in step["counts"]["skipped"]])
        for mutation, code in (
            (lambda items: items.pop(), "ORIGINAL_SKIPS_CHANGED"),
            (lambda items: items.append(deepcopy(items[0])), "DUPLICATE_BASELINE_OR_ACTUAL_SKIP"),
            (lambda items: items[0].update(reason="other reason"), "ORIGINAL_SKIPS_CHANGED"),
        ):
            previous = deepcopy(self.previous)
            items = next(step["counts"]["skipped"] for step in previous["steps"]
                         if step.get("counts") and step["counts"]["skipped"])
            mutation(items)
            self.reject(lambda p=previous: self.all_review(baseline=p), code)

    def test_doc_successor_exact_bytes_modes_and_product_mutants(self):
        frozen = review.committed_manifest(self.source, self.head, modes=True)
        target = "889e885a4a3935ee593b9e18afef04faff60e294"
        current = review.committed_manifest(self.source, target, modes=True)
        allowed = ["docs/handoff/20261012-surface-private-release.md"]
        result = review.doc_successor(self.source, self.head, target, frozen, current, allowed)
        self.assertFalse(result["whole_source_all_accepted"])
        self.assertEqual(result["tail"], [target])
        self.reject(lambda: review.doc_successor(self.source, self.head, target, frozen, current, []),
                    "DOC_PATH_SET_MISMATCH")
        mutant = deepcopy(current)
        mutant["oss-alternatives/apps/opencad/server.py"]["sha256"] = "0" * 64
        self.reject(lambda: review.doc_successor(self.source, self.head, target, frozen, mutant, allowed),
                    "DOC_PATH_SET_MISMATCH")
        for mutate in (
            lambda values: values["oss-alternatives/apps/opencad/server.py"].update(mode="100755"),
            lambda values: values.pop("oss-alternatives/apps/opencad/server.py"),
            lambda values: values.update({"tools/unapproved.py":
                                          {"bytes": 1, "sha256": "0" * 64, "mode": "100644"}}),
        ):
            mutant = deepcopy(current)
            mutate(mutant)
            self.reject(lambda m=mutant: review.doc_successor(
                self.source, self.head, target, frozen, m, allowed), "DOC_PATH_SET_MISMATCH")
        mutant = deepcopy(current)
        mutant[allowed[0]]["mode"] = "100755"
        self.reject(lambda: review.doc_successor(self.source, self.head, target, frozen, mutant, allowed),
                    "DOC_MODE_OR_DELETION")
        self.reject(lambda: review.doc_successor(self.source, self.head, target, frozen, current,
                                                 ["docs/*.md"]), "EXPLICIT_DOC_PATH_REQUIRED")
        self.reject(lambda: review.doc_successor(self.source, self.head, target, frozen, mutant,
                                                 allowed + ["oss-alternatives/apps/opencad/server.py"]),
                    "EXPLICIT_DOC_PATH_REQUIRED")

    def test_real_git_doc_intermediate_product_revert_is_rejected(self):
        # Only this new regenerable repository is cleaned by TemporaryDirectory.
        with tempfile.TemporaryDirectory(prefix="gateway-closure-", dir="/data/tmp") as directory:
            root = Path(directory)
            def git(*args):
                done = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                      env=review.git_env())
                self.assertEqual(done.returncode, 0, done.stderr.decode())
                return done.stdout.decode().strip()
            git("init", "-q")
            (root / "docs").mkdir()
            (root / "docs/note.md").write_bytes(b"before\n")
            (root / "product.py").write_bytes(b"original\n")
            git("add", "docs/note.md", "product.py")
            git("-c", "user.name=Reviewer Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (root / "product.py").write_bytes(b"mutant\n")
            git("add", "product.py")
            git("-c", "user.name=Reviewer Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "product changed")
            (root / "product.py").write_bytes(b"original\n")
            (root / "docs/note.md").write_bytes(b"after\n")
            git("add", "docs/note.md", "product.py")
            git("-c", "user.name=Reviewer Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "product reverted with docs")
            tip = git("rev-parse", "HEAD")
            frozen = review.committed_manifest(root, base, modes=True)
            current = review.committed_manifest(root, tip, modes=True)
            self.reject(lambda: review.doc_successor(root, base, tip, frozen, current,
                                                     ["docs/note.md"]), "NON_DOC_INTERMEDIATE_CHANGE")

    def test_publication_target_is_bound_to_verified_doc_lineage_not_current_head(self):
        frozen, published, current = "a" * 40, "b" * 40, "c" * 40
        relation = {"tail": [published, current], "whole_source_all_accepted": False}
        binding = review.check_publication_target(relation, frozen, current, published)
        self.assertFalse(binding["current_source_head_published"])
        self.assertEqual(binding["recorded_commit"], published)
        self.assertTrue(review.check_publication_target(
            relation, frozen, current, current)["current_source_head_published"])
        self.reject(lambda: review.check_publication_target(relation, frozen, current, "d" * 40),
                    "PUBLICATION_OUTSIDE_VERIFIED_DOC_LINEAGE")

    def test_five_and_six_stage_plans_are_managed_source_bound_not_report_subsets(self):
        plan, guarded, _ = review.all_stage_plan(
            self.source, self.report["source_before"], self.all_root,
            self.report["test_runtime"]["root"])
        self.assertFalse(guarded)
        self.assertEqual([row["label"] for row in plan], list(review.LABELS))
        source = review.manifest(self.sqlite_source)
        plan, guarded, _ = review.all_stage_plan(
            self.sqlite_source, source, self.all_root, "/data/tmp/fixture-plan-runtime")
        self.assertTrue(guarded)
        self.assertEqual([row["label"] for row in plan],
                         ["sqlite-runtime-negative-controls", *review.LABELS])
        self.assertEqual(plan[0]["argv"][-2:], ["--pattern", "test_sqlite_runtime_isolation.py"])
        self.assertEqual(plan[2]["argv"][-1],
                         "/data/tmp/fixture-plan-runtime/http-selftest-workspace")
        mutant = deepcopy(self.report)
        mutant["steps"].pop(0)
        self.reject(lambda: self.all_review(mutant), "STAGE_PLAN_SET_MISMATCH")
        mutant = deepcopy(source)
        mutant["tools/acceptance.py"]["sha256"] = "0" * 64
        self.reject(lambda: review.all_stage_plan(self.sqlite_source, mutant, self.all_root,
                                                   "/data/tmp/fixture-plan-runtime"),
                    "STAGE_PLAN_SOURCE_BINDING")

    def test_sqlite_runtime_markers_paths_and_orthogonal_mutants(self):
        with tempfile.TemporaryDirectory(prefix="gw-sqlite-", dir="/data/tmp") as directory:
            root = Path(directory)
            filesystem = subprocess.check_output(["stat", "-f", "-c", "%T", str(root)],
                                                 text=True).strip()
            recorded = {"root": str(root), "TMPDIR": str(root), "tempfile": str(root),
                        "filesystem": filesystem, "sqlite_opens": 1,
                        "sqlite_paths": {str(root / "observed.sqlite3"): filesystem}, "rejected": []}
            step = {"label": "cadcore", "sqlite_runtime": recorded}
            counts = {"sqlite_runtime": recorded}
            text = "INDEPENDENT_RUNTIME_BINDING " + json.dumps(recorded) + "\n"
            self.assertEqual(review.sqlite_runtime_binding(step, counts, text, str(root), True),
                             recorded)
            for mutate, code in (
                (lambda r: r.update(TMPDIR="/hf3fs-jd/data/tmp"), "SQLITE_RUNTIME_DECLARATION"),
                (lambda r: r.update(rejected=["caught refusal"]), "SQLITE_RUNTIME_DECLARATION"),
                (lambda r: r.update(sqlite_opens=0), "SQLITE_OBSERVATIONS_UNRECONCILED"),
                (lambda r: r.update(sqlite_paths={}, sqlite_opens=0),
                 "SQLITE_REQUIRED_OBSERVATIONS_EMPTY"),
                (lambda r: r.update(sqlite_paths={"/hf3fs-jd/data/tmp/db.sqlite3": filesystem}),
                 "SQLITE_PATH_OUTSIDE_RUNTIME"),
                (lambda r: r.update(sqlite_paths={str(root / "db.sqlite3"): "fuse.hf3fs"}),
                 "SQLITE_RECORDED_FILESYSTEM"),
            ):
                value = deepcopy(recorded)
                mutate(value)
                self.reject(lambda r=value: review.sqlite_runtime_binding(
                    {"label": "cadcore", "sqlite_runtime": r}, {"sqlite_runtime": r},
                    "INDEPENDENT_RUNTIME_BINDING " + json.dumps(r) + "\n", str(root), True), code)
            self.reject(lambda: review.sqlite_runtime_binding(step, counts, "", str(root), True),
                        "SQLITE_RUNTIME_MARKER_REQUIRED")
            self.reject(lambda: review.sqlite_runtime_binding(step, counts, text + text, str(root), True),
                        "SQLITE_RUNTIME_MARKER_REQUIRED")
            self.reject(lambda: review.sqlite_runtime_binding(step, {}, text, str(root), True),
                        "SQLITE_RUNTIME_RESULT_BINDING")
            self.reject(lambda: review.sqlite_runtime_binding(step, counts, text, str(root), False),
                        "UNEXPECTED_SQLITE_MARKER")
            target, link = root / "actual", root / "linked"
            target.mkdir()
            link.symlink_to(target, target_is_directory=True)
            value = deepcopy(recorded)
            value["sqlite_paths"] = {str(link / "db.sqlite3"): filesystem}
            self.reject(lambda: review.sqlite_runtime_binding(
                {"label": "cadcore", "sqlite_runtime": value}, {"sqlite_runtime": value},
                "INDEPENDENT_RUNTIME_BINDING " + json.dumps(value), str(root), True),
                "SQLITE_PATH_LINKED")

    def test_legacy_extra_marker_is_not_ignored(self):
        report, files = self.changed_log(lambda raw: raw.replace(
            b"INDEPENDENT_CHILD_RC rc=0", b"INDEPENDENT_UNREVIEWED_SCOPE {}\nINDEPENDENT_CHILD_RC rc=0"))
        self.reject(lambda: self.all_review(report, files), "UNEXPECTED_CHILD_MARKER")

    def test_sqlite_nested_environment_receipt_is_bound_not_ignored_or_double_counted(self):
        with tempfile.TemporaryDirectory(prefix="gw-nested-", dir="/data/tmp") as directory:
            root = Path(directory)
            child = root / "child"
            child.mkdir()
            filesystem = subprocess.check_output(["stat", "-f", "-c", "%T", str(root)],
                                                 text=True).strip()
            raw = (b"class Fixture:\n"
                   b" def test_environment(self):\n"
                   b"  A.child([], A.ROOT, self.evidence, 'environment', self.root)\n")
            (root / "tools").mkdir()
            (root / "tools/test_sqlite_runtime_isolation.py").write_bytes(raw)
            sources = {"tools/test_sqlite_runtime_isolation.py": review.binding(raw)}
            counts = {"tests": 1, "passed": 1, "failures": 0, "errors": 0,
                      "skipped": [], "expected_failures": [], "unexpected_successes": [],
                      "successful": True}
            runtime = {"root": str(child), "TMPDIR": str(child), "tempfile": str(child),
                       "filesystem": filesystem, "sqlite_opens": 0, "sqlite_paths": {}, "rejected": []}
            environment = {"runtime": str(child), "TMPDIR": str(child), "tempfile": str(child),
                           "equal": True}
            log = ("OPENCAD_TEST_RUNTIME_ROOT=" + str(child) + "\n"
                   + "INDEPENDENT_TEST_COUNTS " + json.dumps(counts) + "\n"
                   + "ENVIRONMENT_BINDING " + json.dumps(environment) + "\n"
                   + "INDEPENDENT_RUNTIME_BINDING " + json.dumps(runtime) + "\n"
                   + "INDEPENDENT_CHILD_RC rc=0\n").encode()
            marker = "INDEPENDENT_STEP label=environment rc=0 counts=" + repr(counts) + "\n"
            def read(path):
                self.assertEqual(path, self.all_root / "test_environment/environment.log")
                return log
            result = review.sqlite_nested_steps(marker, root, sources, self.all_root, str(root), read)
            self.assertFalse(result[0]["counted_in_product_totals"])
            for text, code in (
                ("", "SQLITE_NESTED_MARKER_SET"),
                (marker + marker, "SQLITE_NESTED_MARKER_SET"),
                (marker.replace("rc=0", "rc=1"), "SQLITE_NESTED_MARKER"),
                (marker.replace("label=environment", "label=unknown"), "SQLITE_NESTED_MARKER"),
                (marker.replace("'passed': 1", "'passed': 0"), "SQLITE_NESTED_COUNTS"),
            ):
                self.reject(lambda t=text: review.sqlite_nested_steps(
                    t, root, sources, self.all_root, str(root), read), code)
            self.reject(lambda: review.sqlite_nested_steps(
                marker, root, sources, self.all_root, str(root),
                lambda path: log.replace(b"INDEPENDENT_CHILD_RC rc=0", b"INDEPENDENT_CHILD_RC rc=1")),
                "SQLITE_NESTED_EXIT")

    def test_actual_failed_six_stage_run_is_negative_not_partial_acceptance(self):
        failed = json.loads(self.read(self.failed_all_root / "result.json"))
        self.assertEqual((failed["verdict"], failed["actual_rc"], failed["stage"]), ("FAIL", 1, "all"))
        source = review.manifest(self.failed_source)
        self.assertEqual(source, failed["source_before"])
        self.assertEqual(source, failed["source_after"])
        self.assertEqual(failed["source_drift"], [])
        head = review.git(self.failed_source, ["rev-parse", "HEAD"]).decode().strip()
        self.assertEqual(failed["source_head_before"], head)
        self.assertEqual(failed["source_head_after"], head)
        self.assertEqual(failed["source_status_before"], "")
        self.assertEqual(failed["source_status_after"], "")
        plan, guarded, _ = review.all_stage_plan(
            self.failed_source, source, self.failed_all_root, failed["test_runtime"]["root"])
        self.assertTrue(guarded)
        self.assertEqual(len(plan), 6)
        self.assertEqual([row["label"] for row in failed["steps"]],
                         [row["label"] for row in plan[:-1]])
        self.assertEqual(plan[-1]["label"], "dxf")
        self.assertNotEqual(failed["steps"][-1]["rc"], 0)
        logs = {}
        for row in failed["steps"]:
            raw = self.read(self.failed_all_root / (row["label"] + ".log"))
            self.assertEqual(review.sha(raw), row["log_sha256"])
            logs[row["label"] + ".log"] = raw
        logs["acceptance.log"] = self.read(self.failed_all_root / "acceptance.log")
        self.reject(lambda: review.review_all(
            failed, logs, self.failed_source, self.failed_all_root, self.previous, self.read),
            "ALL_NOT_PASS")
        forged = deepcopy(failed)
        forged["verdict"] = "PASS"
        forged.pop("error")
        forged.pop("first_cause")
        self.reject(lambda: review.review_all(
            forged, logs, self.failed_source, self.failed_all_root, self.previous, self.read),
            "STAGE_PLAN_SET_MISMATCH")

    def test_complete_six_child_fixture_and_source_identity_negatives(self):
        # This is a reviewer fixture, never a new measured product all-stage.
        with tempfile.TemporaryDirectory(prefix="gw-six-", dir="/data/tmp") as directory:
            root = Path(directory)
            filesystem = subprocess.check_output(["stat", "-f", "-c", "%T", str(root)],
                                                 text=True).strip()
            acceptance = review.regular(self.sqlite_source / "tools/acceptance.py")
            files = {
                "tools/acceptance.py": acceptance,
                "tools/test_sqlite_runtime_isolation.py": b"import unittest\nclass Case(unittest.TestCase):\n def test_sqlite(self): pass\n",
                "tools/test_bootstrap.py": b"import unittest\nclass Case(unittest.TestCase):\n def test_bootstrap(self): pass\n",
                "oss-alternatives/apps/opencad/server.py": b"def _selftest_with_out_root(out):\n check('fixture.http', True)\n",
                "oss-alternatives/packages/cadcore/cadcore/__init__.py": b"# fixture\n",
            }
            for package in ("cadcore", "dxf"):
                files["oss-alternatives/packages/%s/tests/test_fixture.py" % package] = (
                    b"import unittest\nclass Case(unittest.TestCase):\n def test_fixture(self): pass\n")
            files["oss-alternatives/apps/opencad/tests/test_fixture.py"] = (
                b"import unittest\nclass Case(unittest.TestCase):\n def test_fixture(self): pass\n")
            for name, raw in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            def git(*args):
                result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                        env=review.git_env())
                self.assertEqual(result.returncode, 0)
                return result.stdout.decode().strip()
            git("init", "-q")
            git("add", *files)
            git("-c", "user.name=Reviewer Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "six-child reviewer fixture")
            head = git("rev-parse", "HEAD")
            sources = review.manifest(root)
            runtime = str(root)
            plan, guarded, _ = review.all_stage_plan(root, sources, self.all_root, runtime)
            self.assertTrue(guarded)
            origins = {"cadcore": "oss-alternatives/packages/cadcore/cadcore/__init__.py"}
            stamp = review.now()
            report = {"verdict": "PASS", "stage": "all", "at": stamp, "finished_at": stamp,
                      "source_before": sources, "source_after": sources, "source_drift": [],
                      "source_sha256": review.digest(sources),
                      "source_after_sha256": review.digest(sources),
                      "source_head_before": head, "source_head_after": head,
                      "source_status_before": "", "source_status_after": "", "actual_rc": 0,
                      "scan": {"files": len(sources), "source_sha256": review.digest(sources),
                               "credential_matches": 0, "loaded_cad_origins": origins,
                               "runtime_origins": {
                                   "server": "oss-alternatives/apps/opencad/server.py", **origins}},
                      "test_runtime": {"root": runtime, "filesystem": filesystem}, "steps": [],
                      "totals": dict(tests=5, passed=5, skipped=0, failures=0, errors=0, http_checks=1),
                      "production_deployment": "NOT_ASSESSED", "multi_tenant_readiness": "NOT_ASSESSED",
                      "industrial_parity": "NOT_ASSESSED"}
            logs = {"acceptance.log": (
                "INDEPENDENT_ACCEPTANCE verdict=PASS stage=all evidence=" + str(self.all_root)
            ).encode()}
            for planned in plan:
                label = planned["label"]
                observed = label in {"cadcore", "dxf"}
                runtime_row = {"root": runtime, "TMPDIR": runtime, "tempfile": runtime,
                               "filesystem": filesystem, "sqlite_opens": int(observed),
                               "sqlite_paths": {str(root / "observed.sqlite3"): filesystem} if observed else {},
                               "rejected": []}
                counts = None
                text = "OPENCAD_TEST_RUNTIME_ROOT=" + runtime + "\n"
                if label == "http-selftest":
                    text += "OPENCAD_SERVER selftest verdict=PASS checks=1 failed=0\n"
                    text += "INDEPENDENT_HTTP_BINDINGS " + json.dumps(origins) + "\n"
                else:
                    identities = review.static_test_inventory(root, label, sources)
                    self.assertEqual(len(identities), 1)
                    text += identities[0] + " ... ok\nRan 1 test in 0.001s\nOK\n"
                    counts = {"discovered": 1, "tests": 1, "passed": 1, "failures": 0,
                              "errors": 0, "skipped": [], "expected_failures": [],
                              "unexpected_successes": [], "successful": True,
                              "counts_reconciled": True, "loaded_cad_origins": origins,
                              "sqlite_runtime": runtime_row}
                    text += "INDEPENDENT_TEST_COUNTS " + json.dumps(counts) + "\n"
                text += "INDEPENDENT_RUNTIME_BINDING " + json.dumps(runtime_row) + "\n"
                text += "INDEPENDENT_CHILD_RC rc=0\n"
                raw = text.encode()
                logs[label + ".log"] = raw
                step = {**planned, "counts": counts, "sqlite_runtime": runtime_row,
                        "rc": 0, "log_sha256": review.sha(raw), "test_runtime_root": runtime}
                if counts is None:
                    step["http_checks"] = 1
                report["steps"].append(step)
            baseline = {"verdict": "PASS", "source_drift": [], "steps": []}
            positive = lambda r=report, f=logs: review.review_all(
                r, f, root, self.all_root, baseline)
            result = positive()
            self.assertEqual(result["totals"], report["totals"])
            self.assertEqual(len(result["steps"]), 6)
            for field, value in (("actual_rc", 1), ("source_head_after", "0" * 40),
                                 ("source_status_after", " M tools/acceptance.py\n"),
                                 ("source_after", {})):
                mutant = deepcopy(report)
                mutant[field] = value
                self.reject(lambda r=mutant: positive(r), "ALL_SOURCE_HEAD_STATUS_OR_RC")
            mutant = deepcopy(report)
            mutant["steps"].pop(0)
            mutant["totals"]["tests"] -= 1
            mutant["totals"]["passed"] -= 1
            self.reject(lambda: positive(mutant), "STAGE_PLAN_SET_MISMATCH")
            mutant, changed = deepcopy(report), dict(logs)
            first = mutant["steps"][0]
            changed[first["label"] + ".log"] = changed[first["label"] + ".log"].replace(
                b"INDEPENDENT_RUNTIME_BINDING ", b"INDEPENDENT_UNREVIEWED_RUNTIME ")
            first["log_sha256"] = review.sha(changed[first["label"] + ".log"])
            self.reject(lambda: positive(mutant, changed), "SQLITE_RUNTIME_MARKER_REQUIRED")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--all-evidence", type=Path, required=True)
    parser.add_argument("--baseline-evidence", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--original-failure", type=Path, required=True)
    parser.add_argument("--expect-head", required=True)
    parser.add_argument("--sqlite-source-root", type=Path, required=True)
    parser.add_argument("--failed-all-evidence", type=Path, required=True)
    parser.add_argument("--failed-source-root", type=Path, required=True)
    args = parser.parse_args()
    out = review.external(args.out)
    for path in (args.all_evidence, args.baseline_evidence, args.original_failure.parent,
                 args.failed_all_evidence):
        path = review.external(path)
        review.need(not out.resolve().is_relative_to(path.resolve())
                    and not path.resolve().is_relative_to(out.resolve()), "OUTPUT_OVERLAPS_INPUT")
    out.mkdir(parents=True, exist_ok=False)
    DynamicClosureTests.all_root = review.external(args.all_evidence)
    DynamicClosureTests.baseline = review.external(args.baseline_evidence)
    DynamicClosureTests.source = args.source_root.resolve()
    DynamicClosureTests.head = args.expect_head
    DynamicClosureTests.original_failure = args.original_failure
    DynamicClosureTests.sqlite_source = args.sqlite_source_root.resolve()
    DynamicClosureTests.failed_all_root = review.external(args.failed_all_evidence)
    DynamicClosureTests.failed_source = args.failed_source_root.resolve()
    started = review.now()
    before = review.manifest(DynamicClosureTests.source, modes=True)
    sqlite_before = review.manifest(DynamicClosureTests.sqlite_source, modes=True)
    failed_source_before = review.manifest(DynamicClosureTests.failed_source, modes=True)
    tool_before = {name: review.binding(review.regular(review.ROOT / name)) for name in review.OWNED}
    head_before = review.git(DynamicClosureTests.source, ["rev-parse", "HEAD"]).decode().strip()
    transcript = io.StringIO()
    result = unittest.TextTestRunner(stream=transcript, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DynamicClosureTests))
    with (out / "tests.log").open("x", encoding="utf-8") as log:
        log.write(transcript.getvalue())
    drift = [name for name, row in DynamicClosureTests.inputs.items()
             if review.binding(review.regular(Path(name))) != row]
    source_drift = review.difference(before, review.manifest(DynamicClosureTests.source, modes=True))
    sqlite_source_drift = review.difference(
        sqlite_before, review.manifest(DynamicClosureTests.sqlite_source, modes=True))
    failed_source_drift = review.difference(
        failed_source_before, review.manifest(DynamicClosureTests.failed_source, modes=True))
    tool_drift = [name for name, row in tool_before.items()
                  if review.binding(review.regular(review.ROOT / name)) != row]
    head_after = review.git(DynamicClosureTests.source, ["rev-parse", "HEAD"]).decode().strip()
    passed = (result.testsRun > 0 and result.wasSuccessful() and not result.skipped
              and not drift and not source_drift and not tool_drift
              and not sqlite_source_drift
              and not failed_source_drift
              and head_before == head_after == args.expect_head)
    report = {"verdict": "PASS" if passed else "FAIL", "actual_rc": 0 if passed else 1,
              "started_at": started, "finished_at": review.now(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
              "negative_controls": DynamicClosureTests.control_count, "input_drift": drift,
              "source_drift": source_drift, "tool_drift": tool_drift, "head": head_after,
              "sqlite_source_root": str(DynamicClosureTests.sqlite_source),
              "sqlite_source_sha256": review.digest(sqlite_before),
              "sqlite_source_drift": sqlite_source_drift,
              "failed_source_root": str(DynamicClosureTests.failed_source),
              "failed_source_sha256": review.digest(failed_source_before),
              "failed_source_drift": failed_source_drift,
              "inputs": DynamicClosureTests.inputs, "executor_files": tool_before,
              "test_log": review.binding(review.regular(out / "tests.log")),
              "product_tests_reexecuted": 0, "scope": "OFFLINE_REVIEWER_CONTROLS",
              "failures_detail": [{"test": str(test), "detail": detail}
                                  for test, detail in result.failures + result.errors]}
    review.save(out / "result.json", report)
    line = "DYNAMIC_CLOSURE_SELFTEST verdict=%s rc=%d tests=%d negatives=%d at=%s result=%s" % (
        report["verdict"], report["actual_rc"], report["tests"], report["negative_controls"],
        report["finished_at"], out / "result.json")
    with (out / "review.log").open("x") as log:
        log.write(line + "\n")
    print(line)
    return report["actual_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
