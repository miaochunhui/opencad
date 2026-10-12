"""Check the shipped panel catalog, including real-data parametric workflows.

The documented consumer request is an independent key/placeholder oracle,
not a replacement runtime dictionary. Node controls consume the shipped JSON.
Runtime fixtures stay on local storage; immutable evidence belongs to the
parent acceptance runner's external OPENCAD_EVIDENCE_ROOT.
"""

import argparse
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[2]
CATALOG = APP / "web/panel-i18n.json"
PARAMETRIC_DOC = APP / "docs/design/parametric-panel-locale.md"
PREFIX = "panel.parametric."
PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")


def require(value, code):
    if not value:
        raise ValueError(code)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "catalog_duplicate_key:" + key)
        result[key] = value
    return result


def read_catalog(text=None):
    return json.loads(CATALOG.read_text() if text is None else text,
                      object_pairs_hook=unique)


def names(value):
    require(isinstance(value, str) and value.strip(), "catalog_empty_translation")
    require(not re.search(r"[{}]", PLACEHOLDER.sub("", value)),
            "catalog_invalid_placeholder")
    return set(PLACEHOLDER.findall(value))


def request(doc=PARAMETRIC_DOC, prefix=PREFIX):
    rows = {}
    for line in doc.read_text().splitlines():
        if not line.startswith("| ") or " --- " in line or "Key Suffix" in line:
            continue
        fields = [part.strip().replace(r"\|", "|")
                  for part in re.split(r"(?<!\\)\|", line)[1:-1]]
        require(len(fields) == 4, "catalog_request_row_shape")
        key = prefix + fields[0]
        require(key not in rows, "catalog_request_duplicate:" + key)
        row = dict(zip(("zh-CN", "en", "ja"), fields[1:]))
        require(names(row["en"]) == names(row["zh-CN"]) == names(row["ja"]),
                "catalog_request_placeholder_mismatch:" + key)
        rows[key] = row
    require(rows, "catalog_request_empty")
    return rows


def validate(payload):
    require(isinstance(payload, dict) and
            set(payload) == {"schema", "default_locale", "locales"} and
            payload["schema"] == "opencad.panel-i18n.v1" and
            payload["default_locale"] == "en", "catalog_schema")
    packs = payload["locales"]
    require(isinstance(packs, dict) and packs and "en" in packs,
            "catalog_locales_empty")
    keys = set(packs["en"])
    require(keys, "catalog_keys_empty")
    for locale, pack in packs.items():
        require(isinstance(pack, dict) and set(pack) == keys,
                "catalog_key_set:" + locale)
        for key, value in pack.items():
            require(re.fullmatch(r"panel\.[a-z][a-z0-9_.]*", key),
                    "catalog_invalid_key")
            require(names(value) == names(packs["en"][key]),
                    "catalog_placeholder_set:" + locale + ":" + key)
            if locale == "en":
                require(not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", value),
                        "catalog_english_contamination:" + key)
            if locale == "zh-CN":
                require(not re.search(r"[\u3040-\u30ff]", value),
                        "catalog_simplified_kana:" + key)
    return packs


def validate_request(packs, rows, prefix=PREFIX):
    expected = set(rows)
    require(expected, "catalog_request_empty")
    for locale, pack in packs.items():
        actual = {key for key in pack if key.startswith(prefix)}
        missing, extra = expected - actual, actual - expected
        require(not missing and not extra,
                "catalog_consumer_keys:" + locale +
                ":missing=" + ",".join(sorted(missing)[:8]) +
                ":extra=" + ",".join(sorted(extra)[:8]))
        for key, row in rows.items():
            require(locale in row, "catalog_request_locale_missing:" + locale)
            require(names(pack[key]) == names(row[locale]),
                    "catalog_consumer_placeholders:" + locale + ":" + key)


