"""Independent FreeCAD assembly job adapter; shared subproc source is untouched."""

from __future__ import annotations

from dataclasses import replace
import json
import threading

from .subproc import ADAPTER_MARKER, KernelExecutionError, SubprocessKernel

__all__ = ["run_assembly_job", "ADAPTER_SOURCE"]

ADAPTER_SOURCE = r'''
def _assembly_matrix(rotation, translation):
    matrix = App.Matrix()
    for i in range(3):
        for j in range(3):
            setattr(matrix, "A%d%d" % (i+1, j+1), rotation[i][j])
        setattr(matrix, "A%d4" % (i+1), translation[i])
    return matrix


def _assembly_transform(shape, rotation, translation):
    transformed = shape.copy()
    transformed.transformShape(_assembly_matrix(rotation, translation), True)
    if transformed.isNull() or not transformed.isValid() or not transformed.Solids:
        raise ValueError("assembly transform did not preserve valid BRep solids")
    return transformed


def _assembly_pose(nodes, identifier, cache):
    if identifier in cache:
        return cache[identifier]
    node = nodes[identifier]
    rotation = node["transform"]["rotation"]
    translation = [_mm(x) for x in node["transform"]["translation_nm"]]
    if node["parent_id"] is not None:
        pr, pt = _assembly_pose(nodes, node["parent_id"], cache)
        translation = [sum(pr[i][k]*translation[k] for k in range(3))+pt[i] for i in range(3)]
        rotation = [[sum(pr[i][k]*rotation[k][j] for k in range(3))
                     for j in range(3)] for i in range(3)]
    cache[identifier] = rotation, translation
    return cache[identifier]


def _assembly_faces(shape):
    result = []
    for index, face in enumerate(shape.Faces):
        surface = face.Surface
        if isinstance(surface, Part.Plane):
            point = face.CenterOfMass
            direction = face.normalAt(*face.Surface.parameter(point))
            kind = "plane"
        elif isinstance(surface, Part.Cylinder):
            point, direction, kind = surface.Center, surface.Axis, "axis"
        else:
            continue
        result.append({"face_index": index, "kind": kind,
                       "point_nm": [int(round(x*1000000)) for x in (point.x, point.y, point.z)],
                       "direction": [direction.x, direction.y, direction.z]})
    return result


def _assembly_measure(left, right):
    common = left.common(right)
    if not common.isNull() and not common.isValid():
        raise ValueError("assembly common produced invalid BRep")
    volumes = [solid.Volume for solid in common.Solids]
    if any(not math.isfinite(value) or value <= 0 for value in volumes):
        raise ValueError("assembly interference volume is invalid")
    volume = int(round(sum(volumes)*1000000000000000000))
    if sum(volumes) > 0 and volume == 0:
        raise ValueError("assembly interference is below integer-nm3 resolution")
    if volume > 0:
        point = common.Vertexes[0].Point
        pair = [[int(round(x*1000000)) for x in (point.x, point.y, point.z)]]*2
        return {"status": "penetrating", "distance_nm": 0,
                "interference_volume_nm3": volume, "nearest_points_nm": [pair]}
    contact = common
    if not (contact.Faces or contact.Edges or contact.Vertexes):
        contact = left.section(right)
    if contact.Faces or contact.Edges or contact.Vertexes:
        if not contact.Vertexes:
            raise ValueError("assembly contact has no BRep witness")
        point = contact.Vertexes[0].Point
        pair = [[int(round(x*1000000)) for x in (point.x, point.y, point.z)]]*2
        return {"status": "touching", "distance_nm": 0,
                "interference_volume_nm3": 0, "nearest_points_nm": [pair]}
    distance, pairs, support = left.distToShape(right)
    if not math.isfinite(distance) or distance <= 0 or not pairs:
        raise ValueError("assembly distance has no positive reading or BRep contact witness")
    distance_nm = int(round(distance*1000000))
    if distance_nm <= 0:
        raise ValueError("assembly positive separation is below integer-nm resolution")
    return {"status": "separated", "distance_nm": distance_nm, "interference_volume_nm3": 0,
            "nearest_points_nm": [[[int(round(x*1000000)) for x in (p.x, p.y, p.z)]
                                  for p in pair] for pair in pairs]}


def _evaluate_assembly(job):
    parts = {}
    for part in job["parts"]:
        shape = _build_recipe(part["recipe"])
        if shape.isNull() or not shape.isValid() or not shape.Solids:
            raise ValueError("assembly part did not build real valid BRep solids")
        if any(not solid.isClosed() or not math.isfinite(solid.Volume) or solid.Volume <= 0
               for solid in shape.Solids):
            raise ValueError("assembly part contains nonpositive or open solid")
        if len(shape.Faces) > 4096:
            raise ValueError("assembly part exceeds face bound")
        parts[(part["part_id"], part["configuration"])] = (part, shape)
    nodes = {node["component_id"]: node for node in job["assembly"]["components"]}
    poses, instances, placed = {}, [], []
    total_triangles = 0
    for node in job["assembly"]["components"]:
        if node["part_id"] is None:
            continue
        part, shape = parts[(node["part_id"], node["configuration"])]
        rotation, translation = _assembly_pose(nodes, node["component_id"], poses)
        actual = _assembly_transform(shape, rotation, translation)
        instance = {"component_id": node["component_id"], "part_id": part["part_id"],
                    "configuration": part["configuration"], "document_sha256": part["document_sha256"],
                    "world_transform": {"rotation": rotation,
                                        "translation_nm": [int(round(x*1000000)) for x in translation]},
                    "properties": _mass_properties(actual), "faces": _assembly_faces(shape)}
        if job["mesh"]:
            vertices, triangles = actual.tessellate(0.05)
            if not vertices or not triangles or len(triangles) > 250000:
                raise ValueError("assembly instance mesh is empty or exceeds triangle bound")
            total_triangles += len(triangles)
            if total_triangles > 500000:
                raise ValueError("assembly total mesh exceeds triangle bound")
            instance["mesh"] = {"vertices_mm": [[p.x, p.y, p.z] for p in vertices],
                                "triangles": [list(t) for t in triangles]}
        instances.append(instance)
        placed.append(actual)
    collisions = []
    for i in range(len(placed)):
        for j in range(i+1, len(placed)):
            collisions.append({"a": instances[i]["component_id"], "b": instances[j]["component_id"],
                               **_assembly_measure(placed[i], placed[j])})
    compound = Part.makeCompound(placed)
    if not compound.isValid():
        raise ValueError("assembly compound is invalid")
    return {"instances": instances, "collisions": collisions,
            "properties": _mass_properties(compound), "solid_count": len(compound.Solids),
            "backend": "FreeCAD-BRep", "freecad_version": ".".join(App.Version()[:3])}
'''


