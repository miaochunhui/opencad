#!/usr/bin/env python3
"""Offline reviewer controls; fresh external evidence and only owned temporary fixtures.

No product tests, browser, HTTP, GPU, remote operations or original evidence writes.
Run directly with --out and --all-evidence to retain the test log and verdict.
"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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
    out = None
    cli_args = []

    def actor(self, name, mode, extra=()):
        out = self.out / name
        argv = [sys.executable, "-B", str(Path(__file__).resolve()),
                *self.cli_args, "--out", str(out), "--terminal-actor", mode, *extra]
        process = subprocess.run(argv, capture_output=True)
        (self.out / (name + ".stdout")).write_bytes(process.stdout)
        (self.out / (name + ".stderr")).write_bytes(process.stderr)
        path = out / "result.json"
        for fallback in ("terminal-failure.json", "terminal-output-failure.json"):
            if mode in {"result-save", "primary-save", "terminal-output"} \
                    and (out / fallback).exists():
                path = out / fallback
        saved = json.loads(review.regular(path)) if path.exists() else None
        review.save(self.out / (name + "-observation.json"),
                    {"argv": argv, "actual_rc": process.returncode, "result": saved,
                     "result_exists": path.exists(), "at": review.now(),
                     "stdout": review.binding(process.stdout),
                     "stderr": review.binding(process.stderr)})
        return process.returncode, saved

    def test_executor_preflight_unknown_cli_paths_are_refused(self):
        for name, statement, placement in (
                ("early-return", "return 0", "main"),
                ("early-exit", "if args.stage == 'all':\n raise SystemExit(0)", "try"),
                ("helper-return", "if unknown():\n return 0", "try"),
                ("stage-rebind", "args.stage = 'scan'", "try"),
                ("report-rebind", "report = {'verdict': 'PASS'}", "try"),
                ("prelude-return", "if args.stage == 'all':\n return 0", "main")):
            def mutate(main, loop, statement=statement, placement=placement):
                owner = next(node for node in main.body
                             if isinstance(node, ast.Try) and loop in node.body)
                target = main.body if placement == "main" else owner.body
                index = (main.body.index(owner) if placement == "main" else next(
                    i for i, node in enumerate(target) if ast.unparse(node) == "commands = []"))
                target.insert(index, ast.parse(statement).body[0])
            with self.subTest(name=name):
                root, _ = self.executor_source_mutant(name, mutate)
                rc, saved = self.actor(name + "-cli", "plan", ("--actor-source", str(root)))
                self.assertEqual(rc, 1)
                self.assertEqual(saved["first_cause"], "ALL_STAGE_EXECUTOR_REACHABILITY")
                type(self).control_count += 1

    def test_terminal_after_each_read_cli_is_fail_loud(self):
        for mode, phase, field in (
                ("source-after", "source-after", "source_after"),
                ("sqlite-after", "sqlite-source-after", "sqlite_source_after"),
                ("failed-after", "failed-source-after", "failed_source_after"),
                ("executor-head", "executor-head-after", "executor_head_after"),
                ("executor-status", "executor-status-after", "executor_status_after"),
                ("null-after", "executor-source-after", "executor_source_after"),
                ("digest-after", "executor-after-digest", "executor_after_sha256"),
                ("tests-log", "test-log-save-readback", "test_log"),
                ("result-save", "result-save", None),
                ("terminal-output", "terminal-output", None)):
            with self.subTest(mode=mode):
                rc, saved = self.actor("terminal-" + mode, mode)
                self.assertEqual(rc, 1)
                self.assertIsNotNone(saved)
                self.assertEqual(saved["actual_rc"], 1)
                self.assertEqual(saved["verdict"], "FAIL")
                self.assertEqual(saved["first_cause"]["phase"], phase)
                self.assertEqual((saved["tests"], saved["passed"]), (2, 2))
                self.assertTrue(saved["counts_complete"])
                if field is not None:
                    self.assertIsNone(saved[field])
                type(self).control_count += 1

    def test_terminal_partial_and_primary_save_cli_preserve_counts(self):
        for mode in ("partial-after", "primary-save"):
            with self.subTest(mode=mode):
                rc, saved = self.actor("terminal-" + mode, mode)
                self.assertEqual(rc, 1)
                self.assertIsNotNone(saved)
                if mode == "partial-after":
                    self.assertEqual(saved["tests"], 1)
                    self.assertFalse(saved["counts_complete"])
                    self.assertEqual(saved["first_cause"]["phase"], "test-run")
                    self.assertEqual(saved["secondary_failures"][0]["phase"],
                                     "executor-source-after")
                else:
                    self.assertEqual(saved["tests"], 2)
                    self.assertEqual(saved["failures"], 1)
                    self.assertEqual(saved["first_cause"]["phase"], "test-failure")
                    self.assertEqual(saved["secondary_failures"][0]["phase"], "result-save")
                if mode == "primary-save":
                    self.assertEqual(review.regular(
                        self.out / ("terminal-" + mode) / "result.json"), b"{")
                type(self).control_count += 1

    def test_executor_preinitialization_return_cli_is_refused(self):
        def mutate(main, loop):
            owner = next(node for node in main.body
                         if isinstance(node, ast.Try) and loop in node.body)
            begin = next(i for i, node in enumerate(owner.body)
                         if ast.unparse(node) == "commands = []")
            owner.body.insert(begin, ast.parse("if args.stage == 'all':\n return 0").body[0])
        root, sources = self.executor_source_mutant("preinit-return", mutate)
        rc, saved = self.actor("preinit-return-cli", "plan",
                               ("--actor-source", str(root)))
        self.assertEqual(rc, 1)
        self.assertEqual(saved["first_cause"], "ALL_STAGE_EXECUTOR_REACHABILITY")
        type(self).control_count += 1

    def test_executor_six_preinitialization_return_cli_is_refused(self):
        root = self.out / "six-preinit-return"
        (root / "tools").mkdir(parents=True)
        tree = ast.parse(review.regular(self.sqlite_source / "tools/acceptance.py"))
        owner = next(node for node in ast.walk(tree)
                     if isinstance(node, ast.Try) and any(
                         ast.unparse(child) == "commands = []" for child in node.body))
        index = next(i for i, node in enumerate(owner.body) if ast.unparse(node) == "commands = []")
        owner.body.insert(index, ast.parse("if args.stage == 'all':\n return 0").body[0])
        (root / "tools/acceptance.py").write_bytes(
            (ast.unparse(ast.fix_missing_locations(tree)) + "\n").encode())
        rc, saved = self.actor("six-preinit-return-cli", "plan", ("--actor-source", str(root)))
        self.assertEqual(rc, 1)
        self.assertEqual(saved["first_cause"], "ALL_STAGE_EXECUTOR_REACHABILITY")
        type(self).control_count += 1

    def test_terminal_executor_after_cli_retains_result(self):
        rc, saved = self.actor("terminal-executor-after", "executor-after")
        self.assertEqual(rc, 1)
        self.assertIsNotNone(saved, "completed tests lost their terminal result")
        self.assertEqual(saved["first_cause"]["phase"], "executor-source-after")
        self.assertEqual(saved["tests"], 2)
        self.assertTrue(saved["counts_complete"])
        self.assertIsNone(saved["executor_source_after"])
        self.assertIsNone(saved["executor_after_sha256"])
        self.assertIsNone(saved["executor_drift"])
        type(self).control_count += 1

    def test_terminal_primary_after_cli_retains_first_cause(self):
        rc, saved = self.actor("terminal-primary-after", "primary-after")
        self.assertEqual(rc, 1)
        self.assertIsNotNone(saved, "earliest test failure lost at source recheck")
        self.assertEqual(saved["first_cause"]["phase"], "test-failure")
        self.assertEqual(saved["failures"], 1)
        self.assertEqual(saved["tests"], 2)
        self.assertTrue(saved["counts_complete"])
        self.assertEqual(saved["secondary_failures"][0]["phase"], "executor-source-after")
        type(self).control_count += 1

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

    def p2_reject(self, name, function, code, fixture):
        def observed():
            try:
                value = function()
            except review.Refused as exc:
                review.save(self.out / (name + "-observation.json"),
                            {"observed": "REFUSED", "first_cause": str(exc),
                             "expected_prefix": code, "fixture": fixture, "at": review.now()})
                raise
            review.save(self.out / (name + "-observation.json"),
                        {"observed": "ACCEPTED_UNSAFE_MUTANT", "expected_prefix": code,
                         "fixture": fixture, "at": review.now(), "result": value})
            return value
        self.reject(observed, code)

    def executor_source_mutant(self, name, mutate):
        root = self.out / name
        (root / "tools").mkdir(parents=True)
        original = review.regular(self.source / "tools/acceptance.py")
        tree = ast.parse(original)
        main = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "main")
        loops = [node for node in ast.walk(main) if isinstance(node, ast.For)
                 and ast.unparse(node.iter) == "commands"]
        self.assertEqual(len(loops), 1)
        mutate(main, loops[0])
        raw = (ast.unparse(ast.fix_missing_locations(tree)) + "\n").encode()
        (root / "tools/acceptance.py").write_bytes(raw)
        sources = {"tools/acceptance.py": review.binding(raw)}
        return root, sources

    def executor_mutant(self, name, mutate):
        return self.executor_source_mutant(name, lambda main, loop: mutate(loop))

    def test_executor_missing_child_is_refused(self):
        root, sources = self.executor_mutant(
            "executor-omission", lambda loop: setattr(loop, "body", [ast.Pass()]))
        self.p2_reject("executor-omission", lambda: review.all_stage_plan(
            root, sources, self.all_root, self.report["test_runtime"]["root"]),
            "ALL_STAGE_EXECUTOR", {"root": str(root), "sources": sources})

    def test_executor_reordering_is_refused(self):
        root, sources = self.executor_mutant("executor-reorder", lambda loop: loop.body.insert(
            0, ast.Expr(value=ast.Call(func=ast.Attribute(
                value=ast.Name(id="commands", ctx=ast.Load()), attr="reverse", ctx=ast.Load()),
                args=[], keywords=[]))))
        self.p2_reject("executor-reorder", lambda: review.all_stage_plan(
            root, sources, self.all_root, self.report["test_runtime"]["root"]),
            "ALL_STAGE_EXECUTOR", {"root": str(root), "sources": sources})

    def test_executor_semantic_mutants_are_refused(self):
        def body(text):
            return lambda loop: setattr(loop, "body", ast.parse(text).body)

        mutations = (
            ("append-omitted", lambda loop: loop.body.pop(1)),
            ("append-before-child", lambda loop: loop.body.reverse()),
            ("wrong-child-argv", body(
                "result = child(argv[:1], cwd, out, label, runtime)\n"
                "report['steps'].append(result)\n"
                "if result['rc'] or result.get('marker_error'):\n"
                " raise RuntimeError(f'failed independent step: {label}')")),
            ("failure-break", lambda loop: setattr(loop.body[-1], "body", [ast.Break()])),
            ("failure-continue", lambda loop: setattr(loop.body[-1], "body", [ast.Continue()])),
            ("failure-ignored", lambda loop: setattr(loop.body[-1], "body", [ast.Pass()])),
            ("failure-inverted", lambda loop: setattr(
                loop.body[-1], "test", ast.UnaryOp(op=ast.Not(), operand=loop.body[-1].test))),
            ("wrong-append-result", lambda loop: setattr(
                loop.body[1].value, "args", [ast.Constant(value=None)])),
            ("duplicate-child", lambda loop: loop.body.insert(1, deepcopy(loop.body[0]))),
            ("subset-iterator", lambda loop: setattr(
                loop, "iter", ast.parse("commands[:1]", mode="eval").body)),
            ("reverse-iterator", lambda loop: setattr(
                loop, "iter", ast.parse("reversed(commands)", mode="eval").body)),
            ("wrong-target", lambda loop: setattr(loop, "target",
                ast.Tuple(elts=[ast.Name(id=name, ctx=ast.Store())
                               for name in ("cwd", "argv", "label")], ctx=ast.Store()))),
            ("loop-else", lambda loop: setattr(loop, "orelse", [ast.Pass()])),
            ("failure-else", lambda loop: setattr(loop.body[-1], "orelse", [ast.Pass()])),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                root, sources = self.executor_mutant("executor-" + name, mutate)
                self.p2_reject("executor-" + name, lambda: review.all_stage_plan(
                    root, sources, self.all_root, self.report["test_runtime"]["root"]),
                    "ALL_STAGE_EXECUTOR", {"root": str(root), "sources": sources})

    def test_executor_surrounding_control_flow_mutants_are_refused(self):
        def block(main, loop):
            return next(node for node in ast.walk(main)
                        if isinstance(node, ast.Try) and loop in node.body)

        def success_jump(main, loop):
            owner = block(main, loop)
            owner.body.insert(owner.body.index(loop) + 1, ast.Return(value=ast.Constant(value=0)))

        def outside_reorder(main, loop):
            main.body.append(ast.parse("commands.reverse()").body[0])

        def conditional_executor(main, loop):
            owner = block(main, loop)
            main.body[main.body.index(owner)] = ast.If(
                test=ast.Name(id="unknown_runtime_selection", ctx=ast.Load()),
                body=[owner], orelse=[])

        for name, mutate in (("success-jump", success_jump), ("outside-reorder", outside_reorder),
                             ("conditional-execution", conditional_executor)):
            with self.subTest(name=name):
                root, sources = self.executor_source_mutant("executor-" + name, mutate)
                self.p2_reject("executor-" + name, lambda: review.all_stage_plan(
                    root, sources, self.all_root, self.report["test_runtime"]["root"]),
                    "ALL_STAGE_EXECUTOR", {"root": str(root), "sources": sources})

    def test_executor_loop_rejects_runtime_failure_gate_removal(self):
        root = self.out / "executor-six-runtime"
        (root / "tools").mkdir(parents=True)
        raw = review.regular(self.sqlite_source / "tools/acceptance.py")
        tree = ast.parse(raw)
        loop = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.For) and ast.unparse(node.iter) == "commands")
        self.assertEqual(ast.unparse(loop.body[-1].test),
                         "result['rc'] or result.get('marker_error') or result.get('runtime_error')")
        loop.body[-1].test.values.pop()
        changed = (ast.unparse(ast.fix_missing_locations(tree)) + "\n").encode()
        (root / "tools/acceptance.py").write_bytes(changed)
        sources = {"tools/acceptance.py": review.binding(changed)}
        self.p2_reject("executor-six-runtime", lambda: review.all_stage_plan(
            root, sources, self.all_root, "/data/tmp/fixture-plan-runtime"),
            "ALL_STAGE_EXECUTOR", {"root": str(root), "sources": sources})

    def test_executor_six_child_first_cause_required(self):
        root = self.out / "executor-six-first-cause"
        (root / "tools").mkdir(parents=True)
        tree = ast.parse(review.regular(self.sqlite_source / "tools/acceptance.py"))
        loop = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.For) and ast.unparse(node.iter) == "commands")
        self.assertEqual(ast.unparse(loop.body[-1].body[0]),
                         "report['first_cause'] = dict(result)")
        loop.body[-1].body.pop(0)
        raw = (ast.unparse(ast.fix_missing_locations(tree)) + "\n").encode()
        (root / "tools/acceptance.py").write_bytes(raw)
        sources = {"tools/acceptance.py": review.binding(raw)}
        self.p2_reject("executor-six-first-cause", lambda: review.all_stage_plan(
            root, sources, self.all_root, "/data/tmp/fixture-plan-runtime"),
            "ALL_STAGE_EXECUTOR", {"root": str(root), "sources": sources})

    def doc_history_mutant(self, name, mutation):
        root = self.out / name
        (root / "docs").mkdir(parents=True)
        path = root / "docs/note.md"
        path.write_bytes(b"before\n")

        def git(*args):
            return review.git(root, list(args)).decode().strip()

        def commit(message):
            git("-c", "user.name=Reviewer Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "core.hooksPath=/dev/null", "commit", "-qm", message)
            return git("rev-parse", "HEAD")

        git("init", "-q")
        git("add", "--", "docs/note.md")
        base = commit("base")
        if mutation == "delete":
            git("update-index", "--force-remove", "--", "docs/note.md")
        elif mutation == "mode":
            git("update-index", "--chmod=+x", "--", "docs/note.md")
        else:
            path.write_bytes(b"middle\n")
            git("add", "--", "docs/note.md")
        middle = commit(mutation)
        path.write_bytes(b"after\n")
        git("add", "--", "docs/note.md")
        if mutation == "mode":
            git("update-index", "--chmod=-x", "--", "docs/note.md")
        tip = commit("restore regular document")
        frozen = review.committed_manifest(root, base, modes=True)
        current = review.committed_manifest(root, tip, modes=True)
        self.assertEqual(current["docs/note.md"]["mode"], "100644")
        fixture = {"root": str(root), "base": base, "middle": middle, "tip": tip}
        return root, base, tip, frozen, current, fixture

    def test_doc_delete_restore_is_refused(self):
        root, base, tip, frozen, current, fixture = self.doc_history_mutant("doc-delete", "delete")
        self.p2_reject("doc-delete", lambda: review.doc_successor(
            root, base, tip, frozen, current, ["docs/note.md"]), "DOC_INTERMEDIATE", fixture)

    def test_doc_mode_restore_is_refused(self):
        root, base, tip, frozen, current, fixture = self.doc_history_mutant("doc-mode", "mode")
        self.p2_reject("doc-mode", lambda: review.doc_successor(
            root, base, tip, frozen, current, ["docs/note.md"]), "DOC_INTERMEDIATE", fixture)

    def test_doc_regular_multi_commit_tail_is_accepted(self):
        root, base, tip, frozen, current, fixture = self.doc_history_mutant(
            "doc-regular", "regular")
        result = review.doc_successor(root, base, tip, frozen, current, ["docs/note.md"])
        self.assertEqual(result["tail"], [fixture["middle"], tip])
        self.assertEqual(len(result["commit_changes"]), 2)
        for item in result["commit_changes"]:
            self.assertEqual(item["changes"][0]["status"], "M")
            self.assertEqual(item["changes"][0]["old_mode"], "100644")
            self.assertEqual(item["changes"][0]["new_mode"], "100644")
        self.assertFalse(result["whole_source_all_accepted"])

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


def record_failure(report, phase, exc, test=None):
    cause = {"phase": phase, "type": type(exc).__name__, "detail": str(exc)}
    if test is not None:
        cause["test"] = str(test)
    if report["first_cause"] is None:
        report["first_cause"] = cause
    else:
        report["secondary_failures"].append(cause)
    report.update(verdict="FAIL", actual_rc=1)


class ObservedResult(unittest.TextTestResult):
    def __init__(self, *args, report, **kwargs):
        super().__init__(*args, **kwargs)
        self.report = report
        self.passed = 0

    def addSuccess(self, test):
        self.passed += 1
        super().addSuccess(test)

    def addFailure(self, test, err):
        record_failure(self.report, "test-failure", err[1], test)
        super().addFailure(test, err)

    def addError(self, test, err):
        record_failure(self.report, "test-error", err[1], test)
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            record_failure(self.report, "test-subtest", err[1], subtest)
        super().addSubTest(test, subtest, err)

    def addSkip(self, test, reason):
        record_failure(self.report, "test-skip", RuntimeError(reason), test)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        record_failure(self.report, "test-expected-failure", err[1], test)
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        record_failure(self.report, "test-unexpected-success", RuntimeError(
            "unexpected success"), test)
        super().addUnexpectedSuccess(test)


class ObservedRunner(unittest.TextTestRunner):
    def __init__(self, *args, report, **kwargs):
        super().__init__(*args, **kwargs)
        self.report = report
        self.observed = None

    def _makeResult(self):
        self.observed = ObservedResult(
            self.stream, self.descriptions, self.verbosity, report=self.report)
        return self.observed


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
    parser.add_argument("--only", action="append",
                        help="exact test method; new evidence still retains all input/source checks")
    args = parser.parse_args()
    DynamicClosureTests.cli_args = [
        "--all-evidence", str(args.all_evidence), "--baseline-evidence", str(args.baseline_evidence),
        "--source-root", str(args.source_root), "--original-failure", str(args.original_failure),
        "--expect-head", args.expect_head, "--sqlite-source-root", str(args.sqlite_source_root),
        "--failed-all-evidence", str(args.failed_all_evidence),
        "--failed-source-root", str(args.failed_source_root)]
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
    DynamicClosureTests.out = out
    DynamicClosureTests.control_count = 0
    DynamicClosureTests.inputs = {}
    report = {"verdict": "FAIL", "actual_rc": 1, "started_at": review.now(),
              "first_cause": None, "secondary_failures": [], "tests": 0, "passed": 0,
              "failures": 0, "errors": 0, "skips": 0, "negative_controls": 0,
              "discovered": None, "counts_complete": False, "selected_tests": args.only or [],
              "inputs": DynamicClosureTests.inputs, "failures_detail": [],
              "product_tests_reexecuted": 0, "scope": "OFFLINE_REVIEWER_CONTROLS",
              "source_root": str(DynamicClosureTests.source),
              "sqlite_source_root": str(DynamicClosureTests.sqlite_source),
              "failed_source_root": str(DynamicClosureTests.failed_source)}
    fields = ("source_before", "source_after", "source_drift", "tool_drift", "head",
              "sqlite_source_before", "sqlite_source_after", "sqlite_source_sha256",
              "sqlite_source_drift", "failed_source_before", "failed_source_after",
              "failed_source_sha256", "failed_source_drift", "input_drift",
              "executor_files", "executor_source_before", "executor_source_after",
              "executor_source_sha256", "executor_after_sha256", "executor_drift",
              "executor_head_before", "executor_head_after", "executor_status_before",
              "executor_status_after", "source_head_before", "test_log", "ownership")
    report.update(dict.fromkeys(fields))

    def capture(field, phase, function):
        try:
            value = function()
            if value is None:
                raise ValueError("required observation returned null")
            report[field] = value
            return True
        except Exception as exc:
            record_failure(report, phase, exc)
            return False

    def tools():
        return {name: review.binding(review.regular(review.ROOT / name)) for name in review.OWNED}

    def head(root):
        return review.git(root, ["rev-parse", "HEAD"]).decode().strip()

    def status():
        return review.git(review.ROOT,
                          ["status", "--porcelain=v1", "--untracked-files=all"]).decode()

    for field, phase, function in (
            ("ownership", "executor-ownership-secret-scan", lambda: review.review_owned(review.ROOT)),
            ("source_before", "source-before", lambda: review.manifest(
                DynamicClosureTests.source, modes=True)),
            ("sqlite_source_before", "sqlite-source-before", lambda: review.manifest(
                DynamicClosureTests.sqlite_source, modes=True)),
            ("failed_source_before", "failed-source-before", lambda: review.manifest(
                DynamicClosureTests.failed_source, modes=True)),
            ("executor_files", "executor-tools-before", tools),
            ("executor_source_before", "executor-source-before", lambda: review.manifest(
                review.ROOT, modes=True)),
            ("executor_head_before", "executor-head-before", lambda: head(review.ROOT)),
            ("executor_status_before", "executor-status-before", status),
            ("source_head_before", "source-head-before", lambda: head(DynamicClosureTests.source))):
        if not capture(field, phase, function):
            break
    transcript = io.StringIO()
    runner = ObservedRunner(stream=transcript, verbosity=2, report=report)
    if report["first_cause"] is None:
        try:
            suite = (unittest.TestSuite(DynamicClosureTests(name) for name in args.only)
                     if args.only else unittest.defaultTestLoader.loadTestsFromTestCase(
                         DynamicClosureTests))
            report["discovered"] = suite.countTestCases()
            if report["discovered"] == 0:
                raise ValueError("empty reviewer test suite")
            runner.run(suite)
            report["counts_complete"] = (
                runner.observed.testsRun == report["discovered"])
            if not report["counts_complete"]:
                raise ValueError("incomplete reviewer test suite")
        except Exception as exc:
            record_failure(report, "test-run", exc)
    result = runner.observed
    if result is not None:
        report.update(tests=result.testsRun, passed=result.passed,
                      failures=len(result.failures), errors=len(result.errors),
                      skips=len(result.skipped),
                      failures_detail=[{"test": str(test), "detail": detail}
                                       for test, detail in result.failures + result.errors])
    report["negative_controls"] = DynamicClosureTests.control_count
    capture("input_drift", "inputs-after", lambda: [
        name for name, row in DynamicClosureTests.inputs.items()
        if review.binding(review.regular(Path(name))) != row])
    for field, phase, function in (
            ("source_after", "source-after", lambda: review.manifest(
                DynamicClosureTests.source, modes=True)),
            ("sqlite_source_after", "sqlite-source-after", lambda: review.manifest(
                DynamicClosureTests.sqlite_source, modes=True)),
            ("failed_source_after", "failed-source-after", lambda: review.manifest(
                DynamicClosureTests.failed_source, modes=True)),
            ("executor_source_after", "executor-source-after", lambda: review.manifest(
                review.ROOT, modes=True)),
            ("executor_head_after", "executor-head-after", lambda: head(review.ROOT)),
            ("executor_status_after", "executor-status-after", status),
            ("head", "source-head-after", lambda: head(DynamicClosureTests.source))):
        capture(field, phase, function)
    for prefix in ("source", "sqlite_source", "failed_source", "executor_source"):
        before, after = report[prefix + "_before"], report[prefix + "_after"]
        drift_field = "executor_drift" if prefix == "executor_source" else prefix + "_drift"
        if before is not None and after is not None:
            capture(drift_field, prefix + "-difference",
                    lambda b=before, a=after: review.difference(b, a))
        digest_field = ("executor_source_sha256" if prefix == "executor_source"
                        else prefix + "_sha256")
        if before is not None:
            capture(digest_field, prefix + "-digest", lambda b=before: review.digest(b))
    if report["executor_source_after"] is not None:
        capture("executor_after_sha256", "executor-after-digest",
                lambda: review.digest(report["executor_source_after"]))
    if report["executor_files"] is not None:
        capture("tool_drift", "executor-tools-after", lambda: [
            name for name, row in report["executor_files"].items()
            if review.binding(review.regular(review.ROOT / name)) != row])
    for field in ("input_drift", "source_drift", "sqlite_source_drift",
                  "failed_source_drift", "executor_drift", "tool_drift"):
        if report[field]:
            record_failure(report, field, ValueError("source or input drift"))
    if (report["source_head_before"] != report["head"] or report["head"] != args.expect_head
            or report["executor_head_before"] != report["executor_head_after"]
            or report["executor_status_before"] != report["executor_status_after"]):
        record_failure(report, "head-status-binding", ValueError("HEAD or status drift"))

    def save_log():
        with (out / "tests.log").open("x", encoding="utf-8") as log:
            log.write(transcript.getvalue())
            log.flush()
            os.fsync(log.fileno())
        return review.binding(review.regular(out / "tests.log"))

    capture("test_log", "test-log-save-readback", save_log)
    if report["first_cause"] is None and report["counts_complete"] and report["tests"] > 0:
        report.update(verdict="PASS", actual_rc=0)
    report["finished_at"] = review.now()
    line = "DYNAMIC_CLOSURE_SELFTEST verdict=%s rc=%d tests=%d negatives=%d at=%s result=%s" % (
        report["verdict"], report["actual_rc"], report["tests"], report["negative_controls"],
        report["finished_at"], out / "result.json")
    try:
        with (out / "candidate.log").open("x") as log:
            log.write(line + "\n")
            log.flush()
            os.fsync(log.fileno())
    except Exception as exc:
        record_failure(report, "candidate-log-save", exc)
    try:
        review.save(out / "result.json", report)
    except Exception as exc:
        record_failure(report, "result-save", exc)
        try:
            review.save(out / "terminal-failure.json", report)
        except Exception as later:
            record_failure(report, "terminal-failure-save", later)
            # No successful exit is possible even if the evidence medium fails.
    line = "DYNAMIC_CLOSURE_SELFTEST verdict=%s rc=%d tests=%d negatives=%d at=%s result=%s" % (
        report["verdict"], report["actual_rc"], report["tests"], report["negative_controls"],
        report["finished_at"], out / "result.json")
    try:
        with (out / "review.log").open("x") as log:
            log.write(line + "\n")
            log.flush()
            os.fsync(log.fileno())
    except Exception as exc:
        record_failure(report, "review-log-save", exc)
        try:
            review.save(out / "terminal-log-failure.json", report)
        except Exception as later:
            record_failure(report, "terminal-log-failure-save", later)
    try:
        print("DYNAMIC_CLOSURE_SELFTEST verdict=%s rc=%d tests=%d negatives=%d at=%s result=%s" % (
            report["verdict"], report["actual_rc"], report["tests"], report["negative_controls"],
            report["finished_at"], out / "result.json"), flush=True)
    except Exception as exc:
        record_failure(report, "terminal-output", exc)
        try:
            review.save(out / "terminal-output-failure.json", report)
        except Exception as later:
            record_failure(report, "terminal-output-failure-save", later)
    return report["actual_rc"]


def terminal_actor():
    """Offline fault actors run the real runner; they never invoke product tests."""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--terminal-actor", required=True,
                        choices=("plan", "executor-after", "primary-after", "source-after",
                                 "sqlite-after", "failed-after", "executor-head", "executor-status",
                                 "null-after", "digest-after", "tests-log", "result-save",
                                 "primary-save", "terminal-output", "partial-after"))
    parser.add_argument("--actor-source", type=Path)
    actor, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    if actor.terminal_actor == "plan":
        out_parser = argparse.ArgumentParser()
        out_parser.add_argument("--out", type=Path, required=True)
        out_args, _ = out_parser.parse_known_args()
        out = review.external(out_args.out)
        out.mkdir(parents=True, exist_ok=False)
        raw = review.regular(actor.actor_source / "tools/acceptance.py")
        report = {"actual_rc": 0, "first_cause": None, "at": review.now(),
                  "source": str(actor.actor_source), "source_binding": review.binding(raw)}
        try:
            report["plan"] = review.all_stage_plan(
                actor.actor_source, {"tools/acceptance.py": review.binding(raw)},
                out, "/data/tmp/offline-plan-fixture")[0]
        except review.Refused as exc:
            report.update(actual_rc=1, first_cause=str(exc))
        review.save(out / "result.json", report)
        print(json.dumps(report, sort_keys=True))
        return report["actual_rc"]

    class Witness(unittest.TestCase):
        def test_first(self):
            if actor.terminal_actor in {"primary-after", "primary-save"}:
                self.fail("ACTOR_PRIMARY_FAILURE")

        def test_second(self):
            self.assertEqual(1, 1)

    original = review.manifest
    root_reads = 0
    reads = {}

    def manifest(root, *args, **kwargs):
        nonlocal root_reads
        key = str(root)
        reads[key] = reads.get(key, 0) + 1
        targets = {"source-after": DynamicClosureTests.source,
                   "sqlite-after": DynamicClosureTests.sqlite_source,
                   "failed-after": DynamicClosureTests.failed_source}
        target = targets.get(actor.terminal_actor)
        if target is not None and Path(root) == target and reads[key] == 2:
            raise OSError("ACTOR_" + actor.terminal_actor)
        if Path(root) == review.ROOT:
            root_reads += 1
            if root_reads == 2:
                if actor.terminal_actor in {"executor-after", "primary-after", "partial-after"}:
                    raise OSError("ACTOR_EXECUTOR_AFTER")
                if actor.terminal_actor == "null-after":
                    return None
        return original(root, *args, **kwargs)

    def suite(_):
        return unittest.TestSuite((Witness("test_first"), Witness("test_second")))

    git_original = review.git
    git_reads = {}

    def git(root, args):
        key = (str(root), tuple(args))
        git_reads[key] = git_reads.get(key, 0) + 1
        if root == review.ROOT and root_reads >= 2 and (
                actor.terminal_actor == "executor-head" and args[0] == "rev-parse"
                or actor.terminal_actor == "executor-status" and args[0] == "status"):
            raise OSError("ACTOR_" + actor.terminal_actor)
        return git_original(root, args)

    regular_original, save_original = review.regular, review.save
    digest_original = review.digest

    def regular(path):
        if actor.terminal_actor == "tests-log" and path.name == "tests.log":
            raise OSError("ACTOR_TEST_LOG_READ")
        return regular_original(path)

    def save(path, value):
        if actor.terminal_actor in {"result-save", "primary-save"} and path.name == "result.json":
            with path.open("x") as stream:
                stream.write("{")
            raise OSError("ACTOR_PARTIAL_RESULT_SAVE")
        return save_original(path, value)

    digest_reads = 0

    def digest(value):
        nonlocal digest_reads
        digest_reads += 1
        if actor.terminal_actor == "digest-after" and digest_reads == 5:
            raise OSError("ACTOR_AFTER_DIGEST")
        return digest_original(value)

    original_run = ObservedRunner.run

    def run(runner, tests):
        if actor.terminal_actor == "partial-after":
            original_run(runner, unittest.TestSuite([Witness("test_second")]))
            raise RuntimeError("ACTOR_PARTIAL_RUN")
        return original_run(runner, tests)

    original_print = print

    def output(*args, **kwargs):
        if actor.terminal_actor == "terminal-output":
            raise OSError("ACTOR_OUTPUT_FAILURE")
        return original_print(*args, **kwargs)

    with mock.patch.object(review, "manifest", manifest), mock.patch.object(
            unittest.defaultTestLoader, "loadTestsFromTestCase", suite), mock.patch.object(
            review, "git", git), mock.patch.object(review, "regular", regular), mock.patch.object(
            review, "save", save), mock.patch.object(review, "digest", digest), mock.patch.object(
            ObservedRunner, "run", run), mock.patch("builtins.print", output):
        return main()


def verify_run():
    """Read an immutable selftest ledger against this exact clean executor."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--verify-run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence, out = review.external(args.verify_run), review.external(args.out)
    review.need(not out.resolve().is_relative_to(evidence.resolve())
                and not evidence.resolve().is_relative_to(out.resolve()), "OUTPUT_OVERLAPS_INPUT")
    out.mkdir(parents=True, exist_ok=False)
    report = {"verdict": "FAIL", "actual_rc": 1, "at": review.now(), "checks": [],
              "input": str(evidence), "product_tests_reexecuted": 0}
    inputs = {}

    def read(path):
        raw = review.regular(path)
        inputs[str(path)] = review.binding(raw)
        return raw

    try:
        review.need(not any((evidence / name).exists() for name in (
            "terminal-failure.json", "terminal-log-failure.json", "terminal-output-failure.json")),
            "SELFTEST_TERMINAL_FAILURE")
        saved = json.loads(read(evidence / "result.json"))
        review.need(saved["verdict"] == "PASS" and saved["actual_rc"] == 0
                    and saved["first_cause"] is None and saved["secondary_failures"] == []
                    and saved["counts_complete"] is True and saved["tests"] > 0
                    and saved["failures"] == saved["errors"] == saved["skips"] == 0,
                    "SELFTEST_NOT_COMPLETE_PASS")
        report["checks"].append("terminal")
        terminal = read(evidence / "review.log").decode().strip()
        review.need(terminal == "DYNAMIC_CLOSURE_SELFTEST verdict=PASS rc=0 tests=%d negatives=%d at=%s result=%s" % (
            saved["tests"], saved["negative_controls"], saved["finished_at"],
            evidence / "result.json"), "SELFTEST_TERMINAL_LINE")
        raw = read(evidence / "tests.log")
        review.need(review.binding(raw) == saved["test_log"], "SELFTEST_LOG_HASH")
        text = raw.decode()
        rows = review.re.findall(
            r"^(test_\w+) \(__main__\.DynamicClosureTests\) \.\.\. ok$", text, review.re.M)
        expected = saved["selected_tests"] or sorted(
            name for name in vars(DynamicClosureTests) if name.startswith("test_"))
        review.need(sorted(rows) == sorted(expected) and len(rows) == len(set(rows))
                    and len(rows) == saved["tests"] == saved["discovered"] == saved["passed"]
                    and review.re.findall(r"^Ran (\d+) tests in [\d.]+s$", text, review.re.M)
                    == [str(saved["tests"])] and text.rstrip().endswith("OK"),
                    "SELFTEST_REQUIRED_LABELS_COUNTS")
        report["checks"].append("test-labels-counts")
        current = review.manifest(review.ROOT, modes=True)
        head = review.git(review.ROOT, ["rev-parse", "HEAD"]).decode().strip()
        status = review.git(review.ROOT,
                            ["status", "--porcelain=v1", "--untracked-files=all"]).decode()
        review.need(current == saved["executor_source_before"] == saved["executor_source_after"]
                    and review.digest(current) == saved["executor_source_sha256"]
                    == saved["executor_after_sha256"]
                    and head == saved["executor_head_before"] == saved["executor_head_after"]
                    and status == saved["executor_status_before"]
                    == saved["executor_status_after"] == "", "SELFTEST_EXECUTOR_FREEZE")
        report["checks"].append("executor-freeze")
        ownership = review.review_owned(review.ROOT)
        review.need(ownership["secret_shape_matches"] == 0
                    and ownership["files"] == saved["executor_files"], "SELFTEST_OWNERSHIP")
        report["ownership"] = ownership
        for prefix in ("source", "sqlite_source", "failed_source"):
            root = Path(saved[prefix + "_root"])
            current_source = review.manifest(root, modes=True)
            review.need(current_source == saved[prefix + "_before"] == saved[prefix + "_after"]
                        and saved[prefix + "_drift"] == [], "SELFTEST_SOURCE_FREEZE:" + prefix)
        for name, expected_binding in saved["inputs"].items():
            review.need(review.binding(read(Path(name))) == expected_binding, "SELFTEST_INPUT_DRIFT")
        review.need(saved["input_drift"] == saved["executor_drift"] == saved["tool_drift"] == [],
                    "SELFTEST_RECORDED_DRIFT")
        report["checks"].append("source-input-closure")
        for name, expected_hash in (
                ("/hf3fs-jd/data/tmp/opencad-dynamic-reviewer-p2-committed-20261012-v1/result.json",
                 "01f037866628231778d53f54b3e7bce88346f25144d880fad849d846f5432122"),
                ("/hf3fs-jd/data/tmp/opencad-dynamic-reviewer-aquinas-second-review-20261012-v1/result.json",
                 "5e25960fac852d4473896b597b18c1ee4f6ab1ca0039713ad6cf318bc74bba5b"),
                ("/hf3fs-jd/data/tmp/opencad-dynamic-reviewer-reachability-terminal-before-20261012-v1/result.json",
                 "b566a8719f7eaf1c1a8e21b276939bf8bbe02e91c74a3cec129dfb0492a7f119")):
            review.need(review.sha(read(Path(name))) == expected_hash, "ORIGINAL_LEDGER_CHANGED")
        report["checks"].append("original-ledgers-retained")
        report.update(verdict="PASS", actual_rc=0, tests=saved["tests"],
                      negative_controls=saved["negative_controls"], head=head,
                      source_sha256=review.digest(current), finished_at=saved["finished_at"],
                      test_log=saved["test_log"])
    except Exception as exc:
        report["first_cause"] = str(exc) if isinstance(exc, review.Refused) else type(exc).__name__
    report["inputs"] = inputs
    report["checked_at"] = review.now()
    review.save(out / "result.json", report)
    print("DYNAMIC_CLOSURE_READBACK verdict=%s rc=%d checks=%d at=%s result=%s" % (
        report["verdict"], report["actual_rc"], len(report["checks"]), report["checked_at"],
        out / "result.json"), flush=True)
    return report["actual_rc"]


if __name__ == "__main__":
    raise SystemExit(verify_run() if "--verify-run" in sys.argv else
                     terminal_actor() if "--terminal-actor" in sys.argv else main())