class PanelCatalogTests(unittest.TestCase):
    def test_actual_catalog_matches_consumer_request_both_ways(self):
        validate_request(validate(read_catalog()), request())

    def test_duplicate_json_keys_are_rejected(self):
        raw = CATALOG.read_text()
        self.assertIn('"schema":', raw)
        duplicate = raw.replace('"schema":', '"schema":"duplicate","schema":', 1)
        with self.assertRaisesRegex(ValueError, "catalog_duplicate_key:schema"):
            read_catalog(duplicate)

    def test_missing_and_extra_consumer_keys_are_rejected(self):
        packs = validate(read_catalog())
        rows = request()
        key = next(iter(rows))
        missing = copy.deepcopy(packs)
        for pack in missing.values():
            pack.pop(key, None)
        with self.assertRaisesRegex(ValueError, "catalog_consumer_keys:"):
            validate_request(missing, rows)
        extra = copy.deepcopy(packs)
        for pack in extra.values():
            pack[PREFIX + "obsolete"] = "obsolete"
        with self.assertRaisesRegex(ValueError, "catalog_consumer_keys:"):
            validate_request(extra, rows)

    def test_same_wrong_placeholder_in_all_locales_is_rejected(self):
        packs = validate(read_catalog())
        rows = request()
        key = next(key for key, row in rows.items() if names(row["en"]))
        mutated = copy.deepcopy(packs)
        for pack in mutated.values():
            pack[key] = "{incorrect}"
        validate({"schema": "opencad.panel-i18n.v1", "default_locale": "en",
                  "locales": mutated})
        with self.assertRaisesRegex(ValueError, "catalog_consumer_placeholders:"):
            validate_request(mutated, rows)

    def test_empty_sets_and_language_pollution_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "catalog_request_empty"):
            validate_request(validate(read_catalog()), {})
        empty = read_catalog()
        empty["locales"] = {}
        with self.assertRaisesRegex(ValueError, "catalog_locales_empty"):
            validate(empty)
        for locale, value, reason in (
            ("en", "\u53c2\u6570", "catalog_english_contamination:"),
            ("zh-CN", "\u30d1\u30e9\u30e1\u30fc\u30bf", "catalog_simplified_kana:"),
        ):
            polluted = read_catalog()
            polluted["locales"][locale]["panel.workbench.heading"] = value
            with self.assertRaisesRegex(ValueError, reason):
                validate(polluted)

    def test_real_catalog_for_parametric_workflows(self):
        evidence = os.environ.get("OPENCAD_EVIDENCE_ROOT")
        require(evidence, "OPENCAD_EVIDENCE_ROOT_required")
        out = Path(evidence) / "real-panel-catalog"
        out.mkdir(exist_ok=False)
        path = APP / "tests/test_parametric_panel_locale.py"
        spec = importlib.util.spec_from_file_location("real_catalog_parametric", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        packs = validate(read_catalog())
        actual = {locale: {key: value for key, value in pack.items()
                           if key.startswith(PREFIX)}
                  for locale, pack in packs.items()}
        require(all(actual.values()), "catalog_consumer_empty")
        stream = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="oc-real-panel-catalog-",
                                         dir="/data/tmp") as runtime:
            module.RUNTIME = Path(runtime)
            with patch.object(module, "dictionary", return_value=actual):
                result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
                    unittest.defaultTestLoader.loadTestsFromTestCase(module.Controls))
            logs = {}
            for file in sorted(Path(runtime).rglob("*")):
                if file.is_file():
                    relative = file.relative_to(runtime).as_posix()
                    data = file.read_bytes()
                    target = out / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    logs[relative] = {"bytes": len(data), "sha256": module.digest(data)}
        (out / "controls.log").write_text(stream.getvalue())
        (out / "summary.json").write_text(json.dumps({
            "tests": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors), "skipped": len(result.skipped),
            "catalog_sha256": module.digest(CATALOG.read_bytes()),
            "consumer_sha256": module.digest((ROOT / module.PRODUCT).read_bytes()),
            "runtime_dictionary": "shipped_panel_i18n_json", "artifacts": logs,
        }, indent=2) + "\n")
        self.assertGreater(result.testsRun, 0)
        self.assertFalse(result.skipped)
        self.assertTrue(result.wasSuccessful(), stream.getvalue())


def snapshot():
    sys.path.insert(0, str(ROOT / "tools"))
    import acceptance
    return {"files": acceptance.source_manifest(ROOT),
            "head": subprocess.check_output(
                ["git", "--no-replace-objects", "-C", str(ROOT), "rev-parse", "HEAD"],
                text=True).strip(),
            "status": subprocess.check_output(
                ["git", "--no-replace-objects", "-C", str(ROOT), "status", "--porcelain"],
                text=True)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()) and
            out != Path("/hf3fs-jd/data/tmp").resolve(), "catalog_evidence_root")
    out.mkdir(parents=True, exist_ok=False)
    report = {"verdict": "FAIL", "at": datetime.now(timezone.utc).isoformat(),
              "source_before": None, "source_after": None, "actual_rc": 1}
    try:
        report["source_before"] = snapshot()
        os.environ["OPENCAD_EVIDENCE_ROOT"] = str(out)
        with (out / "tests.log").open("x") as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(PanelCatalogTests))
        report.update(tests=result.testsRun, failures=len(result.failures),
                      errors=len(result.errors), skipped=len(result.skipped))
        report["source_after"] = snapshot()
        require(report["source_before"] == report["source_after"], "catalog_source_drift")
        require(result.testsRun and result.wasSuccessful() and not result.skipped,
                "catalog_tests_failed")
        report.update(verdict="PASS", actual_rc=0)
    except Exception as exc:
        report["first_cause"] = str(exc)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print("OPENCAD_PANEL_CATALOG verdict={verdict} rc={actual_rc} tests={tests} "
          "failures={failures} errors={errors} at={finished_at}".format(
              tests=report.get("tests", "UNTESTED"),
              failures=report.get("failures", "UNTESTED"),
              errors=report.get("errors", "UNTESTED"),
              **{key: report[key] for key in ("verdict", "actual_rc", "finished_at")}))
    return report["actual_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
