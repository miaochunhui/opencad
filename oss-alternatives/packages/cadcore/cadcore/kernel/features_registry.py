"""Compose independently tested FreeCAD feature implementations."""

from __future__ import annotations

import importlib
from collections.abc import Mapping

from cadcore.errors import CadError

MODULES = ("sweep", "loft", "edges", "shell", "pattern", "step", "clearance")
KINDS = frozenset(("sweep", "loft", "fillet", "chamfer", "shell", "pattern", "mirror", "import_step"))
PARAMETERS = {
    "sweep": {"sketch_uuid", "path_points_nm", "op"},
    "loft": {"profile_uuids", "offsets_nm", "ruled", "op"},
    "fillet": {"size_nm", "edge_indices"},
    "chamfer": {"size_nm", "edge_indices"},
    "shell": {"thickness_nm", "face_indices", "inward"},
    "pattern": {"translations_nm", "fuse"},
    "mirror": {"normal_axis", "offset_nm", "keep_original"},
    "import_step": {"step_text", "op"},
    "extrude": {"distance_nm", "symmetric", "op", "thin", "contours",
                "end_condition", "through_all", "flip", "merge", "feature_op",
                "profile_min_dim_nm", "angle_deg"},
    "revolve": {"axis", "angle_deg", "op", "thin", "contours",
                "end_condition", "through_all", "flip", "merge", "groove"},
}
EDIT_METADATA = {"edited", "edit_count", "edit_log", "name", "suppressed", "reorder_to"}


