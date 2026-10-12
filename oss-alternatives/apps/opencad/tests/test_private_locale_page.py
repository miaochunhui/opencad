"""Private 3D locale wiring checks; no production or CAD mutation is performed."""

import argparse
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone


APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[2]
sys.path.insert(0, str(APP))
import secure_web


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.bindings = []
        self.sections = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.scripts.append((attrs.get("src"), "defer" in attrs))
        if "data-panel-i18n" in attrs:
            self.bindings.append((tag, attrs["data-panel-i18n"]))
        if tag == "section":
            self.sections.append(attrs)


HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert/strict");
const source = fs.readFileSync(process.argv[2], "utf8");
const mode = process.argv[3];
const listeners = new Map();
const alerts = [];
const errors = [];
const calls = [];
let resolveCatalog, rejectCatalog;
const catalog = new Promise((resolve, reject) => {
  resolveCatalog = resolve; rejectCatalog = reject;
});
const root = {form: {value: "private draft", disabled: true, hidden: true}};
const document = {
  readyState: mode === "loading" ? "loading" : "complete",
  body: {children: [], append(node) { node.parent = this; this.children.push(node); alerts.push(node); }},
  querySelector(selector) {
    assert.equal(selector, ".industrial-workbench");
    return mode === "missing-root" ? null : root;
  },
  createElement(tag) {
    assert.equal(tag, "output");
    return {tagName: "OUTPUT", dataset: {}, textContent: "", children: [], parent: null, attrs: {},
      setAttribute(name, value) { this.attrs[name] = value; }};
  },
  addEventListener(name, callback, options) {
    assert.equal(name, "DOMContentLoaded");
    assert.equal(options.once, true);
    listeners.set(name, callback);
  }
};
const locale = {
  ready: catalog,
  bind(node) {
    assert.equal(node, root);
    calls.push("bind");
    if (mode === "bind-failure") throw new Error("missing_translation:panel.workbench.heading");
    return () => calls.push("unbind");
  }
};
const window = mode === "missing-service" ? {} : {OpenCADLocale: locale};
const context = vm.createContext({window, document, Promise, Error, Object, String,
  console: {error(...values) { errors.push(values.map(String).join(" ")); }}});
vm.runInContext(source, context, {filename: process.argv[2]});
assert.ok(window.OpenCADPrivateLocale);
assert.equal(Object.isFrozen(window.OpenCADPrivateLocale), true);
assert.equal(calls.length, 0, "must await real catalog readiness");
const completion = window.OpenCADPrivateLocale.ready.then(
  () => ({ok: true}), error => ({ok: false, error: String(error.message || error)}));
