#!/usr/bin/env python3
"""Bootstrap/acceptance negative controls. Fixtures and results live on 3FS."""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bootstrap_from_infra as bootstrap


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            prefix="negative-", dir="/hf3fs-jd/data/tmp/opencad-bootstrap-s7")
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.source.mkdir()
        self.target.mkdir()
        self.name = "oss-alternatives/apps/opencad/server.py"
        bootstrap.exclusive_bytes(self.source / self.name, b"VALUE = 1\n")
        license_path = self.root / "apache.txt"
        bootstrap.exclusive_bytes(license_path, b"fixture license\n")
        value = (self.source / self.name).read_bytes()
        files = {self.name: {"sha256": bootstrap.sha_bytes(value), "bytes": len(value)}}
        self.plan = {"schema": 1, "source_root": str(self.source), "files": files,
                     "files_sha256": bootstrap.digest(files),
                     "license": {"source": str(license_path),
                                 "sha256": bootstrap.sha_bytes(license_path.read_bytes())}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_copy_and_unrelated_chair_file_preserved(self):
        chair = self.target / "oss-alternatives/apps/opencad/secure_web.py"
        bootstrap.exclusive_bytes(chair, b"CHAIR = True\n")
        report = bootstrap.apply(self.plan, self.target, self.root / "evidence")
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(bootstrap.verify_files(self.target, self.plan["files"]), [])
        self.assertEqual(chair.read_bytes(), b"CHAIR = True\n")
        self.assertEqual((self.target / bootstrap.PROVENANCE).read_text(),
                         (self.root / "evidence/frozen-plan.json").read_text())

    def test_identical_existing_target_rejected(self):
        bootstrap.exclusive_bytes(self.target / self.name, b"VALUE = 1\n")
        with self.assertRaises(FileExistsError):
            bootstrap.apply(self.plan, self.target, self.root / "evidence")
        self.assertFalse((self.root / "evidence").exists())

    def test_changed_source_rejected_before_copy(self):
        # Test-only mutation of this fixture, never a repository source file.
        (self.source / self.name).write_bytes(b"VALUE = 2\n")
        with self.assertRaisesRegex(ValueError, "source changed after freeze"):
            bootstrap.apply(self.plan, self.target, self.root / "evidence")
        self.assertFalse((self.target / self.name).exists())

    def test_dangling_link_target_rejected(self):
        (self.target / "oss-alternatives/apps/opencad").mkdir(parents=True)
        (self.target / self.name).symlink_to("missing")
        with self.assertRaises(FileExistsError):
            bootstrap.apply(self.plan, self.target, self.root / "evidence")

    def test_linked_parent_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "oss-alternatives").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink destination parent"):
            bootstrap.apply(self.plan, self.target, self.root / "evidence")
        self.assertFalse((outside / "apps/opencad/server.py").exists())
        self.assertEqual(json.loads((self.root / "evidence/failure.json").read_text())["verdict"], "FAIL")

    def test_partial_copy_failure_keeps_first_cause(self):
        with patch.object(bootstrap, "exclusive_bytes", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                bootstrap.apply(self.plan, self.target, self.root / "evidence")

    def test_empty_plan_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            bootstrap.validate_plan({"schema": 1, "files": {}})

    def test_outside_product_plan_rejected(self):
        files = {"oss-alternatives/apps/freechat/server.py":
                 {"sha256": "0" * 64, "bytes": 1}}
        with self.assertRaisesRegex(ValueError, "outside CAD"):
            bootstrap.validate_plan({"schema": 1, "files": files,
                                     "files_sha256": bootstrap.digest(files)})

    def test_plan_digest_tamper_rejected(self):
        self.plan["files"][self.name]["bytes"] += 1
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            bootstrap.validate_plan(self.plan)

    def test_source_symlink_rejected(self):
        link = self.root / "linked.py"
        link.symlink_to(self.source / self.name)
        with self.assertRaisesRegex(ValueError, "regular source"):
            bootstrap.regular_bytes(link)

    def test_secret_scanner_sensitivity_without_value_echo(self):
        fixture = b"api_" + b"key = '" + b"x" * 9 + b"'\n"
        with self.assertRaisesRegex(ValueError, "^credential-shaped literal: fixture.py$"):
            bootstrap.scan_bytes("fixture.py", fixture)
        bootstrap.scan_bytes("clean.py", b"VARIABLE_NAME = 'OPENCAD_OUT_ROOT'\n")

    def test_empty_discovery_rejected(self):
        import acceptance
        tests = self.root / "suite"
        tests.mkdir()
        bootstrap.exclusive_bytes(tests / "__init__.py", b"")
        with self.assertRaisesRegex(ValueError, "empty unittest"):
            acceptance.run_suite(tests, "absent*.py", self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
