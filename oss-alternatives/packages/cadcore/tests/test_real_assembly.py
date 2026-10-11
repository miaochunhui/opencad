"""Real independent-part FreeCAD assembly acceptance, without mocks or skips.

Direct invocation writes raw kernel jobs, test logs and a source-bound result
under --out (default 3FS). Unittest discovery runs the identical real tests.
"""

from __future__ import annotations

import argparse
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[2]
sys.path.insert(0, str(PACKAGE))

from cadcore.assembly import (
    Assembly, Component, Mate, PartReference, Transform, bom, evaluate_assembly,
    load_assembly, save_assembly, solve_mates,
)
from cadcore.docmodel import new_document
from cadcore.errors import CadError
from cadcore.kernel.subproc import KernelExecutionError, SubprocessKernel

MM = 1_000_000
OUT = None
OBSERVATIONS = []
OWNED = [
    ROOT / "oss-alternatives/packages/cadcore/cadcore/assembly.py",
    ROOT / "oss-alternatives/packages/cadcore/cadcore/kernel/assembly_worker.py",
    Path(__file__).resolve(),
    ROOT / "docs/design/real-assembly.md",
]


def identifier(name):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "assembly-fixture-" + name))


def cylinder_document(*, outer_mm=2, inner_mm=None, depth_mm=10):
    doc = new_document()
    profile = identifier("profile")
    entities = [{"uuid": identifier("edge-outer"), "kind": "circle",
                 "geom": {"center": [0, 0], "radius_nm": int(outer_mm*MM)}}]
    if inner_mm is not None:
        entities.append({"uuid": identifier("edge-inner"), "kind": "circle",
                         "geom": {"center": [0, 0], "radius_nm": int(inner_mm*MM)}})
    doc.sketches.append({"uuid": profile, "plane": "XY", "entities": entities,
                        "dimensions": [], "constraints": []})
    doc.features.append({"uuid": identifier("extrusion"), "kind": "extrude", "inputs": [profile],
                         "sketch_uuid": profile, "suppressed": False,
                         "params": {"distance_nm": int(depth_mm*MM), "op": "new"}})
    return doc


def box_document():
    doc = new_document()
    profile = identifier("profile")
    points = [[0, 0], [4*MM, 0], [4*MM, 2*MM], [0, 2*MM]]
    doc.sketches.append({"uuid": profile, "plane": "XY", "entities": [
        {"uuid": identifier("edge-%d" % i), "kind": "line",
         "geom": {"p1": points[i], "p2": points[(i+1) % 4]}}
        for i in range(4)], "dimensions": [], "constraints": []})
    doc.features.append({"uuid": identifier("extrusion"), "kind": "extrude", "inputs": [profile],
                         "sketch_uuid": profile, "suppressed": False,
                         "params": {"distance_nm": 3*MM, "op": "new"}})
    return doc


def fixture(pin_transform=None):
    ring = PartReference.from_document("ring", cylinder_document(outer_mm=10, inner_mm=3, depth_mm=5))
    pin = PartReference.from_document("pin", cylinder_document())
    return Assembly((ring, pin), (
        Component("ring-1", "ring", fixed=True),
        Component("pin-1", "pin", transform=pin_transform or Transform()),
    ))


def rz(degrees):
    angle = math.radians(degrees)
    return ((math.cos(angle), -math.sin(angle), 0),
            (math.sin(angle), math.cos(angle), 0), (0, 0, 1))


def rx(degrees):
    angle = math.radians(degrees)
    return ((1, 0, 0), (0, math.cos(angle), -math.sin(angle)),
            (0, math.sin(angle), math.cos(angle)))


def ry(degrees):
    angle = math.radians(degrees)
    return ((math.cos(angle), 0, math.sin(angle)), (0, 1, 0),
            (-math.sin(angle), 0, math.cos(angle)))


def scale_fixture():
    """Four immutable BReps, repeated configurations, six levels, one moving pallet."""
    parts = (
        PartReference.from_document("bush", cylinder_document(
            outer_mm=10, inner_mm=3, depth_mm=5)),
        PartReference.from_document("bush", cylinder_document(
            outer_mm=7, inner_mm=3, depth_mm=4), configuration="slim"),
        PartReference.from_document("shaft", cylinder_document()),
        PartReference.from_document("bracket", box_document()),
    )
    pallet = Transform(ry(31), (10*MM, 20*MM, 0))
    ground_translation = tuple(int(round(pallet.translation_nm[i] -
        8*MM*pallet.rotation[i][2])) for i in range(3))
    nodes = [
        Component("world", transform=Transform(rz(37), (120*MM, -40*MM, 25*MM)), fixed=True),
        Component("datum", parent_id="world", transform=Transform(rx(-28)), fixed=True),
        Component("ground", "bush", parent_id="datum",
                  transform=Transform(pallet.rotation, ground_translation), fixed=True),
        Component("carriage", parent_id="datum",
                  transform=Transform(rx(12), (2*MM, -MM, 3*MM))),
        Component("pallet", parent_id="carriage", transform=pallet, fixed=True),
    ]
    for index in range(4):
        station = "station-%d" % index
        nodes.extend((
            Component(station, parent_id="pallet",
                      transform=Transform(translation_nm=(index*25*MM, 0, 0)), fixed=True),
            Component("bush-%d" % index, "bush", parent_id=station,
                      configuration="slim" if index % 2 else "default", fixed=True),
            Component("shaft-%d" % index, "shaft", parent_id=station, fixed=True),
        ))
    nodes.extend(Component("bracket-%d" % index, "bracket", parent_id="pallet",
        transform=Transform(rz(19+index*13), ((-30-index*15)*MM, 0, 0)), fixed=True)
        for index in range(2))
    return Assembly(parts, tuple(nodes))


