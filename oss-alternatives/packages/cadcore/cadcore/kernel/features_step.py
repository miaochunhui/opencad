"""Self-contained STEP recipes and a FreeCAD subprocess builder snippet.

The constructor validates the complete Part 21 text envelope, not geometry.
Only a successful Part.Shape.read plus solid validation proves a real import.
L1 features persist kind="import_step", params={"step_text": the exact text}.
This imports solid geometry only; it does not promise materials or PMI.
Inject ADAPTER_SOURCE after defining _EXTENDED_BUILDERS and before
_build_recipe; dispatch that registry at the end of _build_step.
"""

from __future__ import annotations

import inspect
import re

__all__ = ["ADAPTER_SOURCE", "MAX_STEP_BYTES", "make_step"]

MAX_STEP_BYTES = 8 * 1024 * 1024


def _validate_text(step_text: str) -> None:
    if not isinstance(step_text, str):
        raise TypeError("STEP text must be a string")
    if not step_text.strip():
        raise ValueError("STEP text must not be empty")
    if len(step_text) > MAX_STEP_BYTES:
        raise ValueError("STEP text exceeds the 8 MiB limit")
    if not step_text.isascii():
        raise ValueError("STEP text must be ASCII (and valid UTF-8)")
    if any((ord(char) < 32 and char not in "\t\r\n") or ord(char) == 127
           for char in step_text):
        raise ValueError("STEP text contains forbidden control characters")
    # Mask quoted strings/comments before recognizing section delimiters.
    masked = re.sub(r"'(?:[^']|'')*'|/\*.*?\*/",
                    lambda match: "''" if match.group().startswith("'") else "",
                    step_text, flags=re.DOTALL)
    if "'" in masked.replace("''", "") or "/*" in masked or "*/" in masked:
        raise ValueError("STEP text has an unterminated string or comment")
    match = re.fullmatch(
        r"\s*ISO-10303-21\s*;\s*HEADER\s*;(?P<header>.*?)"
        r"ENDSEC\s*;\s*DATA(?:\s*\([^;]*\))?\s*;(?P<data>.*?)"
        r"ENDSEC\s*;\s*END-ISO-10303-21\s*;\s*",
        masked, flags=re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        raise ValueError("STEP text must be a complete Part 21 file")
    header = [item.strip() for item in match["header"].split(";") if item.strip()]
    if len(header) < 3 or any(
        re.fullmatch(name + r"\s*\(.*\)", record, flags=re.DOTALL | re.IGNORECASE) is None
        for name, record in zip(("FILE_DESCRIPTION", "FILE_NAME", "FILE_SCHEMA"), header)
    ):
        raise ValueError("STEP header requires description, name and schema")
    records = [item.strip() for item in match["data"].split(";") if item.strip()]
    if not match["data"].rstrip().endswith(";") or not records or any(
        re.fullmatch(r"#\d+\s*=\s*(?:[A-Z][A-Z0-9_]*\s*\(.*\)|\(.*\))",
                     record, flags=re.DOTALL | re.IGNORECASE) is None
        for record in records
    ):
        raise ValueError("STEP DATA must contain complete entity records")


def make_step(*, step_text: str) -> dict[str, str]:
    """Keep the exact bounded text in the recipe; accept no input file path."""
    _validate_text(step_text)
    return {"op": "import_step", "step_text": step_text}


ADAPTER_SOURCE = (
    "\nimport re\n"
    "MAX_STEP_BYTES = %d\n\n" % MAX_STEP_BYTES
    + inspect.getsource(_validate_text)
    + r'''
import tempfile as _step_tempfile


def _build_import_step(step, previous):
    if set(step) != {"op", "step_text"} or step["op"] != "import_step":
        raise ValueError("import_step accepts only op and embedded step_text")
    text = step["step_text"]
    _validate_text(text)
    params_path, _result_path = _paths()
    job_dir = os.path.dirname(os.path.abspath(params_path))
    # mkstemp creates our own unique file, never an arbitrary caller's path.
    descriptor, path = _step_tempfile.mkstemp(prefix="import-step-", suffix=".step",
                                            dir=job_dir)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(text.encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
    try:
        shape = Part.Shape()
        shape.read(path)
        if shape.isNull() or not shape.isValid():
            raise ValueError("STEP read produced a null or invalid shape")
        solids = list(shape.Solids)
        volume = float(shape.Volume)
        if not solids or not math.isfinite(volume) or volume <= 0:
            raise ValueError("STEP read requires solids with positive finite volume")
        if any(not solid.isValid() or not math.isfinite(float(solid.Volume))
               or float(solid.Volume) <= 0 for solid in solids):
            raise ValueError("STEP read produced an invalid or nonpositive solid")
    except Exception as exc:
        raise ValueError("STEP import failed at %s: %s: %s" %
                         (path, type(exc).__name__, exc)) from exc
    return solids[0] if len(solids) == 1 else shape


_EXTENDED_BUILDERS["import_step"] = _build_import_step
'''
)