class _AssemblyKernel(SubprocessKernel):
    def _collect_job_result(self, job, outcome, result_path):
        evidence = result_path.parent
        (evidence / "stdout.log").write_text(outcome.stdout, encoding="utf-8")
        (evidence / "stderr.log").write_text(outcome.stderr, encoding="utf-8")
        (evidence / "process.json").write_text(json.dumps({
            "rc": outcome.rc, "rc_measured": outcome.rc_measured,
            "elapsed_ms": outcome.elapsed_ms, "timed_out": outcome.timed_out,
            "launch_error": outcome.launch_error,
        }, sort_keys=True), encoding="utf-8")
        payload = super()._collect_job_result(job, outcome, result_path)
        if not outcome.rc_measured or outcome.rc != 0:
            raise KernelExecutionError("assembly process did not exit successfully",
                                       reason="assembly_process_failed", rc=outcome.rc)
        if payload.get("schema") != 1 or payload.get("marker") != ADAPTER_MARKER:
            raise KernelExecutionError("assembly protocol identity mismatch",
                                       reason="invalid_assembly_response")
        return payload


def run_assembly_job(kernel, job):
    """Clone kernel configuration, retaining its bounded real-process execution."""
    if not isinstance(job, dict) or job.get("op") != "assembly":
        raise ValueError("expected assembly job")
    source = kernel._profile.adapter_script
    anchor = "\ndef _build_recipe(recipe):"
    branch = '        elif op == "mass":'
    if source.count(anchor) != 1 or source.count(branch) != 1:
        raise KernelExecutionError("assembly adapter anchors changed", reason="assembly_adapter_mismatch")
    source = source.replace(anchor, "\n" + ADAPTER_SOURCE + anchor, 1)
    source = source.replace(branch, '        elif op == "assembly":\n'
                            '            payload["assembly"] = _evaluate_assembly(job)\n' + branch, 1)
    compile(source, "<assembly-adapter>", "exec")
    worker = object.__new__(_AssemblyKernel)
    worker.__dict__ = dict(kernel.__dict__)
    worker._profile = replace(kernel._profile, adapter_script=source)
    worker._stats = dict(kernel._stats)
    worker._lock = threading.Lock()
    payload = worker._run_job(job)
    result = payload.get("assembly")
    if payload.get("op") != "assembly" or not isinstance(result, dict):
        raise KernelExecutionError("assembly worker response absent", reason="invalid_assembly_response")
    expected = [c for c in job["assembly"]["components"] if c["part_id"] is not None]
    instances = result.get("instances")
    if not isinstance(instances, list) or len(instances) != len(expected):
        raise KernelExecutionError("assembly instance count mismatch", reason="invalid_assembly_response")
    for source_node, instance in zip(expected, instances):
        part = next(p for p in job["parts"] if (
            p["part_id"], p["configuration"]) == (
                source_node["part_id"], source_node["configuration"]))
        if any(instance.get(key) != source_node[key] for key in (
                "component_id", "part_id", "configuration")) or (
                instance.get("document_sha256") != part["document_sha256"]):
            raise KernelExecutionError("assembly instance identity mismatch", reason="invalid_assembly_response")
        volume = instance.get("properties", {}).get("volume_nm3")
        if type(volume) is not int or volume <= 0:
            raise KernelExecutionError("assembly instance volume missing", reason="invalid_assembly_response")
    from .features_clearance import _validate_clearance_response
    try:
        pairs = result.get("collisions")
        if not isinstance(pairs, list) or len(pairs) != len(expected)*(len(expected)-1)//2:
            raise ValueError("assembly pair count mismatch")
        expected_pairs = [(expected[i]["component_id"], expected[j]["component_id"])
                          for i in range(len(expected)) for j in range(i+1, len(expected))]
        for pair, identifiers in zip(pairs, expected_pairs):
            if (pair.get("a"), pair.get("b")) != identifiers:
                raise ValueError("assembly collision pair identity mismatch")
            _validate_clearance_response({"ok": True, "op": "clearance", "clearance": pair})
        if result.get("backend") != "FreeCAD-BRep":
            raise ValueError("assembly backend marker mismatch")
    except (ValueError, TypeError, OverflowError) as exc:
        raise KernelExecutionError(str(exc), reason="invalid_assembly_response") from exc
    return result
