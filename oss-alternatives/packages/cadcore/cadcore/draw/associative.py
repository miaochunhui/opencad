"""Version-bound, fail-loud BRep drawings, independent of shared server modules.

Public API: rebuild_drawing, synchronize_drawing, export_drawing, readback_export.
A DrawingSnapshot stores canonical JSON, not mutable geometry handles. Integrators
must call synchronize after model edits and publish only its successful return.
Server-supplied work_dir/out_dir are trusted execution configuration, never body
paths. Specifications contain references and dimensions, not filenames.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import traceback
from typing import Any, Mapping

from ..docmodel import document_to_l1
from ..errors import CadError
from ..kernel.subproc import SubprocessKernel

SCHEMA = "associative-drawing/1"
NM = 1_000_000
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
DEFAULT_WORK_DIR = Path("/hf3fs-jd/data/tmp/opencad-associative-drawings-20261011")
WORKER = Path(__file__).resolve().parents[1] / "kernel/drawing_worker.py"
__all__ = ["DrawingSnapshot", "AssociativeDrawingError", "rebuild_drawing",
           "synchronize_drawing", "export_drawing", "readback_export",
           "source_document_sha256", "assert_current_drawing"]


class AssociativeDrawingError(CadError):
    pass


def fail(message: str, reason: str, **facts: Any) -> None:
    raise AssociativeDrawingError(message, reason=reason, **facts)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_document_sha256(document: Any) -> str:
    """Hash exactly the canonical L1 bytes also used by the CAD document store."""
    return sha(document_to_l1(document).encode("utf-8"))


@dataclass(frozen=True)
class DrawingSnapshot:
    """Immutable, serializable snapshot; to_dict always returns a fresh copy."""

    text: str

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.text)

    @property
    def source_doc_sha256(self) -> str:
        return self.to_dict()["source_doc_sha256"]

    @property
    def model_version(self) -> int:
        return self.to_dict()["model_version"]


def integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        fail(f"{name} must be an integer >= {minimum}", "bad_drawing_spec")
    return value


def token(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        fail(f"{name} must be a bounded nonempty string", "bad_drawing_spec")
    if not value.isascii() or any(ord(item) < 32 or ord(item) == 127 for item in value):
        fail(f"{name} requires printable ASCII in this export profile", "unsupported_drawing_text")
    return value


def exact_keys(value: Any, allowed: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        fail(f"{name} contains unknown fields or is not an object", "bad_drawing_spec")
    return value


def sequence(value: Any, name: str, limit: int) -> list:
    if not isinstance(value, list) or len(value) > limit:
        fail(f"{name} must be a list with at most {limit} entries", "bad_drawing_spec")
    return value


def reference(value: Any) -> tuple[str, str]:
    if not isinstance(value, list) or len(value) != 2:
        fail("drawing refs must be [uuid, named-measurement]", "bad_drawing_reference")
    return token(value[0], "ref uuid"), token(value[1], "ref name")


def payload_of(document: Any) -> tuple[dict, str]:
    text = document_to_l1(document)
    if not 0 < len(text.encode("utf-8")) <= MAX_DOCUMENT_BYTES:
        fail("document exceeds drawing size limit", "drawing_document_size")
    return json.loads(text), sha(text.encode("utf-8"))


def normalized_spec(spec: Mapping[str, Any]) -> dict:
    try:
        data = json.loads(canonical(spec))
    except (TypeError, ValueError) as exc:
        fail(f"spec is not finite JSON: {exc}", "bad_drawing_spec")
    exact_keys(data, {"views", "sections", "dimensions", "bom_refs", "title"}, "spec")
    views = sequence(data.get("views", ["top", "front", "right"]), "views", 4)
    if not views or any(view not in ("top", "front", "right", "iso") for view in views):
        fail("views must select real principal or isometric projections", "bad_drawing_spec")
    if len(views) != len(set(views)):
        fail("duplicate view", "bad_drawing_spec")
    data["views"] = views
    data["title"] = token(data.get("title", "Associative drawing"), "title")
    data["sections"] = sequence(data.get("sections", []), "sections", 4)
    names = set(views)
    for entry in data["sections"]:
        exact_keys(entry, {"name", "axis", "offset_nm", "keep"}, "section")
        name = token(entry.get("name"), "section name")
        if name in names:
            fail("duplicate section/view name", "bad_drawing_spec")
        names.add(name)
        if entry.get("axis") not in ("x", "y", "z"):
            fail("section axis must be x/y/z", "bad_drawing_spec")
        if isinstance(entry.get("offset_nm"), bool) or not isinstance(entry.get("offset_nm"), int):
            fail("section offset_nm must be an integer", "bad_drawing_spec")
        if entry.get("keep") not in ("positive", "negative"):
            fail("section keep must be positive/negative", "bad_drawing_spec")
    data["dimensions"] = sequence(data.get("dimensions", []), "dimensions", 32)
    ids = set()
    for entry in data["dimensions"]:
        exact_keys(entry, {"id", "ref", "kind", "axis", "view"}, "dimension")
        key = token(entry.get("id"), "dimension id")
        if key in ids:
            fail("duplicate dimension id", "bad_drawing_spec")
        ids.add(key)
        reference(entry.get("ref"))
        if entry.get("view") not in views:
            fail("dimensions currently require a principal model view", "bad_drawing_spec")
        if entry.get("kind") == "extent":
            if entry.get("axis") not in ("x", "y", "z"):
                fail("extent axis must be x/y/z", "bad_drawing_spec")
        elif entry.get("kind") != "diameter" or "axis" in entry:
            fail("dimension supports extent or circle diameter only", "bad_drawing_spec")
    data["bom_refs"] = sequence(data.get("bom_refs", []), "bom_refs", 128)
    for ref in data["bom_refs"]:
        reference(ref)
    if len({tuple(ref) for ref in data["bom_refs"]}) != len(data["bom_refs"]):
        fail("duplicate BOM ref", "bad_drawing_reference")
    return data


def resolved_references(document: dict, spec: dict) -> tuple[list[dict], list[dict]]:
    features = document.get("features", [])
    sketches = document.get("sketches", [])
    if not isinstance(features, list) or not isinstance(sketches, list):
        fail("document feature/sketch tables malformed", "bad_drawing_reference")
    active = [item for item in features if isinstance(item, dict) and not item.get("suppressed", False)]
    if not active:
        fail("drawing requires a real solid feature", "drawing_no_solid")
    last = active[-1].get("uuid")
    by_uuid = {}
    for sketch in sketches:
        if not isinstance(sketch, dict) or not isinstance(sketch.get("entities", []), list):
            fail("invalid sketch table", "bad_drawing_reference")
        for entity in sketch.get("entities", []):
            if not isinstance(entity, dict):
                fail("invalid entity", "bad_drawing_reference")
            uid = entity.get("uuid")
            if uid:
                if uid in by_uuid:
                    fail("ambiguous entity uuid", "bad_drawing_reference")
                by_uuid[uid] = (sketch, entity)
    dimensions = []
    for entry in spec["dimensions"]:
        request = dict(entry)
        uid, named = reference(entry["ref"])
        if entry["kind"] == "extent":
            if uid != last or named != "extent:" + entry["axis"]:
                fail("extent ref must name the final live solid feature and axis",
                     "bad_drawing_reference")
        else:
            if uid not in by_uuid or named != "diameter":
                fail("diameter ref must name an existing circle entity", "bad_drawing_reference")
            sketch, entity = by_uuid[uid]
            if (entity.get("kind") != "circle" or entity.get("construction", False)
                    or sketch.get("plane", "XY") != "XY"):
                fail("diameter profile currently supports live XY sketch circles only",
                     "unsupported_drawing_reference")
            if not any(item.get("sketch_uuid") == sketch.get("uuid") for item in active):
                fail("circle sketch is not used by an active solid feature", "bad_drawing_reference")
            geom = entity.get("geom", {})
            if not isinstance(geom, dict):
                fail("circle geometry malformed", "bad_drawing_reference")
            coords = [geom.get("cx"), geom.get("cy"), 0]
            if any(isinstance(item, bool) or not isinstance(item, int) for item in coords):
                fail("circle coordinates must be integer nm", "bad_drawing_reference")
            request["selector"] = {"center_nm": coords,
                                   "radius_nm": integer(geom.get("r"), "radius", minimum=1),
                                   "normal": [0, 0, 1]}
        dimensions.append(request)
    assembly = document.get("sw_assembly", {})
    if not isinstance(assembly, dict):
        fail("BOM assembly table malformed", "bad_drawing_reference")
    components = assembly.get("components", [])
    if not isinstance(components, list):
        fail("BOM component table malformed", "bad_drawing_reference")
    live = [item for item in components if isinstance(item, dict)
            and not item.get("suppressed", False)]
    rows = []
    doc_uuid = document.get("meta", {}).get("doc_uuid")
    for ref in spec["bom_refs"]:
        uid, named = reference(ref)
        if uid == doc_uuid and named == "model":
            rows.append({"ref": ref, "part_key": uid, "quantity": 1,
                         "geometry": "current_model", "material": "UNMEASURED"})
            continue
        matches = [item for item in live if item.get("uuid") == uid]
        if len(matches) != 1 or named != "component" or matches[0].get("kind") == "subassembly":
            fail("BOM ref does not resolve to one live component", "bad_drawing_reference")
        key = token(matches[0].get("part_key"), "part_key")
        rows.append({"ref": ref, "part_key": key, "quantity": 1,
                     "geometry": "UNMEASURED", "material": "UNMEASURED"})
    return dimensions, rows


def run_logged(argv: list[str], cwd: Path, label: str, *, env: dict | None = None,
               timeout_s: float = 120) -> subprocess.CompletedProcess:
    with (cwd / (label + ".log")).open("xb") as handle:
        try:
            process = subprocess.run(argv, cwd=cwd, env=env, stdout=handle,
                                     stderr=subprocess.STDOUT, timeout=timeout_s)
        except (OSError, subprocess.TimeoutExpired) as exc:
            fail(f"{label} execution failed: {exc}", "drawing_process_failed",
                 evidence_dir=str(cwd))
    if process.returncode:
        detail = ""
        if label == "worker" and (cwd / "worker-result.json").is_file():
            try:
                detail = str(json.loads((cwd / "worker-result.json").read_text()).get("error", ""))
            except (ValueError, OSError) as exc:
                detail = "worker failure result unreadable: " + str(exc)
        fail(f"{label} exited {process.returncode}", "drawing_process_failed",
             rc=process.returncode, first_cause=detail, evidence_dir=str(cwd))
    return process


def rebuild_drawing(document: Any, *, model_version: int, spec: Mapping[str, Any],
                    kernel: SubprocessKernel | None = None,
                    work_dir: str | os.PathLike = DEFAULT_WORK_DIR) -> DrawingSnapshot:
    """Rebuild all views/refs from one source; never modify the supplied document."""
    integer(model_version, "model_version")
    payload, document_sha = payload_of(document)
    checked_spec = normalized_spec(spec)
    dimensions, bom = resolved_references(payload, checked_spec)
    root = Path(work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="drawing-", dir=root))
    scripts = directory / "scripts"
    scripts.mkdir()
    own_kernel = kernel is None
    if kernel is None:
        kernel = SubprocessKernel(work_dir=directory / "kernel", keep_work_dir=True)
    try:
        if not isinstance(kernel, SubprocessKernel):
            fail("associative drawings require the real subprocess BRep kernel", "drawing_kernel_required")
        report = kernel.probe_report()
        if not report.available or not report.executable:
            fail("FreeCAD backend unavailable", "drawing_kernel_unavailable", evidence_dir=str(directory))
        shape = kernel.shape_from_document(payload)
        step_path = directory / "model.step"
        kernel.export(shape, fmt="step", path=str(step_path))
        step_sha = sha(step_path.read_bytes())
        worker_bytes = WORKER.read_bytes()
        (scripts / "drawing_worker.py").write_bytes(worker_bytes)
        job = {"source_doc_sha256": document_sha, "model_version": model_version,
               "step_sha256": step_sha, "views": checked_spec["views"],
               "sections": checked_spec["sections"], "dimensions": dimensions}
        (directory / "job.json").write_text(canonical(job) + "\n", encoding="utf-8")
        env = dict(os.environ)
        env.update(OPENCAD_DRAWING_JOB=str(directory / "job.json"),
                   PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen")
        process = run_logged([report.executable, str(scripts / "drawing_worker.py")],
                             directory, "worker", env=env)
        result = json.loads((directory / "worker-result.json").read_text(encoding="utf-8"))
        if (result.get("ok") is not True
                or "DRAWING_WORKER verdict=PASS" not in (directory / "worker.log").read_text()
                or result.get("source_doc_sha256") != document_sha
                or result.get("model_version") != model_version
                or result.get("step_sha256") != step_sha
                or result.get("worker_sha256") != sha(worker_bytes)):
            fail("worker result/binding invalid", "drawing_worker_invalid", evidence_dir=str(directory))
        snapshot = {**result, "schema": SCHEMA, "spec": checked_spec,
                    "spec_sha256": sha(canonical(checked_spec).encode()),
                    "producer_sha256": sha(Path(__file__).read_bytes()),
                    "kernel_adapter_sha256": sha(kernel._profile.adapter_script.encode()),
                    "kernel_name": kernel.name,
                    "bom": bom, "worker_rc": process.returncode,
                    "evidence_dir": str(directory), "standards": "NOT_ASSESSED",
                    "gdt_mbd": "NOT_IMPLEMENTED"}
        snapshot["content_sha256"] = sha(canonical(snapshot).encode())
        text = canonical(snapshot)
        (directory / "drawing.json").write_text(text + "\n", encoding="utf-8")
        (directory / "result.log").write_text(
            f"ASSOCIATIVE_DRAWING verdict=PASS time={datetime.now(timezone.utc).isoformat()}"
            f" doc_sha256={document_sha} model_version={model_version}\n", encoding="utf-8")
        return DrawingSnapshot(text)
    except Exception as exc:
        (directory / "failure.log").write_text(
            f"ASSOCIATIVE_DRAWING verdict=FAIL time={datetime.now(timezone.utc).isoformat()}"
            f" error={type(exc).__name__}: {exc}\n{traceback.format_exc()}", encoding="utf-8")
        raise
    finally:
        if own_kernel:
            kernel.close()


def checked_snapshot(snapshot: DrawingSnapshot) -> dict:
    if not isinstance(snapshot, DrawingSnapshot):
        fail("expected immutable DrawingSnapshot", "bad_drawing_snapshot")
    data = snapshot.to_dict()
    content_sha = data.pop("content_sha256", None)
    if data.get("schema") != SCHEMA or sha(canonical(data).encode()) != content_sha:
        fail("drawing snapshot integrity failed", "bad_drawing_snapshot")
    data["content_sha256"] = content_sha
    return data


def assert_current_drawing(snapshot: DrawingSnapshot, document: Any, *, model_version: int) -> None:
    """Web integration gate: refuse stale source/version or changed drawing code."""
    data = checked_snapshot(snapshot)
    integer(model_version, "model_version")
    if (source_document_sha256(document) != data["source_doc_sha256"]
            or model_version != data["model_version"]):
        fail("drawing is not bound to the current document/version", "stale_drawing")
    if (sha(WORKER.read_bytes()) != data["worker_sha256"]
            or sha(Path(__file__).read_bytes()) != data["producer_sha256"]):
        fail("drawing producer changed; rebuild required", "stale_drawing")


def synchronize_drawing(previous: DrawingSnapshot, document: Any, *, model_version: int,
                        spec: Mapping[str, Any] | None = None,
                        kernel: SubprocessKernel | None = None,
                        work_dir: str | os.PathLike = DEFAULT_WORK_DIR) -> DrawingSnapshot:
    """Return cached same-source snapshot or rebuild; failures leave previous intact."""
    data = checked_snapshot(previous)
    integer(model_version, "model_version")
    current_sha = source_document_sha256(document)
    if model_version < data["model_version"] or (
            current_sha != data["source_doc_sha256"] and model_version <= data["model_version"]):
        fail("model changed without a newer version, or version regressed", "drawing_version_conflict")
    checked_spec = normalized_spec(data["spec"] if spec is None else spec)
    adapter = kernel if kernel is not None else SubprocessKernel()
    if not isinstance(adapter, SubprocessKernel):
        fail("associative drawings require the real subprocess BRep kernel", "drawing_kernel_required")
    if (current_sha == data["source_doc_sha256"]
            and model_version == data["model_version"]
            and sha(canonical(checked_spec).encode()) == data["spec_sha256"]
            and sha(WORKER.read_bytes()) == data["worker_sha256"]
            and sha(Path(__file__).read_bytes()) == data["producer_sha256"]
            and sha(adapter._profile.adapter_script.encode()) == data["kernel_adapter_sha256"]
            and adapter.name == data["kernel_name"]):
        return previous
    return rebuild_drawing(document, model_version=model_version, spec=checked_spec,
                           kernel=kernel, work_dir=work_dir)


def scene(data: dict) -> tuple[list[dict], list[str]]:
    """Create page-space primitives from measured geometry; no invented model edges."""
    primitives = []
    text_lines = [data["spec"]["title"], f"Model version {data['model_version']}",
                  "Doc SHA " + data["source_doc_sha256"],
                  "Standards NOT_ASSESSED; GD&T/MBD NOT_IMPLEMENTED"]
    views = data["views"]
    columns = 2
    rows = math.ceil(len(views) / columns)
    cell_w, cell_h = 140, 115 / rows
    for index, view in enumerate(views):
        curves = view["curves"] + view.get("profile", [])
        points = [point for curve in curves for point in curve["points_nm"]]
        if not points:
            fail("cannot export an empty view", "drawing_empty_view")
        lows = [min(point[i] for point in points) for i in range(2)]
        highs = [max(point[i] for point in points) for i in range(2)]
        scale = min((cell_w - 24) / max(1, (highs[0] - lows[0]) / NM),
                    (cell_h - 22) / max(1, (highs[1] - lows[1]) / NM))
        origin = [10 + index % columns * cell_w,
                  65 + (rows - 1 - index // columns) * cell_h]

        def mapped(point):
            return [origin[i] + 10 + (point[i] - lows[i]) / NM * scale for i in range(2)]

        primitives.append({"type": "TEXT", "layer": "LABEL",
                           "at": [origin[0] + 8, origin[1] + cell_h - 5], "text": view["name"]})
        for curve in curves:
            layer = curve["kind"].upper()
            if curve["geometry"] == "circle":
                primitives.append({"type": "CIRCLE", "layer": layer,
                                   "at": mapped(curve["center_nm"]),
                                   "radius": curve["radius_nm"] / NM * scale})
            else:
                for first, second in zip(curve["points_nm"], curve["points_nm"][1:]):
                    primitives.append({"type": "LINE", "layer": layer,
                                       "a": mapped(first), "b": mapped(second)})
        for dimension in data["dimensions"]:
            if dimension["view"] != view["name"]:
                continue
            first, second = [mapped(point) for point in dimension["witnesses_nm"]]
            primitives.append({"type": "LINE", "layer": "DIMENSIONS", "a": first, "b": second})
            label = f"{dimension['id']} = {dimension['value_nm'] / NM:g} mm"
            text_lines.append(label)
            primitives.append({"type": "TEXT", "layer": "DIMENSIONS",
                               "at": [first[0], first[1] - 4], "text": label})
    for row in data["bom"]:
        text_lines.append(f"BOM {row['part_key']} qty={row['quantity']}")
    if len(text_lines) > 14:
        fail("single-page export supports at most ten dimension/BOM summary rows",
             "drawing_layout_capacity")
    for index, line in enumerate(text_lines):
        at_y = 202 - index * 4 if index < 4 else 8 + (index - 4) * 4
        primitives.append({"type": "TEXT", "layer": "LABEL", "at": [10, at_y],
                           "text": line})
    return primitives, text_lines


def write_pdf(primitives: list[dict], path: Path) -> None:
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        fail("reportlab required for PDF export", "drawing_pdf_dependency")
    pt = 72 / 25.4
    page = canvas.Canvas(str(path), pagesize=(297 * pt, 210 * pt), invariant=1)
    page.setFont("Helvetica", 7)
    for item in primitives:
        if item["type"] == "TEXT" and (
                page.stringWidth(item["text"], "Helvetica", 7) > (287 - item["at"][0]) * pt):
            fail("text exceeds single-page export width", "drawing_layout_capacity")
        page.setDash([2 * pt, pt] if item["layer"] == "HIDDEN" else [])
        page.setLineWidth((0.35 if item["layer"] == "SECTION" else 0.18) * pt)
        if item["type"] == "LINE":
            page.line(*(value * pt for point in (item["a"], item["b"]) for value in point))
        elif item["type"] == "CIRCLE":
            page.circle(item["at"][0] * pt, item["at"][1] * pt, item["radius"] * pt)
        else:
            page.drawString(item["at"][0] * pt, item["at"][1] * pt, item["text"])
    page.showPage()
    page.save()


def write_dxf(primitives: list[dict], path: Path) -> None:
    tags = []

    def add(*pairs):
        for code, value in pairs:
            tags.extend((str(code), str(value)))

    add((0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1015"),
        (9, "$INSUNITS"), (70, 4), (0, "ENDSEC"),
        (0, "SECTION"), (2, "TABLES"), (0, "TABLE"), (2, "LTYPE"), (70, 2))
    for name, segments in (("CONTINUOUS", []), ("DASHED", [2.0, -1.0])):
        add((0, "LTYPE"), (2, name), (70, 0), (3, name), (72, 65),
            (73, len(segments)), (40, sum(abs(value) for value in segments)))
        for value in segments:
            add((49, value), (74, 0))
    add((0, "ENDTAB"), (0, "TABLE"), (2, "LAYER"), (70, 5))
    for layer in ("VISIBLE", "HIDDEN", "SECTION", "DIMENSIONS", "LABEL"):
        add((0, "LAYER"), (2, layer), (70, 0), (62, 7),
            (6, "DASHED" if layer == "HIDDEN" else "CONTINUOUS"))
    add((0, "ENDTAB"), (0, "ENDSEC"), (0, "SECTION"), (2, "ENTITIES"))
    for item in primitives:
        add((0, item["type"]), (8, item["layer"]),
            (6, "DASHED" if item["layer"] == "HIDDEN" else "CONTINUOUS"))
        if item["type"] == "LINE":
            add((10, repr(item["a"][0])), (20, repr(item["a"][1])), (30, 0),
                (11, repr(item["b"][0])), (21, repr(item["b"][1])), (31, 0))
        elif item["type"] == "CIRCLE":
            add((10, repr(item["at"][0])), (20, repr(item["at"][1])), (30, 0),
                (40, repr(item["radius"])))
        else:
            add((10, repr(item["at"][0])), (20, repr(item["at"][1])),
                (30, 0), (40, 2.5), (1, item["text"]))
    add((0, "ENDSEC"), (0, "EOF"))
    path.write_bytes(("\n".join(tags) + "\n").encode("ascii"))


def readback_export(snapshot: DrawingSnapshot, *, export_dir: str | os.PathLike) -> dict:
    """Parse actual DXF coordinates and PDF text and raster bytes independently."""
    data = checked_snapshot(snapshot)
    directory = Path(export_dir)
    primitives, expected_text = scene(data)
    from ..io.dxf_bridge import read_dxf, write_dxf as bridge_write

    dxf_bytes = (directory / "drawing.dxf").read_bytes()
    parsed = read_dxf(dxf_bytes)
    entities = parsed.entities()
    if bridge_write(parsed) != dxf_bytes or parsed.warnings:
        fail("DXF parse/byte roundtrip failed", "drawing_dxf_readback")
    if len(entities) != len(primitives):
        fail("DXF entity count differs from measured scene", "drawing_dxf_readback")
    for entity, expected in zip(entities, primitives):
        if entity.type != expected["type"] or entity.get(8) != expected["layer"]:
            fail("DXF type/layer mismatch", "drawing_dxf_readback")
        if entity.type == "LINE":
            actual = [entity.get(code) for code in (10, 20, 11, 21)]
            wanted = expected["a"] + expected["b"]
        else:
            actual = [entity.get(code) for code in (10, 20)]
            wanted = list(expected["at"])
            if entity.type == "CIRCLE":
                actual.append(entity.get(40))
                wanted.append(expected["radius"])
            elif entity.text() != expected["text"]:
                fail("DXF dimension/BOM text mismatch", "drawing_dxf_readback")
        if any(not isinstance(value, (int, float)) or not math.isfinite(value)
               or abs(value - wanted[i]) > 1e-9 for i, value in enumerate(actual)):
            fail("DXF coordinates differ from measured scene", "drawing_dxf_readback")
    # Independent Poppler parsing and rendering, not PDF magic or producer inspection.
    readback = Path(tempfile.mkdtemp(prefix="readback-", dir=directory))
    pdf_path = str((directory / "drawing.pdf").resolve())
    run_logged(["pdftotext", "-layout", pdf_path, "pdf-text.txt"], readback, "pdf-text")
    run_logged(["pdftoppm", "-r", "72", "-singlefile", pdf_path, "pdf-render"],
               readback, "pdf-render")
    extracted = " ".join((readback / "pdf-text.txt").read_text().split())
    if any(" ".join(line.split()) not in extracted for line in expected_text):
        fail("PDF parsed body lacks measured dimensions/BOM/source binding", "drawing_pdf_readback")
    from PIL import Image

    with Image.open(readback / "pdf-render.ppm") as image:
        image.load()
        pixels = image.convert("RGB")
        ink = sum(1 for point in pixels.getdata() if min(point) < 220)
        if image.width < 500 or image.height < 400 or ink < 100:
            fail("PDF rendered bytes have no drawing", "drawing_pdf_readback")
        geometry_samples = Counter()
        for item in primitives:
            if item["type"] == "TEXT":
                continue
            if item["type"] == "LINE":
                samples = [[item["a"][i] + (item["b"][i] - item["a"][i]) * t
                            for i in range(2)] for t in (0.1, 0.3, 0.5, 0.7, 0.9)]
            else:
                samples = [[item["at"][0] + item["radius"] * math.cos(t),
                            item["at"][1] + item["radius"] * math.sin(t)]
                           for t in (0, math.pi / 2, math.pi, 3 * math.pi / 2)]
            observed = 0
            for x_mm, y_mm in samples:
                x = round(x_mm * 72 / 25.4)
                y = image.height - 1 - round(y_mm * 72 / 25.4)
                if any(min(pixels.getpixel((px, py))) < 220
                       for px in range(max(0, x - 3), min(image.width, x + 4))
                       for py in range(max(0, y - 3), min(image.height, y + 4))):
                    observed += 1
            if not observed:
                fail("PDF raster is missing a measured geometry primitive",
                     "drawing_pdf_readback", layer=item["layer"])
            geometry_samples[item["layer"]] += observed
        raster_sha = sha(pixels.tobytes())
        size = list(image.size)
    return {"verdict": "PASS", "dxf_entities": len(entities),
            "dxf_types": dict(Counter(item.type for item in entities)),
            "dxf_layers": dict(Counter(item.get(8) for item in entities)),
            "dxf_roundtrip_identical": True, "pdf_text_lines": len(expected_text),
            "pdf_raster_size": size, "pdf_ink_pixels": ink, "pdf_raster_sha256": raster_sha,
            "pdf_geometry_samples": dict(geometry_samples),
            "readback_dir": str(readback),
            "pdf_sha256": sha((directory / "drawing.pdf").read_bytes()),
            "dxf_sha256": sha(dxf_bytes), "snapshot_sha256": sha(snapshot.text.encode())}


def export_drawing(snapshot: DrawingSnapshot, *, document: Any, model_version: int,
                   out_dir: str | os.PathLike) -> dict:
    """Generate a fresh PDF/DXF pair and return only after real content readback."""
    assert_current_drawing(snapshot, document, model_version=model_version)
    data = checked_snapshot(snapshot)
    directory = Path(out_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    try:
        primitives, _ = scene(data)
        write_pdf(primitives, directory / "drawing.pdf")
        write_dxf(primitives, directory / "drawing.dxf")
        result = readback_export(snapshot, export_dir=directory)
        (directory / "readback.json").write_text(canonical(result) + "\n", encoding="utf-8")
        (directory / "result.log").write_text(
            f"DRAWING_EXPORT verdict=PASS time={datetime.now(timezone.utc).isoformat()}"
            f" dxf_entities={result['dxf_entities']} pdf_ink_pixels={result['pdf_ink_pixels']}\n",
            encoding="utf-8")
    except Exception as exc:
        (directory / "failure.log").write_text(
            f"DRAWING_EXPORT verdict=FAIL time={datetime.now(timezone.utc).isoformat()}"
            f" error={type(exc).__name__}: {exc}\n{traceback.format_exc()}", encoding="utf-8")
        raise
    return {"export_dir": str(directory), "pdf": str(directory / "drawing.pdf"),
            "dxf": str(directory / "drawing.dxf"), "readback": result}
