"""Private Web drawing adapter; gateway owns authentication and route dispatch.

prepare_drawing(workspace, payload) returns a candidate without mutating input.
The gateway owns receipt/CAS/undo and must verify candidate's full base SHA.
No shared demo fallback or second commit protocol is installed here.
Definition metadata is excluded from the derived model used for BRep replay.
Runtime artifacts remain in the service-owned external directory.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
import tempfile

KEY = "x_industrial_drawing"
SCHEMA = "industrial-drawing-definition/1"


class DrawingWebError(Exception):
    def __init__(self, code, detail, status=422):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


@dataclass(frozen=True)
class DrawingExport:
    data: bytes
    content_type: str
    headers: dict


@dataclass(frozen=True)
class DrawingCandidate:
    before_document_sha256: str
    doc: str
    response_text: str

    @property
    def response(self):
        return json.loads(self.response_text)


def core():
    from cadcore.draw import associative
    return associative


def fields(body, allowed, required):
    if (not isinstance(body, dict) or set(body) - allowed or required - set(body)):
        raise DrawingWebError("bad_drawing_arguments", "Unexpected or missing drawing fields", 400)
    value = body.get("model_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DrawingWebError("bad_drawing_version", "Gateway must assign a positive model_version", 400)


def runtime(workspace):
    root = Path(workspace.out_root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise DrawingWebError("unsafe_drawing_runtime", "Drawing runtime cannot traverse links", 500)
    if root.resolve().is_relative_to(Path(__file__).resolve().parents[3]):
        raise DrawingWebError("unsafe_drawing_runtime", "Drawing runtime must be outside source", 500)
    return root.resolve()


def call(operation):
    try:
        return operation()
    except Exception as exc:
        from cadcore.errors import CadError
        if isinstance(exc, CadError):
            raise DrawingWebError(exc.reason or "drawing_failed", str(exc)) from exc
        raise


def model_document(workspace):
    """Remove only our definition metadata; preserve all actual model inputs."""
    value = json.loads(workspace.l1_text())
    value.pop(KEY, None)
    return value


def definition(workspace):
    raw = workspace.doc.extra.get(KEY)
    if raw is None:
        return None
    if (not isinstance(raw, dict) or set(raw) != {"schema", "definition", "definition_sha256"}
            or raw["schema"] != SCHEMA):
        raise DrawingWebError("bad_drawing_definition", "Stored drawing definition is invalid")
    value = call(lambda: core().normalized_spec(raw["definition"]))
    if core().sha(core().canonical(value).encode()) != raw["definition_sha256"]:
        raise DrawingWebError("bad_drawing_definition", "Stored drawing definition SHA changed")
    return value


def svg(snapshot):
    """Render the same measured scene as PDF/DXF, with escaped text and no script."""
    primitives, _ = core().scene(core().checked_snapshot(snapshot))
    output = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 297 210" role="img">',
        "<title>关联工程图：真实 BRep 投影</title>",
        '<rect width="297" height="210" fill="#fff"/>',
        '<g fill="none" stroke="#172c36" stroke-width=".18">',
    ]
    for item in primitives:
        layer = item["layer"]
        dash = ' stroke-dasharray="2 1"' if layer == "HIDDEN" else ""
        style = ' stroke-width=".35"' if layer == "SECTION" else ""
        if item["type"] == "LINE":
            a, b = item["a"], item["b"]
            output.append(
                f'<line data-layer="{layer}" x1="{a[0]:.9g}" y1="{210-a[1]:.9g}"'
                f' x2="{b[0]:.9g}" y2="{210-b[1]:.9g}"{dash}{style}/>')
        elif item["type"] == "CIRCLE":
            point = item["at"]
            output.append(
                f'<circle data-layer="{layer}" cx="{point[0]:.9g}" cy="{210-point[1]:.9g}"'
                f' r="{item["radius"]:.9g}"{dash}/>')
        else:
            point = item["at"]
            output.append(
                f'<text data-layer="{layer}" x="{point[0]:.9g}" y="{210-point[1]:.9g}"'
                f' stroke="none" fill="#172c36" font-family="serif" font-size="2.5">'
                f'{html.escape(item["text"])}</text>')
    output.extend(("</g>", "</svg>"))
    return "\n".join(output)


def summary(snapshot):
    data = core().checked_snapshot(snapshot)
    primitives, _ = core().scene(data)
    # Snapshot integrity includes ephemeral evidence paths. Client binding must
    # instead hash the measured scene, definition, version and actual producers.
    drawing_sha = core().sha(core().canonical({
        "scene": primitives, "spec": data["spec"],
        "source_model_sha256": data["source_doc_sha256"],
        "model_version": data["model_version"],
        "worker_sha256": data["worker_sha256"],
        "producer_sha256": data["producer_sha256"],
        "kernel_adapter_sha256": data["kernel_adapter_sha256"],
    }).encode())
    return {
        "ok": True, "defined": True, "definition": data["spec"],
        "source_model_sha256": data["source_doc_sha256"],
        "model_version": data["model_version"], "drawing_sha256": drawing_sha,
        "snapshot_sha256": data["content_sha256"],
        "worker_sha256": data["worker_sha256"], "producer_sha256": data["producer_sha256"],
        "kernel_adapter_sha256": data["kernel_adapter_sha256"],
        "views": [{"name": view["name"], "hidden_measured": view["hidden_measured"],
                   "source": view["source"],
                   "visible_curves": sum(c["kind"] == "visible" for c in view["curves"]),
                   "hidden_curves": sum(c["kind"] == "hidden" for c in view["curves"]),
                   **({"material_area_nm2": view["material_area_nm2"],
                       "material_faces": view["material_faces"]} if "section" in view else {})}
                  for view in data["views"]],
        "dimensions": data["dimensions"], "bom": data["bom"],
        "standards": "NOT_ASSESSED", "gdt_mbd": "NOT_IMPLEMENTED",
    }


def rebuild(workspace, model_version, spec):
    return call(lambda: core().rebuild_drawing(
        model_document(workspace), model_version=model_version,
        spec=spec, work_dir=runtime(workspace) / "drawing-worker"))


def prepare_drawing(workspace, body):
    """Return candidate(before_document_sha256, doc L1 text, response).

    The gateway assigns next authoritative model_version; this function never
    edits doc/history or confirms a commit. CAS/undo is exclusively gateway-owned.
    """
    fields(body, {"definition", "model_version"}, {"definition", "model_version"})
    with workspace._lock:
        spec = call(lambda: core().normalized_spec(body["definition"]))
        before = workspace.l1_text()
        drawing = rebuild(workspace, body["model_version"], spec)
        payload = json.loads(before)
        payload[KEY] = {
            "schema": SCHEMA, "definition": spec,
            "definition_sha256": core().sha(core().canonical(spec).encode()),
        }
        candidate = workspace.docmodel.document_from_l1(core().canonical(payload))
        problems = workspace.docmodel.load_blocking_problems(candidate)
        if problems:
            raise DrawingWebError("invalid_document", "; ".join(problems[:8]))
        response = summary(drawing)
        candidate_text = workspace.docmodel.document_to_l1(candidate)
        response["source_document_sha256"] = hashlib.sha256(candidate_text.encode()).hexdigest()
        return DrawingCandidate(hashlib.sha256(before.encode()).hexdigest(),
                                candidate_text, core().canonical(response))


handler = prepare_drawing


def read_drawing(workspace, body):
    """Read only: always derive from this model, never a stale persistent cache."""
    fields(body, {"model_version", "include_svg"}, {"model_version"})
    model_version = body["model_version"]
    if not isinstance(body.get("include_svg", True), bool):
        raise DrawingWebError("bad_drawing_arguments", "include_svg must be boolean", 400)
    with workspace._lock:
        document_sha = hashlib.sha256(workspace.l1_text().encode()).hexdigest()
        spec = definition(workspace)
        if spec is None:
            return {"ok": True, "defined": False, "definition": None,
                    "model_version": model_version,
                    "source_document_sha256": document_sha,
                    "source_model_sha256": core().source_document_sha256(model_document(workspace)),
                    "standards": "NOT_ASSESSED", "gdt_mbd": "NOT_IMPLEMENTED"}
        drawing = rebuild(workspace, model_version, spec)
        value = summary(drawing)
        value["source_document_sha256"] = document_sha
        if body.get("include_svg", True):
            value["svg"] = svg(drawing)
        return value


def export_drawing(workspace, body):
    """Independent GET export: returns bytes, never a user-supplied output path."""
    fields(body, {"model_version", "format"}, {"model_version", "format"})
    model_version, fmt = body["model_version"], body["format"]
    if not isinstance(fmt, str) or fmt not in {"svg", "pdf", "dxf"}:
        raise DrawingWebError("bad_drawing_format", "Use svg, pdf or dxf", 400)
    with workspace._lock:
        spec = definition(workspace)
        if spec is None:
            raise DrawingWebError("drawing_not_defined", "Save a drawing definition first", 404)
        drawing = rebuild(workspace, model_version, spec)
        document = model_document(workspace)
        core().assert_current_drawing(drawing, document, model_version=model_version)
        if fmt == "svg":
            data = svg(drawing).encode("utf-8")
            media = "image/svg+xml; charset=utf-8"
        else:
            directory = Path(tempfile.mkdtemp(prefix="export-", dir=runtime(workspace)))
            destination = directory / "artifact"
            result = call(lambda: core().export_drawing(
                drawing, document=document, model_version=model_version, out_dir=destination))
            data = Path(result[fmt]).read_bytes()
            media = "application/pdf" if fmt == "pdf" else "application/dxf"
        return DrawingExport(data, media, {
            "X-OpenCAD-Model-Version": str(model_version),
            "X-OpenCAD-Model-SHA256": drawing.source_doc_sha256,
            "X-OpenCAD-Document-SHA256": hashlib.sha256(workspace.l1_text().encode()).hexdigest(),
            "X-OpenCAD-Drawing-SHA256": summary(drawing)["drawing_sha256"],
            "X-OpenCAD-Artifact-SHA256": hashlib.sha256(data).hexdigest(),
            "Content-Disposition": f'attachment; filename="drawing.{fmt}"',
        })