def scale_mates(measured):
    instances = {item["component_id"]: item for item in measured["instances"]}

    def face(identifier, kind):
        values = [f for f in instances[identifier]["faces"] if f["kind"] == kind]
        return (min(values, key=lambda f: f["point_nm"][2]) if kind == "plane"
                else values[0])["face_index"]

    internal = tuple(Mate("internal-%s-%d" % (kind, index), kind,
        "bush-%d" % index, face("bush-%d" % index, kind),
        "shaft-%d" % index, face("shaft-%d" % index, kind))
        for index in range(4) for kind in ("axis", "plane"))
    external = tuple(Mate("locate-"+kind, kind, "ground", face("ground", kind),
        "shaft-0", face("shaft-0", kind), offset_nm=-8*MM if kind == "plane" else 0)
        for kind in ("axis", "plane"))
    return internal, external


def source_hashes():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in OWNED}


class RealAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global OUT
        if OUT is None:
            OUT = Path(tempfile.mkdtemp(prefix="assembly-discovery-",
                                       dir="/hf3fs-jd/data/tmp"))
        OUT.mkdir(parents=True, exist_ok=True)
        cls.kernel = SubprocessKernel(work_dir=OUT / "kernel", keep_work_dir=True,
                                      op_timeout_s=40,
                                      extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
        report = cls.kernel.probe_report()
        if not report.available:
            raise AssertionError("real FreeCAD required, never skipped: %s" % report.as_dict())
        if Path(sys.modules["cadcore.assembly"].__file__).resolve() != OWNED[0]:
            raise AssertionError("assembly fixture imported a foreign source")
        cls.original_profile = cls.kernel._profile
        OBSERVATIONS.append({"probe": report.as_dict(), "source": str(OWNED[0])})

    @classmethod
    def tearDownClass(cls):
        if cls.kernel._profile != cls.original_profile:
            raise AssertionError("assembly worker changed caller kernel profile")
        cls.kernel.close()

    def record(self, name, value):
        OBSERVATIONS.append({"test": self.id(), "name": name, "value": value})

    def assert_reason(self, reason, action):
        with self.assertRaises(CadError) as caught:
            action()
        self.assertEqual(caught.exception.reason, reason)
        self.record("rejection", {"reason": caught.exception.reason, "detail": str(caught.exception)})

    def references(self, assembly):
        measured = evaluate_assembly(assembly, self.kernel, mesh=False)
        self.record("reference_inventory", measured)
        instances = {c["component_id"]: c for c in measured["instances"]}
        ring = instances["ring-1"]["faces"]
        pin = instances["pin-1"]["faces"]
        axis_a = next(f["face_index"] for f in ring if f["kind"] == "axis")
        axis_b = next(f["face_index"] for f in pin if f["kind"] == "axis")
        plane_a = min((f for f in ring if f["kind"] == "plane"), key=lambda f: f["point_nm"][2])
        plane_b = min((f for f in pin if f["kind"] == "plane"), key=lambda f: f["point_nm"][2])
        return (Mate("axis", "axis", "ring-1", axis_a, "pin-1", axis_b),
                Mate("plane", "plane", "ring-1", plane_a["face_index"],
                     "pin-1", plane_b["face_index"]))

    def test_01_pin_through_real_hole_not_overlapping_bbox_collision(self):
        assembly = fixture()
        before = assembly.to_json()
        result = evaluate_assembly(assembly, self.kernel)
        self.assertEqual(result["backend"], "FreeCAD-BRep")
        self.assertEqual(result["solid_count"], 2)
        pair = result["collisions"][0]
        self.assertEqual(pair["status"], "separated")
        self.assertAlmostEqual(pair["distance_nm"], MM, delta=2)
        self.assertEqual(pair["interference_volume_nm3"], 0)
        for instance in result["instances"]:
            self.assertGreater(len(instance["mesh"]["triangles"]), 0)
        ring, pin = result["instances"]
        self.assertNotEqual(ring["document_sha256"], pin["document_sha256"])
        self.assertAlmostEqual(ring["properties"]["volume_nm3"]/MM**3,
                               math.pi*(100-9)*5, delta=1e-6)
        self.assertAlmostEqual(pin["properties"]["volume_nm3"]/MM**3, math.pi*4*10, delta=1e-6)
        self.assertEqual(assembly.to_json(), before)
        self.record("through_hole", result)

    def test_02_pin_intersects_actual_hole_wall(self):
        assembly = fixture(Transform(translation_nm=(2*MM, 0, 0)))
        result = evaluate_assembly(assembly, self.kernel)
        pair = result["collisions"][0]
        self.assertEqual(pair["status"], "penetrating")
        self.assertEqual(pair["distance_nm"], 0)
        self.assertGreater(pair["interference_volume_nm3"], 0)
        self.assertLess(pair["interference_volume_nm3"], math.pi*4*5*MM**3)
        self.record("hole_wall_collision", result)

    def test_03_two_levels_rotation_composition_measured_brep(self):
        part = PartReference.from_document("block", box_document())
        assembly = Assembly((part,), (
            Component("root", transform=Transform(rz(90), (10*MM, 0, 0)), fixed=True),
            Component("sub", parent_id="root", transform=Transform(rx(90), (0, 5*MM, 0)), fixed=True),
            Component("block-1", "block", parent_id="sub", fixed=True),
        ))
        result = evaluate_assembly(assembly, self.kernel)
        props = result["instances"][0]["properties"]
        self.assertEqual(result["solid_count"], 1)
        self.assertAlmostEqual(props["volume_nm3"]/MM**3, 24, delta=1e-8)
        self.assertEqual(props["bbox_nm"], [5*MM, 0, 0, 8*MM, 4*MM, 2*MM])
        mesh = result["instances"][0]["mesh"]["vertices_mm"]
        for index, limits in enumerate(((5, 8), (0, 4), (0, 2))):
            self.assertAlmostEqual(min(v[index] for v in mesh), limits[0], places=7)
            self.assertAlmostEqual(max(v[index] for v in mesh), limits[1], places=7)
        self.record("two_level_rotation", result)

    def test_04_axis_and_plane_numeric_solve_residual_and_dof(self):
        assembly = fixture(Transform(rx(12), (MM, MM//2, 3*MM)))
        axis, plane = self.references(assembly)
        assembly = replace(assembly, mates=(axis, plane))
        original = assembly.to_json()
        solved, result = solve_mates(assembly, self.kernel)
        self.assertEqual(result["status"], "solved")
        self.assertEqual(result["variables"], 6)
        self.assertEqual(result["jacobian_rank"], 5)
        self.assertEqual(result["dof"], 1)
        self.assertGreater(result["iterations"], 0)
        self.assertLess(max(r["linear_residual_nm"] for r in result["residuals"]), 100)
        self.assertLess(max(r["angular_residual_rad"] for r in result["residuals"]), 1e-7)
        self.assertEqual(assembly.to_json(), original)
        self.assertNotEqual(solved.to_json(), original)
        self.assertEqual(result["evaluation"]["collisions"][0]["status"], "separated")
        self.record("numeric_mates", result)

    def test_05_axis_only_leaves_slide_and_spin_dof(self):
        assembly = fixture(Transform(rx(8), (MM, 0, 0)))
        axis, _ = self.references(assembly)
        solved, report = solve_mates(replace(assembly, mates=(axis,)), self.kernel)
        self.assertEqual(report["dof"], 2)
        self.assertEqual(report["jacobian_rank"], 4)
        self.record("axis_dof", report)

    def test_06_conflicting_planes_fail_with_measured_residual(self):
        assembly = fixture()
        _, plane = self.references(assembly)
        assembly = replace(assembly, mates=(plane, replace(plane, mate_id="conflict", offset_nm=MM)))
        before = assembly.to_json()
        self.assert_reason("mate_solve_failed",
                           lambda: solve_mates(assembly, self.kernel, max_iterations=15))
        self.assertEqual(assembly.to_json(), before)

    def test_07_duplicate_constraint_fails_overconstrained(self):
        assembly = fixture()
        axis, _ = self.references(assembly)
        assembly = replace(assembly, mates=(axis, replace(axis, mate_id="duplicate")))
        self.assert_reason("mate_overconstrained", lambda: solve_mates(assembly, self.kernel))

    def test_08_wrong_real_surface_reference_rejected(self):
        assembly = fixture()
        _, plane = self.references(assembly)
        mate = replace(plane, kind="axis")
        self.assert_reason("bad_mate_reference",
                           lambda: solve_mates(replace(assembly, mates=(mate,)), self.kernel))

    def test_09_bom_counts_parts_configuration_not_subassemblies(self):
        first = PartReference.from_document("pin", cylinder_document(), configuration="short")
        second = PartReference.from_document("pin", cylinder_document(depth_mm=20), configuration="long")
        assembly = Assembly((first, second), (
            Component("group", fixed=True),
            Component("a", "pin", parent_id="group", configuration="short"),
            Component("b", "pin", parent_id="group", configuration="short",
                      transform=Transform(translation_nm=(10*MM, 0, 0))),
            Component("c", "pin", configuration="long", transform=Transform(translation_nm=(20*MM, 0, 0))),
        ))
        rows = {row["configuration"]: row for row in bom(assembly)}
        self.assertEqual(rows["short"]["quantity"], 2)
        self.assertEqual(rows["long"]["quantity"], 1)
        self.assertEqual(rows["short"]["component_ids"], ["a", "b"])
        self.assertNotEqual(rows["short"]["document_sha256"], rows["long"]["document_sha256"])
        result = evaluate_assembly(assembly, self.kernel, mesh=False)
        self.assertEqual(result["solid_count"], 3)
        self.record("bom", bom(assembly))

    def test_10_save_reopen_exact_hierarchy_refs_mates_and_brep(self):
        assembly = fixture()
        axis, plane = self.references(assembly)
        assembly = replace(assembly, mates=(axis, plane))
        path = OUT / "roundtrip-assembly.json"
        save_assembly(assembly, path)
        loaded = load_assembly(path)
        self.assertEqual(loaded, assembly)
        self.assertEqual(path.read_text(), assembly.to_json())
        first = evaluate_assembly(assembly, self.kernel, mesh=False)
        second = evaluate_assembly(loaded, self.kernel, mesh=False)
        self.assertEqual(first, second)
        self.assertEqual(bom(loaded), bom(assembly))
        self.record("save_reopen", {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def test_11_part_refs_immutable_and_document_digest_tamper_rejected(self):
        document = cylinder_document()
        part = PartReference.from_document("pin", document)
        before = part.l1_text
        document.features[0]["params"]["distance_nm"] = 40*MM
        self.assertEqual(part.l1_text, before)
        with self.assertRaises(FrozenInstanceError):
            part.part_id = "other"
        self.assert_reason("part_digest_mismatch",
                           lambda: replace(part, l1_text=part.l1_text + " "))

    def test_12_boolean_lengths_and_nonrigid_matrices_rejected(self):
        for bad in (True, 1.5, "1", 10**13):
            self.assert_reason("bad_assembly_input", lambda: Transform(translation_nm=(bad, 0, 0)))
        for bad in (((2, 0, 0), (0, 1, 0), (0, 0, 1)),
                    ((-1, 0, 0), (0, 1, 0), (0, 0, 1))):
            self.assert_reason("bad_assembly_input", lambda: Transform(rotation=bad))

    def test_13_cycles_and_unknown_parent_rejected(self):
        part = PartReference.from_document("pin", cylinder_document())
        self.assert_reason("assembly_cycle", lambda: Assembly((part,), (
            Component("a", parent_id="b"), Component("b", parent_id="a"),
            Component("pin", "pin", parent_id="a"))))
        self.assert_reason("unknown_parent", lambda: Assembly((part,), (
            Component("pin", "pin", parent_id="missing"),)))

    def test_14_unknown_part_or_configuration_rejected(self):
        part = PartReference.from_document("pin", cylinder_document())
        self.assert_reason("unknown_part", lambda: Assembly((part,), (
            Component("pin", "pin", configuration="absent"),)))

    def test_15_hierarchy_depth_and_instance_limits_rejected(self):
        part = PartReference.from_document("pin", cylinder_document())
        nodes = tuple(Component("group-%d" % i, parent_id="group-%d" % (i-1) if i else None)
                      for i in range(9)) + (Component("pin", "pin", parent_id="group-8"),)
        self.assert_reason("assembly_limit", lambda: Assembly((part,), nodes))
        self.assert_reason("assembly_limit", lambda: Assembly((part,), tuple(
            Component("pin-%d" % i, "pin") for i in range(33))))

    def test_16_duplicate_ids_and_part_parent_rejected(self):
        part = PartReference.from_document("pin", cylinder_document())
        self.assert_reason("bad_assembly_input", lambda: Assembly((part,), (
            Component("pin", "pin"), Component("pin", "pin"))))
        self.assert_reason("bad_assembly_input", lambda: Assembly((part,), (
            Component("a", "pin"), Component("b", "pin", parent_id="a"))))

    def test_17_non_solid_document_fails_real_worker_not_fake_empty(self):
        part = PartReference.from_document("empty", new_document())
        assembly = Assembly((part,), (Component("empty", "empty"),))
        with self.assertRaises(CadError) as caught:
            evaluate_assembly(assembly, self.kernel)
        self.assertIn(caught.exception.reason, ("empty_document", "no_solid_feature"))

    def test_18_json_duplicate_unknown_fields_and_sha_tamper_rejected(self):
        text = fixture().to_json()
        self.assert_reason("bad_assembly_input", lambda: Assembly.from_json(
            text.replace('"schema": 1', '"schema": 1, "schema": 1')))
        data = json.loads(text)
        data["bogus"] = True
        self.assert_reason("bad_assembly_input", lambda: Assembly.from_json(json.dumps(data)))
        data.pop("bogus")
        data["parts"][0]["document_sha256"] = "0"*64
        self.assert_reason("part_digest_mismatch", lambda: Assembly.from_json(json.dumps(data)))

    def test_19_free_body_reports_actual_six_dof(self):
        assembly = fixture()
        solved, report = solve_mates(assembly, self.kernel)
        self.assertEqual(solved, assembly)
        self.assertEqual(report["dof"], 6)
        self.assertEqual(report["jacobian_rank"], 0)
        self.assertEqual(report["residuals"], [])

    def test_20_caller_kernel_profile_unchanged_and_source_binding(self):
        result = evaluate_assembly(fixture(), self.kernel, mesh=False)
        self.assertEqual(self.kernel._profile, self.original_profile)
        self.assertEqual(result["backend"], "FreeCAD-BRep")
        self.assertTrue(all(path.is_file() for path in OWNED))
        self.record("source_sha256", source_hashes())

    def test_21_real_pin_tangent_to_hole_wall_has_contact_not_interference(self):
        assembly = fixture(Transform(translation_nm=(MM, 0, 0)))
        result = evaluate_assembly(assembly, self.kernel, mesh=False)
        pair = result["collisions"][0]
        self.assertEqual(pair["status"], "touching")
        self.assertEqual(pair["distance_nm"], 0)
        self.assertEqual(pair["interference_volume_nm3"], 0)
        self.record("tangent_hole_wall", result)

    def test_22_moving_parent_solves_fixed_child_in_rotated_hierarchy(self):
        assembly = fixture()
        axis, plane = self.references(assembly)
        components = (
            Component("world", transform=Transform(rz(35), (20*MM, 5*MM, 0)), fixed=True),
            Component("ring-1", "ring", parent_id="world", fixed=True),
            Component("sub", parent_id="world", transform=Transform(rx(10), (MM, MM, 2*MM))),
            Component("pin-1", "pin", parent_id="sub", fixed=True),
        )
        assembly = replace(assembly, components=components, mates=(axis, plane))
        solved, report = solve_mates(assembly, self.kernel)
        self.assertEqual(report["dof"], 1)
        self.assertEqual(report["variables"], 6)
        self.assertEqual(report["jacobian_rank"], 5)
        self.assertEqual(solved.components[-1].transform, assembly.components[-1].transform)
        self.assertNotEqual(solved.components[2].transform, assembly.components[2].transform)
        self.assertEqual(report["evaluation"]["collisions"][0]["status"], "separated")
        self.record("parent_mate_solve", report)

    def test_23_fixed_inconsistent_assembly_rejects_without_moving_anything(self):
        assembly = fixture()
        _, plane = self.references(assembly)
        components = tuple(replace(c, fixed=True) for c in assembly.components)
        assembly = replace(assembly, components=components,
                           mates=(replace(plane, offset_nm=MM),))
        before = assembly.to_json()
        self.assert_reason("mate_solve_failed", lambda: solve_mates(assembly, self.kernel))
        self.assertEqual(assembly.to_json(), before)

    def test_24_solver_rejects_excess_moving_nodes_before_geometry(self):
        part = PartReference.from_document("pin", cylinder_document())
        assembly = Assembly((part,), tuple(Component("pin-%d" % i, "pin") for i in range(13)))
        self.assert_reason("assembly_limit", lambda: solve_mates(assembly, self.kernel))

    def test_25_bad_iteration_and_tolerance_inputs_rejected(self):
        assembly = fixture()
        for count in (True, 0, 101, 1.2):
            self.assert_reason("assembly_limit", lambda: solve_mates(
                assembly, self.kernel, max_iterations=count))
        for tolerance in (0, 10001):
            self.assert_reason("bad_mate", lambda: solve_mates(
                assembly, self.kernel, tolerance_nm=tolerance))

    def test_26_deep_or_nonfinite_json_is_bounded_and_rejected(self):
        self.assert_reason("assembly_limit", lambda: Assembly.from_json("["*40+"0"+"]"*40))
        self.assert_reason("bad_assembly_input", lambda: Assembly.from_json('{"x": NaN}'))
        self.assert_reason("bad_assembly_input", lambda: Assembly.from_json("{"))

    def test_27_save_load_symlinks_and_corrupt_bytes_fail_loud(self):
        assembly = fixture()
        actual = OUT / "actual-assembly.json"
        save_assembly(assembly, actual)
        alias = OUT / "alias-assembly.json"
        alias.symlink_to(actual)
        self.assert_reason("bad_assembly_path", lambda: save_assembly(assembly, alias))
        self.assert_reason("bad_assembly_path", lambda: load_assembly(alias))
        damaged = OUT / "damaged-assembly.json"
        damaged.write_text("{broken", encoding="utf-8")
        self.assert_reason("bad_assembly_input", lambda: load_assembly(damaged))

    def test_28_shared_config_refs_are_not_external_mutable_documents(self):
        first = cylinder_document()
        reference = PartReference.from_document("pin", first)
        assembly = Assembly((reference,), (
            Component("pin-a", "pin", fixed=True),
            Component("pin-b", "pin", fixed=True, transform=Transform(translation_nm=(10*MM, 0, 0))),
        ))
        first.features.clear()
        result = evaluate_assembly(assembly, self.kernel, mesh=False)
        self.assertEqual(result["solid_count"], 2)
        self.assertEqual(result["instances"][0]["document_sha256"], reference.document_sha256)
        self.assertEqual(result["instances"][1]["document_sha256"], reference.document_sha256)
        self.assertEqual(bom(assembly)[0]["quantity"], 2)

    def test_29_composed_world_translation_bound_rejected(self):
        part = PartReference.from_document("pin", cylinder_document())
        assembly = Assembly((part,), (
            Component("parent", transform=Transform(translation_nm=(10**12, 0, 0)), fixed=True),
            Component("pin", "pin", parent_id="parent",
                      transform=Transform(translation_nm=(10**12, 0, 0)), fixed=True),
        ))
        self.assert_reason("assembly_limit", lambda: evaluate_assembly(assembly, self.kernel))

    def test_30_discovery_evidence_and_raw_process_are_bound(self):
        self.assertEqual(Path(self.kernel._work_dir).resolve(), (OUT / "kernel").resolve())
        jobs = list((OUT / "kernel").glob("job-*/process.json"))
        self.assertTrue(jobs, "real FreeCAD job process evidence must exist")
        for path in jobs:
            process = json.loads(path.read_text())
            self.assertEqual(process["rc"], 0)
            self.assertTrue(process["rc_measured"])
            self.assertFalse(process["timed_out"])
            self.assertTrue(path.with_name("result.json").is_file())
            self.assertTrue(path.with_name("stdout.log").is_file())
            self.assertTrue(path.with_name("stderr.log").is_file())

    def test_31_rigid_internal_mates_validate_without_constraining_parent(self):
        assembly = scale_fixture()
        measured = evaluate_assembly(assembly, self.kernel, mesh=False)
        internal, _ = scale_mates(measured)
        assembly = replace(assembly, mates=internal)
        self.record("rigid_input", assembly.as_dict())
        solved, report = solve_mates(assembly, self.kernel)
        self.assertEqual(solved, assembly)
        self.assertEqual((report["variables"], report["jacobian_rank"], report["dof"]), (6, 0, 6))
        self.assertEqual(len(report["rigid_mates"]), 8)
        self.assertTrue(all(item["motion_frame_id"] == "carriage" for item in report["rigid_mates"]))
        self.record("rigid_validation", report)

    def scale_system(self):
        assembly = scale_fixture()
        measured = evaluate_assembly(assembly, self.kernel, mesh=False)
        internal, external = scale_mates(measured)
        return replace(assembly, mates=internal+external)

    def test_32_rigid_pallet_external_solve_six_levels_eleven_breps(self):
        assembly = self.scale_system()
        before = assembly.to_json()
        initial = evaluate_assembly(assembly, self.kernel, mesh=False)
        solved, report = solve_mates(assembly, self.kernel)
        self.assertEqual(assembly.to_json(), before)
        self.assertEqual((report["variables"], report["jacobian_rank"], report["dof"]), (6, 5, 1))
        self.assertGreater(report["iterations"], 0)
        self.assertEqual(len(report["rigid_mates"]), 8)
        self.assertEqual(report["active_mate_ids"], ["locate-axis", "locate-plane"])
        for original, actual in zip(assembly.components, solved.components):
            if original.fixed:
                self.assertEqual(actual, original)
            else:
                self.assertNotEqual(actual.transform, original.transform)
        self.assertTrue(all(r["linear_residual_nm"] <= 100 and
                            r["angular_residual_rad"] <= 1e-7 for r in report["residuals"]))
        final = report["evaluation"]
        self.assertEqual(final["solid_count"], 11)
        self.assertEqual(len(final["collisions"]), 55)
        self.assertEqual(len({p.document_sha256 for p in solved.parts}), 4)
        self.assertEqual(sum(row["quantity"] for row in bom(solved)), 11)
        self.assertEqual(sorted(row["quantity"] for row in bom(solved)), [2, 2, 3, 4])
        old_instances = {c["component_id"]: c for c in initial["instances"]}
        for item in final["instances"]:
            reference = next(p for p in solved.parts if (
                p.part_id, p.configuration) == (item["part_id"], item["configuration"]))
            self.assertEqual(item["document_sha256"], reference.document_sha256)
            self.assertEqual(item["faces"], old_instances[item["component_id"]]["faces"])
            chain = []
            node = next(c for c in solved.components if c.component_id == item["component_id"])
            while node is not None:
                chain.append(node)
                node = next((c for c in solved.components if c.component_id == node.parent_id), None)
            rotation = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
            translation = (0, 0, 0)
            for node in reversed(chain):
                translation = tuple(translation[i] + sum(
                    rotation[i][k]*node.transform.translation_nm[k] for k in range(3))
                    for i in range(3))
                rotation = tuple(tuple(sum(rotation[i][k]*node.transform.rotation[k][j]
                    for k in range(3)) for j in range(3)) for i in range(3))
            self.assertEqual(item["world_transform"]["translation_nm"],
                             [int(round(x)) for x in translation])
            self.assertLess(max(abs(item["world_transform"]["rotation"][i][j]-rotation[i][j])
                               for i in range(3) for j in range(3)), 1e-12)
        for index in range(4):
            pair = next(p for p in final["collisions"] if
                        (p["a"], p["b"]) == ("bush-%d" % index, "shaft-%d" % index))
            self.assertEqual(pair["status"], "separated")
            self.assertEqual(pair["interference_volume_nm3"], 0)
            self.assertAlmostEqual(pair["distance_nm"], MM, delta=2)
        # Independently compose the datum and known pallet axis, not solver-private helpers.
        datum = tuple(tuple(sum(rz(37)[i][k]*rx(-28)[k][j] for k in range(3))
                            for j in range(3)) for i in range(3))
        expected_axis = tuple(sum(datum[i][k]*ry(31)[k][2] for k in range(3)) for i in range(3))
        shaft = next(c for c in final["instances"] if c["component_id"] == "shaft-0")
        self.assertLess(max(abs(shaft["world_transform"]["rotation"][i][2]-expected_axis[i])
                            for i in range(3)), 1e-7)
        ground = next(c for c in final["instances"] if c["component_id"] == "ground")
        delta = [shaft["world_transform"]["translation_nm"][i] -
                 ground["world_transform"]["translation_nm"][i] for i in range(3)]
        self.assertAlmostEqual(sum(delta[i]*expected_axis[i] for i in range(3)), 8*MM, delta=100)
        saved = OUT / "rigid-pallet.json"
        save_assembly(solved, saved)
        reopened = load_assembly(saved)
        self.assertEqual(reopened.to_json(), solved.to_json())
        reread = evaluate_assembly(reopened, self.kernel, mesh=False)
        self.assertEqual(reread, final)
        self.record("rigid_pallet_solve", report)
        self.record("rigid_pallet_bom", bom(solved))

    def test_33_rigid_internal_conflict_has_measured_residual_no_candidate_pose(self):
        assembly = self.scale_system()
        bad = replace(assembly.mates[1], offset_nm=MM)
        assembly = replace(assembly, mates=(bad, *assembly.mates[2:]))
        before = assembly.to_json()
        with self.assertRaises(CadError) as caught:
            solve_mates(assembly, self.kernel)
        self.assertEqual(caught.exception.reason, "mate_solve_failed")
        self.assertEqual(caught.exception.details["iterations"], 0)
        residual = caught.exception.details["residuals"][0]
        self.assertAlmostEqual(residual["signed_linear_residual_nm"], -MM, delta=1)
        self.assertEqual(assembly.to_json(), before)
        self.record("rigid_conflict", caught.exception.details)

    def test_34_rigid_duplicate_and_external_redundancy_still_rejected(self):
        assembly = self.scale_system()
        for mate in (assembly.mates[0], assembly.mates[-1]):
            duplicate = replace(assembly, mates=(*assembly.mates, replace(mate, mate_id="duplicate")))
            self.assert_reason("mate_overconstrained", lambda: solve_mates(duplicate, self.kernel))
        reversed_axis = replace(assembly.mates[0], mate_id="reversed",
            a=assembly.mates[0].b, a_face=assembly.mates[0].b_face,
            b=assembly.mates[0].a, b_face=assembly.mates[0].a_face)
        self.assert_reason("mate_overconstrained", lambda: solve_mates(
            replace(assembly, mates=(*assembly.mates, reversed_axis)), self.kernel))

    def test_35_rigid_hole_wall_collision_measured_in_rotated_world(self):
        assembly = scale_fixture()
        nodes = tuple(replace(c, transform=Transform(translation_nm=(2*MM, 0, 0)))
                      if c.component_id == "shaft-2" else c for c in assembly.components)
        result = evaluate_assembly(replace(assembly, components=nodes), self.kernel, mesh=False)
        self.assertEqual(len(result["collisions"]), 55)
        pair = next(p for p in result["collisions"] if (p["a"], p["b"]) == ("bush-2", "shaft-2"))
        self.assertEqual(pair["status"], "penetrating")
        self.assertGreater(pair["interference_volume_nm3"], 0)
        self.assertEqual(pair["distance_nm"], 0)
        self.record("rotated_wall_collision", pair)

    def test_36_wholly_fixed_internal_mates_validate_actual_zero_variables(self):
        assembly = self.scale_system()
        assembly = replace(assembly, components=tuple(
            replace(c, fixed=True) for c in assembly.components), mates=assembly.mates[:8])
        solved, report = solve_mates(assembly, self.kernel)
        self.assertEqual(solved, assembly)
        self.assertEqual((report["variables"], report["jacobian_rank"], report["dof"]), (0, 0, 0))
        self.assertTrue(all(item["motion_frame_id"] is None for item in report["rigid_mates"]))
        self.record("all_fixed_validation", report)

    def test_37_flexible_child_remains_independent_solve_not_rigid_verification(self):
        assembly = self.scale_system()
        assembly = replace(assembly, components=tuple(replace(
            c, fixed=False, transform=Transform(rx(9), (MM, 0, 2*MM)))
            if c.component_id == "shaft-2" else c for c in assembly.components))
        solved, report = solve_mates(assembly, self.kernel)
        self.assertEqual((report["variables"], report["jacobian_rank"], report["dof"]), (12, 10, 2))
        self.assertEqual(len(report["rigid_mates"]), 6)
        self.assertIn("internal-axis-2", report["active_mate_ids"])
        self.assertIn("internal-plane-2", report["active_mate_ids"])
        self.assertNotEqual(next(c for c in solved.components if c.component_id == "shaft-2"),
                            next(c for c in assembly.components if c.component_id == "shaft-2"))
        self.record("flexible_with_rigid_pallet", report)

    def test_38_external_conflict_measured_positive_negative_residuals(self):
        assembly = self.scale_system()
        plane = assembly.mates[-1]
        assembly = replace(assembly, mates=(*assembly.mates,
            replace(plane, mate_id="conflicting-location", offset_nm=plane.offset_nm+MM)))
        before = assembly.to_json()
        with self.assertRaises(CadError) as caught:
            solve_mates(assembly, self.kernel)
        self.assertEqual(caught.exception.reason, "mate_solve_failed")
        residuals = {r["mate_id"]: r for r in caught.exception.details["residuals"]}
        self.assertGreater(residuals["locate-plane"]["signed_linear_residual_nm"], 100)
        self.assertLess(residuals["conflicting-location"]["signed_linear_residual_nm"], -100)
        self.assertEqual(assembly.to_json(), before)
        self.record("external_conflict", caught.exception.as_dict())

    def test_39_swapped_plane_signed_offset_aligned_and_opposed_numeric_brep(self):
        assembly = scale_fixture()
        assembly = replace(assembly, components=tuple(replace(
            c, transform=Transform(translation_nm=(0, 0, 12*MM)))
            if c.component_id == "shaft-0" else c for c in assembly.components))
        measured = evaluate_assembly(assembly, self.kernel, mesh=False)
        instances = {item["component_id"]: item for item in measured["instances"]}
        left, right = instances["bush-0"], instances["shaft-0"]
        a_face = min((f for f in left["faces"] if f["kind"] == "plane"),
                     key=lambda f: f["point_nm"][2])

        def world_point(instance, face):
            pose = instance["world_transform"]
            return [pose["translation_nm"][i] + sum(
                pose["rotation"][i][k]*face["point_nm"][k] for k in range(3))
                for i in range(3)]

        for aligned in (True, False):
            values = [f for f in right["faces"] if f["kind"] == "plane"]
            b_face = (min if aligned else max)(values, key=lambda f: f["point_nm"][2])
            pa, pb = world_point(left, a_face), world_point(right, b_face)
            normal = [sum(left["world_transform"]["rotation"][i][k]*a_face["direction"][k]
                          for k in range(3)) for i in range(3)]
            offset = int(round(sum((pb[i]-pa[i])*normal[i] for i in range(3))))
            self.assertEqual(offset, (-12 if aligned else -22)*MM)
            original = Mate("directed-plane", "plane", "bush-0", a_face["face_index"],
                            "shaft-0", b_face["face_index"], offset_nm=offset, aligned=aligned)
            swapped = replace(original, mate_id="swapped-plane", a=original.b,
                a_face=original.b_face, b=original.a, b_face=original.a_face,
                offset_nm=-offset if aligned else offset)
            reports = []
            for mate in (original, swapped):
                solved, report = solve_mates(replace(assembly, mates=(mate,)), self.kernel)
                self.assertEqual(solved.components, assembly.components)
                self.assertEqual((report["variables"], report["jacobian_rank"], report["dof"]), (6, 0, 6))
                self.assertLess(abs(report["residuals"][0]["signed_linear_residual_nm"]), 1)
                self.assertLess(report["residuals"][0]["angular_residual_rad"], 1e-10)
                reports.append(report)
            self.assertEqual(reports[0]["evaluation"], reports[1]["evaluation"])
            self.assert_reason("mate_overconstrained", lambda: solve_mates(
                replace(assembly, mates=(original, swapped)), self.kernel))
            with self.assertRaises(CadError) as caught:
                solve_mates(replace(assembly, mates=(
                    replace(swapped, offset_nm=-swapped.offset_nm),)), self.kernel)
            self.assertEqual(caught.exception.reason, "mate_solve_failed")
            self.assertGreater(caught.exception.details["residuals"][0]["linear_residual_nm"], MM)
            self.assertEqual(caught.exception.details["jacobian_rank"], 0)
            self.record("swapped_plane_numeric", {"aligned": aligned, "direct_offset_nm": offset,
                "swapped_offset_nm": swapped.offset_nm, "reports": reports,
                "wrong_sign": caught.exception.as_dict()})

    def test_40_empty_mates_fixed_and_moving_nodes_keep_measured_rank(self):
        assembly = scale_fixture()
        for fixed in (False, True):
            value = replace(assembly, components=tuple(
                replace(c, fixed=True) for c in assembly.components)) if fixed else assembly
            solved, report = solve_mates(value, self.kernel)
            self.assertEqual(solved, value)
            self.assertEqual(report["jacobian_rank"], 0)
            self.assertEqual(report["variables"], 0 if fixed else 6)
            self.assertEqual(report["dof"], 0 if fixed else 6)
            self.assertEqual(report["residuals"], [])
            self.assertEqual(report["rigid_mates"], [])
            self.assertEqual(report["active_mate_ids"], [])
            self.record("empty_mates", report)


class _Log:
    def __init__(self, handle):
        self.handle = handle

    def write(self, value):
        self.handle.write(value)
        self.handle.flush()
        sys.stdout.write(value)

    def flush(self):
        self.handle.flush()
        sys.stdout.flush()


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--discovery", action="store_true",
                        help="use unittest discovery on this exact test file")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    OUT = (args.out or Path("/hf3fs-jd/data/tmp/opencad-assembly-20261011") / stamp).resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    started = time.monotonic()
    with (OUT / "tests.log").open("x", encoding="utf-8") as handle:
        if args.discovery:
            suite = unittest.defaultTestLoader.discover(str(PACKAGE / "tests"),
                                                       pattern="test_real_assembly.py",
                                                       top_level_dir=str(PACKAGE))
            discovered = sys.modules["tests.test_real_assembly"]
            discovered.OUT = OUT
            discovered.OBSERVATIONS = OBSERVATIONS
        else:
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(RealAssemblyTests)
        result = unittest.TextTestRunner(stream=_Log(handle), verbosity=2).run(suite)
    artifacts = {}
    for path in sorted(OUT.rglob("*")):
        if path.is_file():
            artifacts[str(path.relative_to(OUT))] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    after = source_hashes()
    complete = (result.wasSuccessful() and result.testsRun > 0 and before == after
                and not result.skipped and bool(OBSERVATIONS) and any(
                    name.endswith("/process.json") for name in artifacts))
    report = {"verdict": "PASS" if complete else "FAIL",
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skips": len(result.skipped), "source_before": before, "source_after": after,
              "source_unchanged": before == after, "observations": OBSERVATIONS,
              "discovery": args.discovery, "artifacts": artifacts,
              "duration_s": time.monotonic()-started, "production_mutated": False,
              "finished_at": datetime.now(timezone.utc).isoformat()}
    (OUT / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print("REAL_ASSEMBLY verdict=%s tests=%d failures=%d errors=%d skips=%d evidence=%s" %
          (report["verdict"], report["tests"], report["failures"], report["errors"], report["skips"], OUT))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