if (mode === "loading") {
  resolveCatalog();
  assert.equal(calls.length, 0, "must await DOM");
  listeners.get("DOMContentLoaded")();
} else if (mode === "load-failure") {
  rejectCatalog(new Error("panel_catalog_fetch_failed"));
} else {
  resolveCatalog();
}
completion.then(result => {
  const failed = ["load-failure", "bind-failure", "missing-root", "missing-service"].includes(mode);
  assert.equal(result.ok, !failed);
  assert.equal(root.form.value, "private draft");
  assert.equal(root.form.disabled, true);
  assert.equal(root.form.hidden, true);
  if (failed) {
    assert.equal(alerts.length, 1, "failure must be visible separately from CAD status");
    assert.equal(alerts[0].attrs.role, "alert");
    assert.equal(alerts[0].attrs.lang, "en");
    assert.equal(alerts[0].attrs["data-opencad-public-locale-error"], "private");
    assert.deepEqual(Object.keys(alerts[0].attrs).sort(),
      ["data-opencad-public-locale-error", "lang", "role"]);
    assert.equal(alerts[0].textContent, "private_locale_initialization_failed");
    assert.equal(alerts[0].tagName, "OUTPUT");
    assert.equal(alerts[0].parent, document.body);
    assert.equal(alerts[0].children.length, 0);
    assert.equal(alerts[0].textContent.includes("panel_catalog_fetch_failed"), false);
    assert.equal(alerts[0].textContent.includes("missing_translation"), false);
    assert.equal(errors.length, 1, "failure must also remain diagnosable");
    assert.notEqual(result.error, "");
  } else {
    assert.deepEqual(calls, ["bind"]);
    assert.equal(alerts.length, 0);
    assert.equal(errors.length, 0);
  }
  console.log("PRIVATE_LOCALE_JS_PASS " + mode);
}).catch(error => { console.error(error); process.exitCode = 1; });
"""


class PrivateLocalePageTests(unittest.TestCase):
    def page(self):
        parser = PageParser()
        parser.feed(secure_web.private_3d_page())
        return parser

    def test_service_precedes_identity_adapter_once(self):
        scripts = self.page().scripts
        names = [name for name, _ in scripts]
        service = "/web/js/locale-service.js"
        self.assertEqual(names.count(service), 1)
        self.assertLess(names.index(service), names.index("/web/js/project-session.js"))
        self.assertFalse(dict(scripts)[service])

    def test_private_binding_is_deferred_and_unique(self):
        scripts = self.page().scripts
        self.assertEqual(scripts.count(("/web/js/private-locale.js", True)), 1)

    def test_all_five_panel_summaries_are_bound(self):
        self.assertEqual(self.page().bindings, [
            ("summary", "panel.workbench.parametric"),
            ("summary", "panel.workbench.surfaces"),
            ("summary", "panel.workbench.assembly"),
            ("summary", "panel.workbench.drawing"),
            ("summary", "panel.workbench.render"),
        ])

    def test_workspace_accessible_name_is_bound(self):
        sections = [item for item in self.page().sections
                    if item.get("class") == "industrial-workbench"]
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].get("data-panel-i18n-aria"),
                         "panel.workbench.heading")

    def test_private_2d_assets_never_bind_absent_3d_workbench(self):
        self.assertNotIn("private-locale.js", secure_web.PRIVATE_ASSETS)
        self.assertNotIn("locale-service.js", secure_web.PRIVATE_ASSETS)

    def static_page(self, source):
        self.assertEqual(Path(secure_web.__file__).resolve(), APP / "secure_web.py")
        with tempfile.TemporaryDirectory(prefix="private-locale-static-", dir="/data/tmp") as directory:
            path = Path(directory).resolve()
            (path / "index.html").write_text(source, encoding="utf-8")
            self.assertEqual((path / "index.html").read_text(encoding="utf-8"), source)
            handler = object.__new__(secure_web.Handler)
            handler.server = type("StaticServer", (), {"web_root": path})()
            return handler._static("/").payload.decode("utf-8")

    def test_private_2d_existing_service_is_not_duplicated(self):
        page = self.static_page("<html><head>" + secure_web.LOCALE_SERVICE_ASSET +
                                "<script src='/web/js/app.js'></script></head></html>")
        self.assertEqual(page.count(secure_web.LOCALE_SERVICE_ASSET), 1)
        self.assertLess(page.index("locale-service.js"), page.index("project-session.js"))
        self.assertLess(page.index("project-session.js"), page.index("app.js"))
        self.assertNotIn("private-locale.js", page)

    def test_private_2d_missing_service_is_inserted_once(self):
        page = self.static_page("<html><head></head></html>")
        self.assertEqual(page.count(secure_web.LOCALE_SERVICE_ASSET), 1)
        self.assertLess(page.index("locale-service.js"), page.index("project-session.js"))

    def test_private_2d_duplicate_service_is_refused(self):
        with self.assertRaisesRegex(secure_web.HttpError, "must be unique"):
            self.static_page("<html><head>" + secure_web.LOCALE_SERVICE_ASSET * 2 +
                             "</head></html>")

    def test_private_2d_relative_service_is_not_duplicated(self):
        for source in ("web/js/locale-service.js", "./web/js/locale-service.js"):
            page = self.static_page("<html><head></head><body><script src='" + source +
                                    "'></script><script src='web/js/app.js'></script></body></html>")
            parsed = PageParser()
            parsed.feed(page)
            services = [item for item in parsed.scripts if item[0].endswith("locale-service.js")]
            self.assertEqual(services, [(source, False)])
            self.assertLess(page.index("locale-service.js"), page.index("project-session.js"))
            self.assertLess(page.index("project-session.js"), page.index("app.js"))

    def test_private_2d_comment_tag_is_not_a_loaded_service(self):
        page = self.static_page("<html><head><!--" + secure_web.LOCALE_SERVICE_ASSET +
                                "--></head></html>")
        parsed = PageParser()
        parsed.feed(page)
        services = [item for item in parsed.scripts if item[0].endswith("locale-service.js")]
        self.assertEqual(services, [("/web/js/locale-service.js", False)])

    def test_private_2d_attribute_order_does_not_duplicate_service(self):
        page = self.static_page("<html><head><script type='text/javascript' "
                                "src='web/js/locale-service.js'></script></head></html>")
        parsed = PageParser()
        parsed.feed(page)
        self.assertEqual(parsed.scripts[0], ("web/js/locale-service.js", False))
        self.assertEqual(len(parsed.scripts), 2)

    def test_private_2d_deferred_or_unclosed_service_is_refused(self):
        for tag in ("<script src='web/js/locale-service.js' defer></script>",
                    "<script src='web/js/locale-service.js' async></script>",
                    "<script src='web/js/locale-service.js'>"):
            with self.assertRaises(secure_web.HttpError) as caught:
                self.static_page("<html><head>" + tag)
            self.assertEqual(caught.exception.code, "invalid_locale_service")

    def run_js(self, mode):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required; no skip or stub success")
        path = APP / "web/js/private-locale.js"
        self.assertTrue(path.is_file(), "private locale asset must exist")
        process = subprocess.run([node, "-", str(path), mode], input=HARNESS,
                                 text=True, capture_output=True, check=False)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn("PRIVATE_LOCALE_JS_PASS " + mode, process.stdout)

    def test_real_binding_after_catalog_ready(self):
        self.run_js("ready")

    def test_late_dom_binding_does_not_touch_drafts(self):
        self.run_js("loading")

    def test_catalog_failure_is_not_silent(self):
        self.run_js("load-failure")

    def test_missing_translation_is_not_silent(self):
        self.run_js("bind-failure")

    def test_missing_workspace_is_not_empty_success(self):
        self.run_js("missing-root")

    def test_missing_service_is_not_empty_success(self):
        self.run_js("missing-service")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    base = Path("/hf3fs-jd/data/tmp")
    if (not args.out.is_absolute() or ".." in args.out.parts
            or not args.out.is_relative_to(base) or args.out == base):
        raise ValueError("new external evidence directory required")
    args.out.mkdir(exist_ok=False)
    paths = [Path(__file__), APP / "secure_web.py", APP / "web/js/private-locale.js"]
    before = {str(path.relative_to(ROOT)):
              hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
              for path in paths}
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PrivateLocalePageTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = {str(path.relative_to(ROOT)):
             hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
             for path in paths}
    drift = sorted(path for path in before if before[path] != after[path])
    passed = result.wasSuccessful() and result.testsRun > 0 and not drift
    report = {
        "at": datetime.now(timezone.utc).isoformat(), "verdict": "PASS" if passed else "FAIL",
        "actual_rc": 0 if passed else 1, "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "source_before": before, "source_after": after,
        "source_drift": drift, "production_changed": False,
        "browser_acceptance": "NOT_EXECUTED", "shared_catalog_integration": "NOT_EXECUTED",
    }
    (args.out / "tests.log").write_text(stream.getvalue(), encoding="utf-8")
    (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    line = "PRIVATE_LOCALE_PAGE verdict={} tests={} failures={} errors={} at={}".format(
        report["verdict"], report["tests"], report["failures"], report["errors"], report["at"])
    (args.out / "verdict.log").write_text(line + "\n", encoding="utf-8")
    print(line)
    return report["actual_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
