#!/usr/bin/env python3
"""Independent private drawing HTTP/browser review against --source-root.

Only new isolated accounts, projects, request scratch and external evidence are
written. Product sources, existing projects, permissions and production are not
modified. Real FreeCAD HLR, sections and HTTP-exported PDF/DXF/SVG are inspected.
A gateway subclass pauses after real export, before its real final auth/CAS
check, to deterministically test revocation and version races. No geometry mock.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
NM = 1_000_000
DEFAULT_BROWSER = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def source_files(root):
    prefixes = ("oss-alternatives/apps/opencad",
                "oss-alternatives/packages/cadcore", "oss-alternatives/packages/dxf",
                "oss-alternatives/packages/vault", "oss-alternatives/tools/cad3d")
    result = {}
    for prefix in prefixes:
        for path in sorted((root / prefix).rglob("*")):
            if path.suffix in {".py", ".js", ".css", ".json", ".html"}:
                if path.is_symlink():
                    raise ValueError("Source symlink: " + str(path))
                if path.is_file():
                    result[str(path.relative_to(root))] = digest(path.read_bytes())
    if not result:
        raise ValueError("Empty source fingerprint")
    return result


class Review:
    def __init__(self, args, out, log):
        self.args, self.out, self.log = args, out, log
        self.checks, self.requests, self.artifacts, self.races = [], [], {}, []
        self.cookies = {}

    def check(self, name, condition, **facts):
        item = {"name": name, "pass": bool(condition), "time": self.now(), **facts}
        self.checks.append(item)
        self.log.write(json.dumps(item, ensure_ascii=True) + "\n")
        self.log.flush()
        if not condition:
            raise AssertionError(name)

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def call(self, method, route, body=None, *, user=None, project=None, raw=False):
        headers = {"Origin": self.origin, "X-OpenCAD-Request": "1"}
        if user:
            headers["Cookie"] = self.cookies[user]
        if project:
            headers["X-OpenCAD-Project"] = project
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        started = time.monotonic()
        try:
            connection.request(method, route, body=None if body is None else json.dumps(body),
                               headers=headers)
            response = connection.getresponse()
            data = response.read()
            received = dict(response.getheaders())
            self.requests.append({"method": method, "route": route, "status": response.status,
                                  "bytes": len(data), "seconds": time.monotonic() - started})
            value = (data if raw or not received.get("Content-Type", "").startswith("application/json")
                     else json.loads(data))
            return response.status, received, value
        finally:
            connection.close()

    def login(self, user):
        code, headers, response = self.call(
            "POST", "/api/auth/login", {"username": user, "password": self.passwords[user]})
        self.check("login-" + user, code == 200 and response["ok"])
        self.cookies[user] = headers["Set-Cookie"].split(";", 1)[0]

    def state(self, head):
        code, _, value = self.call("GET", "/api/state", user="alice", project=head["project_id"])
        self.check("state-read", code == 200)
        return value

    def mutate(self, head, path, fields):
        body = {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex, **fields}
        code, _, value = self.call("POST", path, body, user="alice", project=head["project_id"])
        return code, value, body

    @staticmethod
    def definition(doc):
        cut = doc["features"][-1]["uuid"]
        circle = next(item for sketch in doc["sketches"] for item in sketch["entities"]
                      if item["kind"] == "circle")
        return {"title": "Private associative plate", "views": ["top", "front", "right"],
                "sections": [{"name": "section-y", "axis": "y", "offset_nm": 10 * NM,
                              "keep": "positive"}],
                "dimensions": [
                    {"id": "width", "kind": "extent", "axis": "x", "view": "top",
                     "ref": [cut, "extent:x"]},
                    {"id": "thickness", "kind": "extent", "axis": "z", "view": "front",
                     "ref": [cut, "extent:z"]},
                    {"id": "hole", "kind": "diameter", "view": "top",
                     "ref": [circle["uuid"], "diameter"]}],
                "bom_refs": [[doc["meta"]["doc_uuid"], "model"]]}

    def drawing(self, head, label, width):
        code, _, value = self.call("GET", "/api/drawing", user="alice",
                                  project=head["project_id"])
        self.check(label + "-drawing-read", code == 200 and value["defined"])
        self.check(label + "-version", value["revision"] == value["model_version"]
                   == head["revision"])
        document = self.state(head)["doc"]
        self.check(label + "-document-binding",
                   value["source_document_sha256"] == digest(document.encode()))
        dimensions = {item["id"]: item["value_nm"] for item in value["dimensions"]}
        self.check(label + "-real-dimensions", dimensions == {
            "width": width * NM, "thickness": width * NM // 5, "hole": width * NM // 5},
            measured_nm=dimensions)
        section = next(item for item in value["views"] if item["name"] == "section-y")
        area = section["material_area_nm2"] / NM**2
        self.check(label + "-real-section",
                   math.isclose(area, (width - width / 5) * width / 5, abs_tol=1e-6),
                   material_area_mm2=area, material_faces=section["material_faces"])
        principal = [item for item in value["views"] if item["name"] in ("top", "front", "right")]
        self.check(label + "-real-hlr", all(item["hidden_measured"] for item in principal)
                   and any(item["hidden_curves"] > 0 for item in principal), views=principal)
        write(self.out / (label + "-drawing.json"), json.dumps(value, indent=2).encode())
        return value

    def run_command(self, argv, cwd, name):
        with (cwd / (name + ".log")).open("xb") as log:
            result = subprocess.run(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                    check=False, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.check(name + "-process", result.returncode == 0, argv=argv, rc=result.returncode)

    def export_readback(self, head, drawing, label):
        from cadcore.io.dxf_bridge import read_dxf, write_dxf
        from PIL import Image
        directory = self.out / label
        directory.mkdir()
        info = {}
        for fmt in ("svg", "pdf", "dxf"):
            code, headers, data = self.call("GET", "/api/drawing/" + fmt,
                                            user="alice", project=head["project_id"], raw=True)
            self.check(label + "-" + fmt + "-authorized-bytes", code == 200 and len(data) > 100)
            expected = {
                "X-OpenCAD-Model-Version": str(head["revision"]),
                "X-OpenCAD-Model-SHA256": drawing["source_model_sha256"],
                "X-OpenCAD-Document-SHA256": drawing["source_document_sha256"],
                "X-OpenCAD-Drawing-SHA256": drawing["drawing_sha256"],
                "X-OpenCAD-Artifact-SHA256": digest(data),
                "X-OpenCAD-Revision": str(head["revision"]),
                "X-OpenCAD-Snapshot-SHA256": head["sha256"],
            }
            self.check(label + "-" + fmt + "-version-headers",
                       all(headers.get(key) == value for key, value in expected.items()))
            write(directory / ("drawing." + fmt), data)
            info[fmt] = {"bytes": len(data), "sha256": digest(data), "binding": expected}
        svg = ET.fromstring((directory / "drawing.svg").read_bytes())
        layers = Counter(item.get("data-layer") for item in svg.iter() if item.get("data-layer"))
        self.check(label + "-svg-body", svg.tag == "{http://www.w3.org/2000/svg}svg"
                   and all(layers[layer] > 0 for layer in ("VISIBLE", "HIDDEN", "SECTION", "DIMENSIONS")))
        parsed = read_dxf((directory / "drawing.dxf").read_bytes())
        entities = parsed.entities()
        dxf_layers = Counter(item.get(8) for item in entities)
        self.check(label + "-dxf-coordinate-readback",
                   not parsed.warnings and write_dxf(parsed) == (directory / "drawing.dxf").read_bytes()
                   and all(dxf_layers[key] > 0 for key in layers), entities=len(entities))
        coordinate_entities = [item for item in entities if item.type in {"LINE", "CIRCLE"}]
        self.check(label + "-dxf-finite-geometry", bool(coordinate_entities)
                   and all(isinstance(item.get(code), (int, float)) and math.isfinite(item.get(code))
                           for item in coordinate_entities for code in (10, 20)))
        expected_text = [f"{item['id']} = {item['value_nm'] / NM:g} mm"
                         for item in drawing["dimensions"]]
        actual_text = [item.text() for item in entities if item.type == "TEXT"]
        self.check(label + "-dxf-real-dimension-text",
                   all(text in actual_text for text in expected_text))
        self.run_command(["pdftotext", "-layout", "drawing.pdf", "pdf-text.txt"],
                         directory, label + "-pdf-text")
        self.run_command(["pdftoppm", "-r", "72", "-singlefile", "drawing.pdf", "pdf-render"],
                         directory, label + "-pdf-render")
        text = " ".join((directory / "pdf-text.txt").read_text().split())
        self.check(label + "-pdf-body-dimensions",
                   all(value in text for value in expected_text)
                   and drawing["source_model_sha256"] in text)
        with Image.open(directory / "pdf-render.ppm") as image:
            pixels = image.convert("RGB")
            ink = sum(min(point) < 220 for point in pixels.getdata())
            self.check(label + "-pdf-rendered-ink", image.width > 500 and image.height > 400
                       and ink > 100, ink_pixels=ink, size=list(image.size))
        svg_text = ["".join(item.itertext()) for item in svg.iter()
                    if item.tag.endswith("}text")]
        self.check(label + "-svg-real-dimension-text",
                   all(value in svg_text for value in expected_text))
        info.update(svg_layers=dict(layers), dxf_layers=dict(dxf_layers), pdf_ink_pixels=ink)
        self.artifacts[label] = info

    def race(self, head, *, revoke):
        name = "revoked-export" if revoke else "changed-export"
        arrival, release = threading.Event(), threading.Event()
        self.app.export_barrier = (arrival, release)
        response, failures = [], []

        def request():
            try:
                response.append(self.call("GET", "/api/drawing/pdf", user="alice",
                                          project=head["project_id"]))
            except Exception as exc:
                failures.append(type(exc).__name__ + ": " + str(exc))

        thread = threading.Thread(target=request, daemon=True)
        thread.start()
        try:
            self.check(name + "-real-export-completed", arrival.wait(120))
            if revoke:
                code, _, _ = self.call("POST", "/api/auth/logout", {}, user="alice")
                self.check(name + "-logout", code == 200)
                latest = head
            else:
                code, latest, _ = self.mutate(head, "/api/drawing", {"definition": self.spec})
                self.check(name + "-competing-commit", code == 200)
        finally:
            release.set()
            thread.join(120)
            self.app.export_barrier = None
        self.check(name + "-joined", not thread.is_alive() and not failures, failures=failures)
        code, headers, value = response[0]
        expected = 401 if revoke else 409
        self.check(name + "-denies-artifact", code == expected and isinstance(value, dict)
                   and value.get("ok") is False
                   and "X-OpenCAD-Artifact-SHA256" not in headers, status=code)
        self.races.append({"name": name, "status": code, "error": value.get("error"),
                           "after_real_export": True})
        if revoke:
            self.login("alice")
        return latest

    def http_review(self):
        code, _, head = self.call("POST", "/api/projects", {
            "title": "Independent private drawing", "request_id": uuid.uuid4().hex}, user="alice")
        self.check("create-isolated-project", code == 201)
        document = self.state(head)["doc"]
        code, head, _ = self.mutate(head, "/api/parametric", {
            "action": "create_plate", "expected_document_sha256": digest(document.encode())})
        self.check("create-real-parametric-plate", code == 200)
        self.spec = self.definition(json.loads(self.state(head)["doc"]))
        code, head, _ = self.mutate(head, "/api/drawing", {"definition": self.spec})
        self.check("save-real-associated-definition", code == 200)
        narrow = self.drawing(head, "base", 40)
        self.export_readback(head, narrow, "base-exports")
        old = head
        code, head, _ = self.mutate(head, "/api/parametric", {
            "action": "rebuild", "configuration": "wide",
            "expected_document_sha256": narrow["source_document_sha256"]})
        self.check("model-size-change-real-rebuild", code == 200)
        wide = self.drawing(head, "wide", 60)
        self.check("automatic-association-changes", narrow["definition"] == wide["definition"]
                   and narrow["source_model_sha256"] != wide["source_model_sha256"]
                   and narrow["drawing_sha256"] != wide["drawing_sha256"]
                   and narrow["svg"] != wide["svg"])
        self.export_readback(head, wide, "wide-exports")
        before = self.state(head)
        code, _, _ = self.mutate(old, "/api/drawing", {"definition": self.spec})
        self.check("stale-cas-conflict", code == 409)
        bad = {**self.spec, "dimensions": [
            {**self.spec["dimensions"][0], "ref": ["missing-feature", "extent:x"]}]}
        code, _, _ = self.mutate(head, "/api/drawing", {"definition": bad})
        self.check("bad-reference-rejected", code == 422)
        after = self.state(head)
        self.check("errors-leave-document-and-history",
                   all(before[key] == after[key] for key in ("revision", "sha256", "doc", "undo_depth")))
        for user, expected in ((None, 401), ("bob", 403)):
            for fmt in ("svg", "pdf", "dxf"):
                code, headers, _ = self.call("GET", "/api/drawing/" + fmt, user=user,
                                            project=head["project_id"])
                self.check("export-access-" + str(user) + "-" + fmt,
                           code == expected and "X-OpenCAD-Artifact-SHA256" not in headers,
                           status=code)
        head = self.race(head, revoke=False)
        self.race(head, revoke=True)

    def browser_review(self, root):
        helper = load("private_drawing_browser_helper",
                      root / "oss-alternatives/tools/cad3d/viewer_controls_check.py")
        review = self

        class SafeBrowser(helper.BrowserSession):
            def click(self, selector):
                point = self.evaluate(
                    "(async function(){const n=document.querySelector(" + json.dumps(selector) + ");"
                    "if(!n)throw Error('missing target');n.scrollIntoView({block:'center'});"
                    "await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));"
                    "const b=n.getBoundingClientRect(),x=b.x+b.width/2,y=b.y+b.height/2;"
                    "const hit=document.elementFromPoint(x,y);return {x,y,"
                    "hit:!!hit&&(hit===n||n.contains(hit)),width:b.width,height:b.height,"
                    "hitTag:hit?.tagName,hitClass:hit?.className};})()")
                self.events.append({"method": "REAL_CLICK_HIT_TEST", "selector": selector,
                                    "point": point})
                if not point["hit"] or point["width"] <= 0 or point["height"] <= 0:
                    raise RuntimeError("Real click target obscured: " + selector)
                self.mouse("mouseMoved", point["x"], point["y"], buttons=0)
                self.mouse("mousePressed", point["x"], point["y"])
                self.mouse("mouseReleased", point["x"], point["y"])

            def call(self, method, params):
                result = self.helper._cdp_call(self.ws, self.sequence, method, params, 120)
                self.events.append({"method": method, "ok": "error" not in result})
                if "error" in result or "exceptionDetails" in result.get("result", {}):
                    raise RuntimeError("Browser operation failed: " + method)
                return result["result"]

            def __exit__(self, exc_type, exc, tb):
                if exc_type is not None and self.ws is not None:
                    try:
                        state = self.evaluate(
                            "({parametric:document.querySelector('.parametric-notice')?.textContent,"
                            "result:document.querySelector('.parametric-result')?.textContent,"
                            "drawing:document.querySelector('.drawing-panel-status')?.textContent,"
                            "project:document.querySelector('.project-status output')?.textContent,"
                            "requests:window.__drawingReviewRequests,"
                            "busy:window.opencad3d?.busy,"
                            "buttons:Array.from(document.querySelectorAll('button')).map(b=>"
                            "({text:b.textContent,hidden:b.hidden,disabled:b.disabled}))})")
                        write(self.out / "failure-state.json", json.dumps(state, indent=2).encode())
                        self.screenshot("failure")
                    except Exception:
                        review.log.write("Browser failure-state collection failed:\n"
                                         + traceback.format_exc())
                return super().__exit__(exc_type, exc, tb)

        with SafeBrowser(self.args.browser, self.out / "browser", source_root=root) as browser:
            browser.call("Page.addScriptToEvaluateOnNewDocument", {"source":
                "window.__drawingReviewRequests=[];"
                "const drawingReviewFetch=window.fetch.bind(window);"
                "window.fetch=async function(input,options){"
                "const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;"
                "const r=await drawingReviewFetch(input,options);"
                "if(path.startsWith('/api/'))window.__drawingReviewRequests.push("
                "{path,status:r.status});return r;};"})
            browser.call("Page.navigate", {"url": self.origin + "/web/3d.html"})
            browser.wait("!!document.querySelector('dialog[open] input[name=username]')")
            browser.click("input[name=username]")
            browser.call("Input.insertText", {"text": "alice"})
            browser.click("input[name=password]")
            browser.call("Input.insertText", {"text": self.passwords["alice"]})
            browser.click("dialog button[type=submit]")
            browser.wait("!!document.querySelector('input[aria-label=\"新项目名称\"]')")
            # Create through the real project form, not by injecting a transport.
            browser.click("input[aria-label=\"新项目名称\"]")
            browser.call("Input.insertText", {"text": "Drawing browser"})
            browser.click("dialog button:nth-of-type(2)")
            browser.wait("!document.querySelector('dialog')")
            browser.click(".industrial-workbench details:nth-of-type(1) summary")
            browser.wait("!!document.querySelector('[data-parametric-create]:not([hidden]):not(:disabled)')")
            browser.wait("!!window.opencad3d && !window.opencad3d.busy", timeout=120)
            self.check("browser-empty-project-ready", browser.evaluate(
                "!document.querySelector('[data-parametric-create]').disabled"))
            browser.click("[data-parametric-create]")
            browser.wait("!!document.querySelector('input[data-parameter=width]')", timeout=120)
            browser.wait("!window.opencad3d.busy", timeout=120)
            state = browser.evaluate("fetch('/api/state').then(r=>r.json())")
            spec = review.definition(json.loads(state["doc"]))
            browser.click(".industrial-workbench details:nth-of-type(4) summary")
            browser.wait("!!document.querySelector('.drawing-definition')")
            browser.wait("!document.querySelector('.drawing-panel button[type=submit]').disabled")
            browser.click(".drawing-definition")
            browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a",
                                                     "code": "KeyA", "modifiers": 2,
                                                     "windowsVirtualKeyCode": 65})
            browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a",
                                                     "code": "KeyA", "modifiers": 2,
                                                     "windowsVirtualKeyCode": 65})
            browser.call("Input.insertText", {"text": json.dumps(spec)})
            browser.click(".drawing-panel button[type=submit]")
            browser.wait("document.querySelector('.drawing-panel-status').textContent.includes('已重算')",
                         timeout=120)
            initial = browser.evaluate(
                "({binding:document.querySelector('.drawing-panel-binding').textContent,"
                "text:document.querySelector('.drawing-panel tbody').textContent,"
                "svg:document.querySelector('.drawing-panel-preview').innerHTML})")
            self.check("browser-real-form-save", "40 mm" in initial["text"] and "8 mm" in initial["text"])
            browser.click(".drawing-panel-preview")
            browser.screenshot("base-associated")
            browser.click("input[data-parameter=width]")
            browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a",
                                                     "code": "KeyA", "modifiers": 2,
                                                     "windowsVirtualKeyCode": 65})
            browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a",
                                                     "code": "KeyA", "modifiers": 2,
                                                     "windowsVirtualKeyCode": 65})
            browser.call("Input.insertText", {"text": "60 * mm"})
            browser.click("[data-parametric-rebuild]")
            browser.wait("document.querySelector('.parametric-result').textContent.includes('重建已保存')",
                         timeout=120)
            browser.wait("document.querySelector('.drawing-panel tbody').textContent.includes('60 mm')",
                         timeout=120)
            latest = browser.evaluate(
                "({binding:document.querySelector('.drawing-panel-binding').textContent,"
                "text:document.querySelector('.drawing-panel tbody').textContent,"
                "svg:document.querySelector('.drawing-panel-preview').innerHTML})")
            self.check("browser-model-change-auto-rebuild", "12 mm" in latest["text"]
                       and latest["binding"] != initial["binding"] and latest["svg"] != initial["svg"])
            browser.click(".drawing-panel-preview")
            browser.screenshot("wide-associated")
            for fmt in ("svg", "pdf", "dxf"):
                browser.click("[data-drawing-export=" + fmt + "]")
                browser.wait("document.querySelector('.drawing-panel-status').textContent"
                             ".includes('已下载同版本 " + fmt.upper() + "')", timeout=120)
                self.check("browser-real-download-" + fmt, True)
            write(self.out / "browser-bindings.json",
                  json.dumps({"base": initial, "wide": latest}, indent=2).encode())

    def execute(self):
        root = self.args.source_root.resolve(strict=True)
        sys.path.insert(0, str(root / "oss-alternatives/apps/opencad"))
        from access_control import AuthController
        from project_store import ProjectStore
        from project_gateway import ProjectGateway
        from secure_web import build_server

        class BarrierGateway(ProjectGateway):
            export_barrier = None

            def _current(self, head, principal):
                barrier = self.export_barrier
                if barrier is not None:
                    barrier[0].set()
                    if not barrier[1].wait(120):
                        raise RuntimeError("Export race release not received")
                return super()._current(head, principal)

        runtime = self.args.runtime_root.resolve(strict=True) / ("drawing-review-" + uuid.uuid4().hex)
        runtime.mkdir()
        self.runtime = runtime
        auth = AuthController(runtime / "accounts", allow_registration=True)
        store = ProjectStore(runtime / "projects")
        self.passwords = {name: uuid.uuid4().hex for name in ("alice", "bob")}
        for name, password in self.passwords.items():
            auth.register(name, password)
        self.app = BarrierGateway(auth, store, runtime / "requests", secure_cookies=False)
        server = build_server(self.app)
        self.port = server.server_address[1]
        self.origin = "http://127.0.0.1:" + str(self.port)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            if self.args.stage in {"all", "http"}:
                self.login("alice")
                self.login("bob")
                self.http_review()
            if self.args.stage in {"all", "browser"}:
                self.browser_review(root)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)
            self.app.close()
            if thread.is_alive():
                raise RuntimeError("Owned test server did not stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("/data/dev/opencad"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path,
                        default=Path("/data/dev/opencad-private-fixtures"))
    parser.add_argument("--browser", default=DEFAULT_BROWSER)
    parser.add_argument("--stage", choices=("all", "http", "browser"), default="all",
                        help="Explicit stage; a browser-only result is not HTTP acceptance")
    args = parser.parse_args()
    root = args.source_root.resolve(strict=True)
    out = args.out.absolute()
    if not out.parent.is_dir() or any(path.is_symlink() for path in (out, *out.parents)):
        parser.error("Out requires an existing non-symlink external parent")
    if (out.is_relative_to(root) or args.runtime_root.resolve().is_relative_to(root)
            or args.runtime_root.resolve().is_relative_to(out)):
        parser.error("Evidence/runtime must be external and distinct from sources")
    out.mkdir(exist_ok=False)
    report = {"verdict": "FAIL", "started_at": Review.now(), "source_root": str(root),
              "stage": args.stage,
              "production_mutated": False, "permissions_mutated": False,
              "scope": "isolated private HTTP/browser and real associative BRep drawing",
              "boundaries": ["TLS/production proxy NOT_ASSESSED", "multi-tenant deployment NOT_ASSESSED",
                             "GB/ISO NOT_ASSESSED", "GD&T/MBD NOT_IMPLEMENTED",
                             "permission incident NOT_CLOSED"]}
    before = source_files(root)
    started = time.monotonic()
    with (out / "review.log").open("x") as log:
        review = Review(args, out, log)
        original_stderr = sys.stderr
        sys.stderr = log
        try:
            review.execute()
            report["verdict"] = "PASS"
        except Exception:
            report["first_cause"] = traceback.format_exc()
            log.write(report["first_cause"])
            log.flush()
        finally:
            sys.stderr = original_stderr
            after = source_files(root)
            report.update(checks=review.checks, requests=review.requests, races=review.races,
                          artifacts=review.artifacts, runtime_root=str(getattr(review, "runtime", "")),
                          source_sha256=before, source_drift=sorted(
                              name for name in set(before) | set(after)
                              if before.get(name) != after.get(name)),
                          reviewer_sha256=digest(Path(__file__).read_bytes()),
                          finished_at=Review.now(), wall_s=time.monotonic() - started)
            if report["source_drift"]:
                report["verdict"] = "FAIL"
            line = (f"PRIVATE_DRAWING_REVIEW verdict={report['verdict']} "
                    f"checks={len(review.checks)} failed={sum(not item['pass'] for item in review.checks)}"
                    f" time={report['finished_at']}")
            log.write(line + "\n")
    write(out / "result.json", json.dumps(report, indent=2, ensure_ascii=True).encode() + b"\n")
    print(line + " evidence=" + str(out), flush=True)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