def validate_nm(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_nm(item)
    elif isinstance(value, bool) or not isinstance(value, int) or abs(value) > 2**53:
        raise CadError("length must be safe integer nanometres", reason="bad_feature_params")


def validate_parameters(kind, params):
    if kind not in PARAMETERS:
        raise CadError("unsupported solid feature", reason="unsupported_solid_feature")
    if not isinstance(params, Mapping):
        raise CadError("feature params must be a mapping", reason="bad_feature_params")
    if set(params).difference(PARAMETERS[kind] | EDIT_METADATA):
        raise CadError("unknown solid feature parameters", reason="bad_feature_params")
    if "op" in PARAMETERS[kind] and params.get("op", "new") not in (
            "new", "add", "union", "join", "cut", "pocket", "intersect"):
        raise CadError("unsupported feature operation", reason="unsupported_solid_feature")
    for name in ("symmetric", "through_all", "flip", "merge", "groove", "suppressed", "edited"):
        if name in params and not isinstance(params[name], bool):
            raise CadError(name + " must be boolean", reason="bad_feature_params")
    if params.get("groove"):
        raise CadError("groove construction is unsupported", reason="unsupported_solid_feature")
    for key, value in params.items():
        if key.endswith("_nm"):
            validate_nm(value)
    if kind == "extrude" and params.get("angle_deg", 0) not in (0, "0"):
        raise CadError("extrusion draft angle is unsupported", reason="unsupported_solid_feature")


def validate_document(document):
    sketches = document.get("sketches", [])
    features = document.get("features", [])
    if not isinstance(sketches, list) or not isinstance(features, list):
        raise CadError("sketches and features must be lists", reason="bad_features_table")
    ids = set()
    sketch_ids = set()
    for table, target in ((sketches, sketch_ids), (features, set())):
        for row in table:
            if not isinstance(row, Mapping):
                raise CadError("geometry table row must be a mapping", reason="bad_features_table")
            identifier = row.get("uuid")
            if identifier is not None:
                if not isinstance(identifier, str) or not identifier or identifier in ids:
                    raise CadError("duplicate or invalid geometry UUID", reason="bad_features_table")
                ids.add(identifier)
                target.add(identifier)
    prior = set()
    live = set()
    for feature in features:
        kind = feature.get("kind")
        params = feature.get("params", {})
        validate_parameters(kind, params)
        if "inputs" in feature:
            refs = feature["inputs"]
            if (not isinstance(refs, list) or (not refs and kind in ("extrude", "revolve", "sweep", "loft"))
                    or any(not isinstance(ref, str) for ref in refs)):
                raise CadError("feature inputs must be string references", reason="unknown_sketch")
            if kind != "loft" and len(set(refs)) != len(refs):
                raise CadError("duplicate feature input reference", reason="unknown_sketch")
            if any(ref not in sketch_ids | prior for ref in refs):
                raise CadError("missing, self or forward feature reference", reason="unknown_sketch")
            if kind in ("extrude", "revolve") and refs and not set(refs) & sketch_ids:
                raise CadError("feature has no sketch input", reason="unknown_sketch")
            if kind == "sweep" and refs != [params.get("sketch_uuid", feature.get("sketch_uuid"))]:
                raise CadError("sweep inputs disagree with profile parameter", reason="unknown_sketch")
            if kind == "loft" and refs != params.get("profile_uuids"):
                raise CadError("loft inputs disagree with profile parameters", reason="unknown_sketch")
            if (kind in ("fillet", "chamfer", "shell", "pattern", "mirror") and refs
                    and feature.get("suppressed") is not True):
                if any(ref not in live for ref in refs):
                    raise CadError("modifier references an inactive or non-solid input",
                                   reason="missing_base_solid")
        identifier = feature.get("uuid")
        if identifier:
            prior.add(identifier)
            if feature.get("suppressed") is not True:
                live.add(identifier)


def module(name):
    return importlib.import_module("cadcore.kernel.features_" + name)


def compose_adapter(source):
    anchor = "\ndef _build_recipe(recipe):"
    dispatch = '    raise ValueError("unsupported build op: %s" % op)'
    job_anchor = '        elif op == "project2d":'
    if any(source.count(item) != 1 for item in (anchor, dispatch, job_anchor)):
        raise RuntimeError("FreeCAD adapter composition anchors changed")
    snippets = '\nif "_EXTENDED_BUILDERS" not in globals():\n    _EXTENDED_BUILDERS = {}\n' + "\n".join(
        module(name).ADAPTER_SOURCE for name in MODULES)
    source = source.replace(anchor, snippets + anchor)
    source = source.replace(dispatch, (
        "    if op in _EXTENDED_BUILDERS:\n"
        "        return _EXTENDED_BUILDERS[op](step, previous)\n" + dispatch))
    source = source.replace(job_anchor, (
        '        elif op == "clearance":\n'
        '            payload["clearance"] = _measure_clearance(job)\n' + job_anchor))
    compile(source, "<freecad-composed-adapter>", "exec")
    return source


def feature_step(kind, params, previous, document, kernel):
    """Resolve live document references before validating each feature recipe."""
    validate_parameters(kind, params)
    sketches = {sketch.get("uuid"): sketch for sketch in document.get("sketches", [])
                if isinstance(sketch, Mapping)}

    def profile(reference):
        if not isinstance(reference, str) or not reference:
            raise CadError("sketch reference must be a nonempty string", reason="unknown_sketch")
        if reference not in sketches:
            raise CadError("feature refers to missing sketch", reason="unknown_sketch")
        return kernel.sketch_to_wire(sketches[reference]).to_dict()

    if kind == "sweep":
        return module("sweep").make_step(
            profile=profile(params.get("sketch_uuid")),
            path_points_nm=params.get("path_points_nm"))
    if kind == "loft":
        refs = params.get("profile_uuids")
        if not isinstance(refs, list) or len(refs) < 2:
            raise CadError("loft requires at least two profiles", reason="unsupported_solid_feature")
        return module("loft").make_step(
            profiles=[profile(ref) for ref in refs], offsets_nm=params.get("offsets_nm"),
            ruled=params.get("ruled", False))
    if kind == "import_step":
        return module("step").make_step(step_text=params.get("step_text"))
    if previous is None:
        raise CadError("feature requires a prior solid", reason="missing_base_solid")
    if kind in ("fillet", "chamfer"):
        return module("edges").make_step(kind=kind, size_nm=params.get("size_nm"),
                                        edge_indices=params.get("edge_indices"))
    if kind == "shell":
        return module("shell").make_step(thickness_nm=params.get("thickness_nm"),
                                        face_indices=params.get("face_indices"),
                                        inward=params.get("inward", True))
    if kind == "pattern":
        return module("pattern").make_pattern_step(translations_nm=params.get("translations_nm"),
                                                  fuse=params.get("fuse", False))
    if kind == "mirror":
        return module("pattern").make_mirror_step(normal_axis=params.get("normal_axis"),
                                                 offset_nm=params.get("offset_nm", 0),
                                                 keep_original=params.get("keep_original", False))
    raise CadError("unsupported solid feature", reason="unsupported_solid_feature")
