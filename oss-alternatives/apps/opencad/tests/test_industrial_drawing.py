"""Real drawing HTTP/CAS/browser acceptance, without mock geometry or skips.

--dependency-root imports the real project_store from that read-only source.
SQLite WAL fixtures are dedicated local data; all logs/artifacts are external.
The local fixture's bearer token is test routing, not production authentication.
Production auth/CSRF and common gateway dispatch remain chair-owned.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.dont_write_bytecode = True
APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[2]
PACKAGES = ROOT / "oss-alternatives/packages"
for path in (APP, PACKAGES / "cadcore", PACKAGES / "dxf", PACKAGES / "vault"):
    sys.path.insert(0, str(path))

import industrial_drawing as drawing
import server
from cadcore import docmodel

OUT = None
DEPENDENCY_ROOT = Path(os.environ.get("OPENCAD_DRAWING_DEPENDENCY_ROOT", "/data/dev/opencad"))
BROWSER = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def fixture():
    source = load("drawing_plate_fixture", PACKAGES / "cadcore/tests/test_associative_drawings.py")
    doc, definition = source.plate(), source.specification()
    identifiers = {name: str(uuid.uuid5(uuid.NAMESPACE_URL, "drawing-web-fixture:" + name))
                   for name in ("outline", "bottom", "right", "upper", "left",
                                "hole-sketch", "hole-circle", "base", "hole-cut")}

    def replace(value):
        if isinstance(value, str):
            return identifiers.get(value, value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    definition = json.loads(json.dumps(definition))
    for entry in definition["dimensions"]:
        entry["ref"][0] = identifiers.get(entry["ref"][0], entry["ref"][0])
    for ref in definition["bom_refs"]:
        ref[0] = identifiers.get(ref[0], ref[0])
    return replace(doc), definition


class FixtureApplication:
    """Real store CAS wiring example; auth token is private isolated test fixture."""

    def __init__(self, store, out, core):
        self.store, self.out, self.core = store, out, core
        self.project = None
        self.race = False
        self.errors = []
        self.requests = []
        self.svg_mutation = False
        self.export_mutation = False
        self.read_barrier = None

    @contextmanager
    def workspace(self, head):
        directory = tempfile.mkdtemp(prefix="request-", dir=self.out)
        workspace = server.Workspace(self.core, locale="en",
                                     standard=head["snapshot"]["standard"], out_root=directory)
        workspace.doc = docmodel.document_from_l1(head["snapshot"]["doc"])
        workspace.undo_stack = list(head["snapshot"]["undo"])
        workspace.redo_stack = list(head["snapshot"]["redo"])
        workspace.standard = head["snapshot"]["standard"]
        yield workspace

    def metadata(self, head):
        return {key: head[key] for key in ("project_id", "revision", "sha256")}

    def race_edit(self, head):
        changed = json.loads(head["snapshot"]["doc"])
        changed["meta"]["title_key"] = "raced"
        snapshot = {**head["snapshot"], "doc": docmodel.document_to_l1(changed)}
        return self.store.commit(
            head["project_id"], expected_revision=head["revision"], expected_sha=head["sha256"],
            request_id="race-" + uuid.uuid4().hex,
            request_payload={"expected_revision": head["revision"], "expected_sha": head["sha256"]},
            payload=snapshot, response={"ok": True})

    def handle(self, method, path, payload, token):
        if token != "drawing-isolated-fixture":
            raise drawing.DrawingWebError("fixture_auth_required", "Isolated fixture token required", 401)
        head = self.store.read_head(self.project)
        if path == "/api/state":
            return 200, {"ok": True, **self.metadata(head)}, {}
        if path == "/api/drawing" and method == "POST":
            expected = {"definition", "expected_revision", "expected_sha", "request_id"}
            if not isinstance(payload, dict) or set(payload) != expected:
                raise drawing.DrawingWebError("bad_arguments", "Complete CAS envelope required", 400)
            request = {**payload, "route": path}
            receipt = self.store.read_receipt(head["project_id"], payload["request_id"],
                                             request_payload=request)
            if receipt is not None:
                return 200, {**receipt["response"], **self.metadata(receipt)}, {}
            if (head["revision"], head["sha256"]) != (
                    payload["expected_revision"], payload["expected_sha"]):
                raise drawing.DrawingWebError("revision_conflict", "Stale project base", 409)
            with self.workspace(head) as workspace:
                candidate = drawing.prepare_drawing(workspace, {
                    "definition": payload["definition"], "model_version": head["revision"] + 1})
                if candidate.before_document_sha256 != digest(workspace.l1_text()):
                    raise drawing.DrawingWebError("revision_conflict", "Candidate base changed", 409)
                snapshot = {"doc": candidate.doc,
                            "undo": [*workspace.undo_stack, workspace.l1_text()],
                            "redo": [], "standard": workspace.standard}
                if self.race:
                    self.race = False
                    self.race_edit(head)
                receipt = self.store.commit(
                    head["project_id"], expected_revision=payload["expected_revision"],
                    expected_sha=payload["expected_sha"], request_id=payload["request_id"],
                    request_payload=request, payload=snapshot, response=candidate.response)
            return 200, {**receipt["response"], **self.metadata(receipt)}, {}
        if method != "GET":
            raise drawing.DrawingWebError("method_not_allowed", "Unknown fixture route", 405)
        with self.workspace(head) as workspace:
            if path == "/api/drawing":
                value = drawing.read_drawing(workspace, {"model_version": head["revision"]})
                if self.svg_mutation and value["defined"]:
                    value["svg"] = value["svg"].replace(
                        "</svg>", "<script>window.drawingInjected=true</script></svg>")
                response = (200, {**value, **self.metadata(head)}, {})
            elif path.startswith("/api/drawing/"):
                result = drawing.export_drawing(workspace, {
                    "model_version": head["revision"], "format": path.rsplit("/", 1)[1]})
                response = (200, result.data, {**result.headers, "Content-Type": result.content_type})
                if self.export_mutation:
                    response[2]["X-OpenCAD-Document-SHA256"] = "0" * 64
            else:
                raise drawing.DrawingWebError("not_found", "Unknown fixture route", 404)
        if self.race:
            self.race = False
            self.race_edit(head)
        current = self.store.read_head(head["project_id"])
        if (current["revision"], current["sha256"]) != (head["revision"], head["sha256"]):
            raise drawing.DrawingWebError("revision_conflict", "Model changed during drawing build", 409)
        if path == "/api/drawing" and self.read_barrier is not None:
            started, release = self.read_barrier
            self.read_barrier = None
            started.set()
            if not release.wait(15):
                raise RuntimeError("Isolated drawing response barrier was not released")
        return response


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, _fmt, *_args):
        pass

    def do_GET(self):
        self.handle_route()

    def do_POST(self):
        self.handle_route()

    def handle_route(self):
        path = self.path.split("?", 1)[0]
        self.server.application.requests.append(
            {"time": datetime.now(timezone.utc).isoformat(), "method": self.command, "path": path})
        try:
            if path == "/" and self.command == "GET":
                data = self.server.page.encode()
                status, headers = 200, {"Content-Type": "text/html; charset=utf-8"}
            elif path in ("/web/js/drawing-panel.js", "/web/css/drawing-panel.css"):
                data = (APP / path.removeprefix("/")).read_bytes()
                status, headers = 200, {"Content-Type": "text/javascript" if path.endswith(".js") else "text/css"}
            else:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.loads(raw) if raw else {}
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                status, value, headers = self.server.application.handle(self.command, path, payload, token)
                data = value if isinstance(value, bytes) else json.dumps(value).encode()
                headers.setdefault("Content-Type", "application/json")
        except Exception as exc:
            self.server.application.errors.append({"type": type(exc).__name__, "detail": str(exc)})
            status = getattr(exc, "status", 500)
            data = json.dumps({"ok": False, "error": getattr(exc, "code", "internal_error"),
                               "detail": str(exc)}).encode()
            headers = {"Content-Type": "application/json"}
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/web/css/drawing-panel.css">
<style>body{margin:0;padding:12px;background:#e8e7df}main{max-width:1100px;margin:auto}</style>
</head><body><main id="drawing-host"></main>
<script src="/web/js/drawing-panel.js"></script><script>
// Fixture-only project envelope. Production uses the chair's project-session.
const rawFetch=window.fetch.bind(window);
async function fixtureFetch(path,options) {
 options=options||{};options.headers=new Headers(options.headers);
 options.headers.set("Authorization","Bearer drawing-isolated-fixture");
 if(options.method==="POST") {
  const state=await rawFetch("/api/state",{headers:options.headers}).then(r=>r.json());
  const body=JSON.parse(options.body);
  body.expected_revision=state.revision;body.expected_sha=state.sha256;
  body.request_id=crypto.randomUUID();options.body=JSON.stringify(body);
 }
 return rawFetch(path,options);
}
window.panel=window.opencadDrawingPanel.mount(document.getElementById("drawing-host"),
 {fetch:fixtureFetch});
</script></body></html>"""


class IndustrialDrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global OUT
        if OUT is None:
            configured = os.environ.get("OPENCAD_DRAWING_TEST_OUT")
            OUT = Path(configured) if configured else Path(
                "/hf3fs-jd/data/tmp/opencad-drawing-web-20261011") / (
                    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-discover")
        OUT = OUT.resolve()
        OUT.mkdir(parents=True, exist_ok=True)
        dependency = DEPENDENCY_ROOT / "oss-alternatives/apps/opencad/project_store.py"
        cls.store_module = load("drawing_real_project_store", dependency)
        cls.core = server._load_cadcore()
        cls.source_doc, cls.spec = fixture()

    def setUp(self):
        self.out = OUT / self._testMethodName
        self.out.mkdir()
        runtime = Path("/data/dev/opencad-drawing-web-fixtures-20261011") / uuid.uuid4().hex
        self.store = self.store_module.ProjectStore(runtime)
        self.addCleanup(self.store.close)
        self.head = self.store.create_project(
            "drawing-test-owner", title="Hole plate", request_id="create",
            payload={"doc": docmodel.document_to_l1(self.source_doc), "undo": [],
                     "redo": [], "standard": self.source_doc["standard_profile"]})
        self.app = FixtureApplication(self.store, self.out, self.core)
        self.app.project = self.head["project_id"]
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.http.application, self.http.page = self.app, PAGE
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = "http://127.0.0.1:" + str(self.http.server_port)

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(5)
        (self.out / "fixture-errors.json").write_text(json.dumps(self.app.errors, indent=2))
        (self.out / "fixture-requests.json").write_text(json.dumps(self.app.requests, indent=2))

    @contextmanager
    def browser(self, helper, width):
        with helper.BrowserSession(BROWSER, self.out / ("browser-" + str(width)),
                                   width=width, height=900, source_root=ROOT) as browser:
            try:
                yield browser
            except Exception:
                facts = browser.evaluate("""(()=>({url:location.href,
                    status:document.querySelector('.drawing-panel-status')?.textContent,
                    busy:document.querySelector('.drawing-panel')?.getAttribute('aria-busy'),
                    panel:!!window.panel,svg:!!document.querySelector('.drawing-panel-preview svg'),
                    resources:performance.getEntriesByType('resource').map(n=>n.name),
                    body:document.body.innerText}))()""")
                (browser.out / "failure-dom.json").write_text(json.dumps(facts, indent=2))
                browser.screenshot("failure")
                raise

    def request(self, path, payload=None, auth=True):
        headers = {"Authorization": "Bearer drawing-isolated-fixture"} if auth else {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, headers=headers,
                                         data=json.dumps(payload).encode() if payload is not None else None)
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = response.read()
            value = json.loads(data) if response.headers.get("Content-Type") == "application/json" else data
            return response.status, value, dict(response.headers)

    def save(self, spec=None, head=None, request_id=None):
        head = head or self.store.read_head(self.head["project_id"])
        return self.request("/api/drawing", {"definition": spec or self.spec,
                            "expected_revision": head["revision"], "expected_sha": head["sha256"],
                            "request_id": request_id or uuid.uuid4().hex})

    def test_prepare_is_real_but_doc_and_history_unchanged(self):
        with self.app.workspace(self.head) as workspace:
            before = workspace.l1_text()
            candidate = drawing.prepare_drawing(workspace, {"definition": self.spec, "model_version": 2})
            self.assertEqual(workspace.l1_text(), before)
            self.assertEqual(workspace.undo_stack, [])
            self.assertEqual(candidate.before_document_sha256, digest(before))
            self.assertNotEqual(candidate.doc, before)
            self.assertEqual(docmodel.document_to_l1(docmodel.document_from_l1(candidate.doc)),
                             candidate.doc)
            self.assertEqual(docmodel.document_from_l1(candidate.doc).diagnostics,
                             workspace.doc.diagnostics)
            self.assertEqual(candidate.response["model_version"], 2)
            self.assertTrue(all(view["hidden_measured"] for view in candidate.response["views"]))
            self.assertNotIn("svg", candidate.response)

    def test_real_http_save_read_cas_and_model_binding(self):
        status, saved, _ = self.save()
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["revision"], 2)
        head = self.store.read_head(self.head["project_id"])
        self.assertEqual(len(head["snapshot"]["undo"]), 1)
        self.assertEqual(head["snapshot"]["undo"][0], self.head["snapshot"]["doc"])
        status, value, _ = self.request("/api/drawing")
        self.assertEqual(status, 200, value)
        self.assertEqual(value["source_model_sha256"], saved["source_model_sha256"])
        self.assertEqual(value["drawing_sha256"], saved["drawing_sha256"])
        self.assertEqual(value["source_document_sha256"], digest(head["snapshot"]["doc"]))
        self.assertIn('data-layer="HIDDEN"', value["svg"])
        self.assertIn('data-layer="SECTION"', value["svg"])
        self.assertEqual(self.store.read_head(self.head["project_id"]), head)
        (self.out / "read.json").write_text(json.dumps(value, indent=2))

    def test_empty_read_does_not_create_drawing(self):
        status, value, _ = self.request("/api/drawing")
        self.assertEqual(status, 200)
        self.assertFalse(value["defined"])
        self.assertEqual(self.store.read_head(self.head["project_id"]), self.head)
        self.assertEqual(self.request("/api/drawing/pdf")[0], 404)

    def test_duplicate_request_replays_once(self):
        first = self.save(request_id="same", head=self.head)
        second = self.save(request_id="same", head=self.head)
        self.assertEqual(first[0], 200, first[1])
        self.assertEqual(second[:2], first[:2])
        self.assertEqual(self.store.read_head(self.head["project_id"])["revision"], 2)

    def test_bad_reference_and_boundary_keep_old_head(self):
        self.assertEqual(self.save()[0], 200)
        head = self.store.read_head(self.head["project_id"])
        bad = json.loads(json.dumps(self.spec))
        bad["dimensions"][0]["ref"][0] = str(uuid.uuid4())
        self.assertEqual(self.save(spec=bad)[0], 422)
        bad = json.loads(json.dumps(self.spec))
        bad["sections"][0]["offset_nm"] = 0
        self.assertEqual(self.save(spec=bad)[0], 422)
        self.assertEqual(self.store.read_head(self.head["project_id"]), head)

    def test_stale_cas_and_racing_candidate_not_acknowledged(self):
        self.assertEqual(self.save()[0], 200)
        self.assertEqual(self.save(head=self.head)[0], 409)
        self.app.race = True
        status, value, _ = self.save()
        self.assertEqual(status, 409, value)
        self.assertEqual(self.store.read_head(self.head["project_id"])["revision"], 3)

    def test_read_race_rejected_instead_of_old_geometry(self):
        self.assertEqual(self.save()[0], 200)
        self.app.race = True
        status, value, _ = self.request("/api/drawing")
        self.assertEqual(status, 409, value)
        self.assertNotIn("svg", value)

    def test_svg_pdf_dxf_bytes_readback_real(self):
        self.assertEqual(self.save()[0], 200)
        for fmt in ("svg", "pdf", "dxf"):
            status, value, headers = self.request("/api/drawing/" + fmt)
            self.assertEqual(status, 200, value)
            self.assertIsInstance(value, bytes)
            self.assertEqual(headers["X-OpenCAD-Artifact-SHA256"], hashlib.sha256(value).hexdigest())
            self.assertEqual(headers["X-OpenCAD-Model-Version"], "2")
            (self.out / ("drawing." + fmt)).write_bytes(value)
        from xml.etree import ElementTree
        root = ElementTree.fromstring((self.out / "drawing.svg").read_bytes())
        self.assertGreater(len(root.findall(".//{http://www.w3.org/2000/svg}line")), 20)
        readbacks = list(self.out.glob("request-*/export-*/artifact/readback.json"))
        self.assertEqual(len(readbacks), 2)
        self.assertTrue(all(json.loads(path.read_text())["verdict"] == "PASS" for path in readbacks))

    def test_export_race_rejected_and_request_paths_denied(self):
        self.assertEqual(self.save()[0], 200)
        self.app.race = True
        self.assertEqual(self.request("/api/drawing/svg")[0], 409)
        bad = {**self.spec, "path": "/etc/passwd"}
        self.assertEqual(self.save(spec=bad)[0], 422)

    def test_model_edit_triggers_real_section_hlr_dimension_rebuild(self):
        self.assertEqual(self.save()[0], 200)
        status, before, _ = self.request("/api/drawing")
        self.assertEqual(status, 200)
        head = self.store.read_head(self.head["project_id"])
        document = json.loads(head["snapshot"]["doc"])
        for entity in document["sketches"][0]["entities"]:
            for key in ("x1", "x2"):
                if entity["geom"][key] == 120_000_000:
                    entity["geom"][key] = 140_000_000
        document["sketches"][1]["entities"][0]["geom"]["r"] = 8_000_000
        for feature in document["features"]:
            feature["params"]["distance_nm"] = 8_000_000
        self.store.commit(
            head["project_id"], expected_revision=head["revision"], expected_sha=head["sha256"],
            request_id="model-edit",
            request_payload={"expected_revision": head["revision"], "expected_sha": head["sha256"]},
            payload={**head["snapshot"], "doc": docmodel.document_to_l1(document)},
            response={"ok": True})
        status, after, _ = self.request("/api/drawing")
        self.assertEqual(status, 200, after)
        self.assertEqual(after["model_version"], 3)
        self.assertNotEqual(before["source_model_sha256"], after["source_model_sha256"])
        self.assertEqual({item["id"]: item["value_nm"] for item in after["dimensions"]},
                         {"width": 140_000_000, "hole": 16_000_000, "thickness": 8_000_000})
        section = next(view for view in after["views"] if view["name"] == "section-A")
        self.assertEqual(section["material_area_nm2"], 992_000_000_000_000)

    def test_payload_cannot_override_version_or_server_path(self):
        with self.app.workspace(self.head) as workspace:
            for body in ({"model_version": 1, "path": "/etc/passwd"},
                         {"model_version": True},
                         {"model_version": 1, "format": []},
                         {"model_version": 1, "format": "step"}):
                with self.subTest(body=body), self.assertRaises(drawing.DrawingWebError):
                    drawing.export_drawing(workspace, body)
        self.assertEqual(self.request("/api/drawing", auth=False)[0], 401)

    def test_corrupt_definition_and_unsafe_runtime_rejected(self):
        self.assertEqual(self.save()[0], 200)
        head = self.store.read_head(self.head["project_id"])
        with self.app.workspace(head) as workspace:
            workspace.doc.extra[drawing.KEY]["definition"]["title"] = "tampered"
            with self.assertRaises(drawing.DrawingWebError) as caught:
                drawing.read_drawing(workspace, {"model_version": 2})
            self.assertEqual(caught.exception.code, "bad_drawing_definition")
        with self.app.workspace(head) as workspace:
            workspace.out_root = str(ROOT)
            with self.assertRaises(drawing.DrawingWebError) as caught:
                drawing.export_drawing(workspace, {"model_version": 2, "format": "svg"})
            self.assertEqual(caught.exception.code, "unsafe_drawing_runtime")
        with self.app.workspace(head) as workspace:
            link = self.out / "runtime-link"
            link.symlink_to(workspace.out_root, target_is_directory=True)
            workspace.out_root = str(link)
            with self.assertRaises(drawing.DrawingWebError):
                drawing.read_drawing(workspace, {"model_version": 2})
        self.assertEqual(self.store.read_head(self.head["project_id"]), head)

    def test_browser_state_change_while_busy_rebuilds_new_head(self):
        self.assertEqual(self.save()[0], 200)
        helper = load("drawing_state_browser_helper",
                      ROOT / "oss-alternatives/tools/cad3d/viewer_controls_check.py")
        with self.browser(helper, 1440) as browser:
            browser.call("Page.navigate", {"url": self.base + "/"})
            browser.wait("!!document.querySelector('.drawing-panel-preview svg') && "
                         "!document.querySelector('.drawing-panel').matches('[aria-busy=true]')")
            started, release = threading.Event(), threading.Event()
            self.addCleanup(release.set)
            self.app.read_barrier = (started, release)
            browser.evaluate("void window.panel.refresh()")
            self.assertTrue(started.wait(10), "Real drawing response was not produced")
            new_head = self.app.race_edit(self.store.read_head(self.head["project_id"]))
            browser.evaluate("window.dispatchEvent(new Event('opencad:state'))")
            self.assertFalse(browser.evaluate("!!document.querySelector('.drawing-panel-preview svg')"))
            release.set()
            browser.wait("document.querySelector('.drawing-panel-binding').textContent.includes('项目版本 3')"
                         " && !document.querySelector('.drawing-panel').matches('[aria-busy=true]')",
                         timeout=30)
            value = browser.evaluate("document.querySelector('.drawing-panel-preview').textContent")
            self.assertIn("Model version 3", value)
            self.assertEqual(new_head["revision"], 3)
            browser.screenshot("state-rebuilt")

    def test_browser_rejects_svg_mutation_and_mismatched_export(self):
        self.assertEqual(self.save()[0], 200)
        helper = load("drawing_mutation_browser_helper",
                      ROOT / "oss-alternatives/tools/cad3d/viewer_controls_check.py")
        with self.browser(helper, 1440) as browser:
            browser.call("Browser.setDownloadBehavior",
                         {"behavior": "allow", "downloadPath": str(self.out)})
            browser.call("Page.navigate", {"url": self.base + "/"})
            browser.wait("!!document.querySelector('.drawing-panel-preview svg') && "
                         "!document.querySelector('.drawing-panel').matches('[aria-busy=true]')")
            browser.click('[data-drawing-export="svg"]')
            browser.wait("document.querySelector('.drawing-panel-status').textContent.includes('已下载')")
            deadline = time.monotonic() + 10
            while not (self.out / "drawing.svg").exists():
                if time.monotonic() >= deadline:
                    self.fail("Actual same-version SVG download is missing")
                time.sleep(.1)
            exported = (self.out / "drawing.svg").read_bytes()
            self.assertIn(b'data-layer="SECTION"', exported)
            self.app.export_mutation = True
            browser.click('[data-drawing-export="svg"]')
            browser.wait("document.querySelector('.drawing-panel-status').textContent.includes('已拒绝下载')")
            self.assertEqual((self.out / "drawing.svg").read_bytes(), exported)
            self.assertEqual(list(self.out.glob("drawing*.svg")), [self.out / "drawing.svg"])
            self.app.export_mutation = False
            self.app.svg_mutation = True
            browser.click("[data-drawing-refresh]")
            browser.wait("document.querySelector('.drawing-panel-status').textContent.includes('不支持的 SVG')")
            self.assertFalse(browser.evaluate("!!document.querySelector('.drawing-panel-preview svg')"))
            self.assertFalse(browser.evaluate("window.drawingInjected===true"))
            browser.screenshot("unsafe-svg-rejected")
            self.app.svg_mutation = False
            browser.click("[data-drawing-refresh]")
            browser.wait("!!document.querySelector('.drawing-panel-preview svg') && "
                         "!document.querySelector('.drawing-panel').matches('[aria-busy=true]')")
            browser.screenshot("safe-scene-restored")

    def test_real_browser_form_preview_and_narrow_bounds(self):
        helper = load("drawing_browser_helper", ROOT / "oss-alternatives/tools/cad3d/viewer_controls_check.py")
        screenshots = []
        for width in (1440, 420):
            with self.browser(helper, width) as browser:
                browser.call("Page.navigate", {"url": self.base + "/"})
                browser.wait("document.querySelector('.drawing-definition') && "
                             "!document.querySelector('.drawing-panel').matches('[aria-busy=true]') && " +
                             ("document.querySelector('.drawing-panel-status').textContent.includes('先保存')"
                              if width == 1440 else
                              "!!document.querySelector('.drawing-panel-preview svg')"))
                browser.evaluate("document.querySelector('.drawing-definition').value=" +
                                 json.dumps(json.dumps(self.spec)) +
                                 ";document.querySelector('.drawing-definition').dispatchEvent(new Event('input'));")
                browser.click(".drawing-panel-form button[type=submit]")
                browser.wait("document.querySelector('.drawing-panel-preview svg') && "
                             "!document.querySelector('.drawing-panel').matches('[aria-busy=true]')", timeout=30)
                dimensions = browser.evaluate(
                    "Array.from(document.querySelectorAll('.drawing-panel tbody tr')).map(n=>n.textContent)")
                self.assertTrue(any("120 mm" in item for item in dimensions))
                facts = browser.evaluate("""(()=>{const s=document.querySelector('.drawing-panel-preview svg');
                    const r=s.getBoundingClientRect();return {width:innerWidth,height:innerHeight,
                    scroll:document.documentElement.scrollWidth,box:{left:r.left,right:r.right,width:r.width},
                    hidden:s.querySelectorAll('[data-layer=HIDDEN]').length,
                    section:s.querySelectorAll('[data-layer=SECTION]').length};})()""")
                self.assertLessEqual(facts["scroll"], width)
                self.assertGreater(facts["box"]["width"], 100)
                self.assertLessEqual(facts["box"]["right"], width)
                self.assertGreater(facts["hidden"], 0)
                self.assertGreater(facts["section"], 0)
                shot = browser.screenshot("drawing")
                screenshots.append({"width": width, "facts": facts, "screenshot": str(shot)})
        (self.out / "browser-result.json").write_text(json.dumps(screenshots, indent=2))


