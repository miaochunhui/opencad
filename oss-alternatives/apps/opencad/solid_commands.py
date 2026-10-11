"""Transactional solid commands backed by real FreeCAD reconstruction."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping

from cadcore.errors import CadError, DependencyMissing
from cadcore.kernel.subproc import SubprocessKernel

COMMANDS = {
    "sweep_solid": ("sweep", ("sketch_uuid", "path_points_nm", "op")),
    "loft_solid": ("loft", ("profile_uuids", "offsets_nm", "ruled", "op")),
    "fillet_solid": ("fillet", ("size_nm", "edge_indices")),
    "chamfer_solid": ("chamfer", ("size_nm", "edge_indices")),
    "shell_solid": ("shell", ("thickness_nm", "face_indices", "inward")),
    "pattern_solid": ("pattern", ("translations_nm", "fuse")),
    "mirror_solid": ("mirror", ("normal_axis", "offset_nm", "keep_original")),
    "import_step": ("import_step", ("step_text", "op")),
}
EDIT_COMMANDS = ("suppress_solid", "edit_feature_params", "prepare_release")


def require(condition, message):
    if not condition:
        raise CadError(message, reason="bad_solid_arguments")


def validate(workspace):
    kernel = SubprocessKernel()
    try:
        doc = json.loads(workspace.l1_text())
        if any(feature.get("suppressed") is True for feature in doc["features"]):
            # Stored inactive parameters must still produce valid BRep topology.
            shadow = json.loads(workspace.l1_text())
            for feature in shadow["features"]:
                feature["suppressed"] = False
            kernel.tessellate(kernel.shape_from_document(shadow))
        try:
            shape = kernel.shape_from_document(doc)
        except DependencyMissing as exc:
            if exc.reason != "no_solid_feature" or any(
                    feature.get("suppressed") is not True for feature in doc["features"]):
                raise
        else:
            kernel.tessellate(shape)
    finally:
        kernel.close()


def execute(workspace, command, args):
    if command == "prepare_release":
        from release_state import prepare
        return prepare(workspace, args)

    def action():
        if command in EDIT_COMMANDS:
            feature = next((item for item in workspace.doc.features
                            if item.get("uuid") == args.get("feature_uuid")), None)
            require(feature is not None, "unknown feature UUID")
            if command == "suppress_solid":
                require(set(args) == {"feature_uuid", "suppressed"}, "unexpected suppression arguments")
                require(isinstance(args["suppressed"], bool), "suppressed must be boolean")
                feature["suppressed"] = args["suppressed"]
            else:
                require(set(args) == {"feature_uuid", "params"}, "unexpected feature edit arguments")
                require(isinstance(args["params"], Mapping), "params must be a mapping")
                old = feature.get("params", {})
                from cadcore.kernel.features_registry import PARAMETERS, validate_parameters
                kind = feature["kind"]
                require(not set(args["params"]).difference(PARAMETERS[kind] | set(old)),
                        "unknown feature edit parameter")
                required = {
                    "sweep": {"sketch_uuid", "path_points_nm"},
                    "loft": {"profile_uuids", "offsets_nm"},
                    "fillet": {"size_nm", "edge_indices"}, "chamfer": {"size_nm", "edge_indices"},
                    "shell": {"thickness_nm", "face_indices"}, "pattern": {"translations_nm"},
                    "mirror": {"normal_axis"}, "import_step": {"step_text"},
                    "extrude": {"distance_nm"}, "revolve": {"axis", "angle_deg"},
                }[kind]
                require(required <= set(args["params"]), "missing required feature parameter")
                validate_parameters(kind, args["params"])
                feature["params"] = dict(args["params"])
                if kind == "sweep":
                    reference = feature["params"]["sketch_uuid"]
                    feature["inputs"] = [reference]
                    feature["sketch_uuid"] = reference
                elif kind == "loft":
                    feature["inputs"] = list(feature["params"]["profile_uuids"])
            identifier = feature["uuid"]
        else:
            kind, allowed = COMMANDS[command]
            require(not set(args).difference(allowed), "unknown solid parameters")
            params = dict(args)
            if kind == "sweep":
                params.setdefault("sketch_uuid", workspace.sketch["uuid"])
            identifier = str(uuid.uuid4())
            refs = params.get("profile_uuids", [])
            if kind == "sweep":
                refs = [params["sketch_uuid"]]
            elif kind in ("fillet", "chamfer", "shell", "pattern", "mirror"):
                refs = [item["uuid"] for item in workspace.doc.features
                        if item.get("suppressed") is not True][-1:]
            require(isinstance(refs, list), "profile references must be a list")
            feature = {"uuid": identifier, "kind": kind, "inputs": refs,
                       "params": params, "suppressed": False}
            if kind == "sweep":
                feature["sketch_uuid"] = params["sketch_uuid"]
            workspace.doc.features.append(feature)
        validate(workspace)
        return identifier

    identifier = workspace._mutate(action)
    return workspace._command_result(command, identifier)