def main():
    global OUT, DEPENDENCY_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dependency-root", type=Path, default=DEPENDENCY_ROOT)
    parser.add_argument("--independent-discovery", action="store_true",
                        help="Also run this exact suite through tools/acceptance.py")
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    OUT, DEPENDENCY_ROOT = args.out.resolve(), args.dependency_root.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    paths = [Path(__file__), APP / "industrial_drawing.py", APP / "server.py",
             APP / "web/js/drawing-panel.js", APP / "web/css/drawing-panel.css",
             ROOT / "docs/design/industrial-drawing-web.md",
             PACKAGES / "cadcore/cadcore/draw/associative.py",
             PACKAGES / "cadcore/cadcore/kernel/drawing_worker.py",
             PACKAGES / "cadcore/cadcore/kernel/subproc.py",
             PACKAGES / "cadcore/cadcore/docmodel.py",
             PACKAGES / "cadcore/tests/test_associative_drawings.py",
             ROOT / "oss-alternatives/tools/cad3d/viewer_controls_check.py",
             APP / "tests/legacy_browser_compat_gate.py",
             DEPENDENCY_ROOT / "oss-alternatives/apps/opencad/project_store.py"]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    suite = (unittest.defaultTestLoader.loadTestsFromNames(args.tests, sys.modules[__name__])
             if args.tests else unittest.defaultTestLoader.loadTestsFromTestCase(IndustrialDrawingTests))
    started = time.monotonic()
    commands = [{"argv": [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:]],
                 "cwd": str(ROOT)}]
    with (OUT / "tests.log").open("x") as handle:
        result = unittest.TextTestRunner(stream=handle, verbosity=2).run(suite)
    independent = None
    if args.independent_discovery and result.wasSuccessful() and not result.skipped:
        argv = [sys.executable, "-B", str(ROOT / "tools/acceptance.py"), "--suite",
                str(APP / "tests"), "--top", str(APP), "--pattern", Path(__file__).name]
        env = dict(os.environ, OPENCAD_DRAWING_DEPENDENCY_ROOT=str(DEPENDENCY_ROOT),
                   OPENCAD_DRAWING_TEST_OUT=str(OUT / "independent-discovery"),
                   PYTHONDONTWRITEBYTECODE="1")
        commands.append({"argv": argv, "cwd": str(ROOT)})
        with (OUT / "independent-discovery.log").open("xb") as handle:
            child = subprocess.run(argv, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT)
        markers = [line.removeprefix("INDEPENDENT_TEST_COUNTS ") for line in
                   (OUT / "independent-discovery.log").read_text().splitlines()
                   if line.startswith("INDEPENDENT_TEST_COUNTS ")]
        counts = json.loads(markers[0]) if len(markers) == 1 else None
        independent = {"rc": child.returncode, "counts": counts,
                       "verdict": "PASS" if child.returncode == 0 and counts
                       and counts["tests"] == result.testsRun and counts["passed"] == result.testsRun
                       and not counts["skipped"] and counts["successful"] else "FAIL"}
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    drift = [name for name in before if before[name] != after[name]]
    facts = {"verdict": "PASS" if result.wasSuccessful() and result.testsRun
             and not result.skipped and not drift
             and (independent is None or independent["verdict"] == "PASS") else "FAIL",
             "tests": result.testsRun, "failures": len(result.failures),
             "errors": len(result.errors), "skipped": len(result.skipped),
             "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
             "source_drift": drift, "source": after,
             "commands": commands, "independent_discovery": independent,
             "elapsed_seconds": time.monotonic() - started,
             "time": datetime.now(timezone.utc).isoformat(),
             "production_auth": "NOT_ASSESSED", "production_deployment": "NOT_ASSESSED"}
    (OUT / "result.json").write_text(json.dumps(facts, indent=2) + "\n")
    line = (f"INDUSTRIAL_DRAWING_WEB verdict={facts['verdict']} tests={facts['tests']}"
            f" passed={facts['passed']} failures={facts['failures']} errors={facts['errors']}"
            f" skipped={facts['skipped']} time={facts['time']} evidence={OUT}")
    (OUT / "acceptance.log").write_text(line + "\n")
    print(line, flush=True)
    if facts["verdict"] != "PASS":
        print((OUT / "tests.log").read_text()[-9000:])
    return 0 if facts["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
