"""Real assembly transforms, measurement precision and freeze-consumer regressions."""

import copy
import unittest

from cadcore.docmodel import document_from_l1, document_to_l1, new_document
from cadcore.errors import CadError
from cadcore.ops import organization, sw_assembly as asm
from cadcore.ops.features.organization__w10 import layer_visible_entities


class PatternMeasureTests(unittest.TestCase):
    def setUp(self):
        self.doc = new_document()
        self.base = asm.insert_component(
            self.doc, part_key="pin", at_nm=(10, 20, 30), rotation_deg=(0, 0, 90),
            size_nm=(100, 200, 300),
            refs={"tip": {"point_nm": [0, 0, 300], "dir": [0, 0, 1]}})[0]

    def rows(self, key):
        return self.doc.extra["sw_assembly"][key]

    def test_pattern_preserves_geometry_rotation_and_does_not_move_original(self):
        before = copy.deepcopy(self.rows("components")[0])
        created = asm.component_pattern(self.doc, refs=[self.base], count=4,
                                        dx_nm=25_000_000, dy_nm=-50, dz_nm=100)
        self.assertEqual(len(created), 3)
        self.assertEqual(self.rows("components")[0], before)
        for index, row in enumerate(self.rows("components")[1:], 1):
            self.assertEqual(row["at_nm"], [10 + index * 25_000_000,
                                          20 - index * 50, 30 + index * 100])
            self.assertEqual(row["rotation"], before["rotation"])
            self.assertEqual(row["bbox_nm"], before["bbox_nm"])
            self.assertEqual(row["refs"], before["refs"])
        self.rows("components")[1]["refs"]["tip"]["point_nm"][0] = 7
        self.assertEqual(self.rows("components")[0]["refs"], before["refs"])
        roundtrip = document_from_l1(document_to_l1(self.doc))
        self.assertEqual(roundtrip.extra["sw_assembly"], self.doc.extra["sw_assembly"])

    def test_rejected_pattern_is_atomic_and_one_station_is_real_zero(self):
        for kwargs, reason in (
                ({"refs": [self.base, "missing"], "count": 4}, "unknown_component"),
                ({"refs": [self.base, self.base], "count": 4}, "duplicate_component"),
                ({"refs": [self.base], "count": True}, "invalid_argument"),
                ({"refs": [self.base], "count": 0}, "out_of_range"),
                ({"refs": [self.base], "count": 4, "dx_nm": 2 ** 53}, "out_of_range"),
                ({"refs": [self.base], "count": 4, "dx_nm": 0.5}, "invalid_length")):
            before = copy.deepcopy(self.doc.extra)
            with self.assertRaises(CadError) as caught:
                asm.component_pattern(self.doc, **kwargs)
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(self.doc.extra, before)
        self.assertEqual(asm.component_pattern(self.doc, refs=[self.base], count=1), [])

    def test_measurement_uses_current_origins_and_survives_roundtrip(self):
        other = asm.insert_component(self.doc, part_key="bolt", at_nm=(40, 60, 30))[0]
        poses = [row["at_nm"][:] for row in self.rows("components")]
        asm.measure_distance(self.doc, refs=[self.base, other])
        self.assertEqual(self.rows("measurements")[-1]["distance_nm"], 50)
        self.assertEqual([row["at_nm"] for row in self.rows("components")], poses)
        asm.move_component(self.doc, components=[other], delta_nm=(0, 0, 120))
        asm.measure_distance(self.doc, refs=[self.base, other])
        self.assertEqual(self.rows("measurements")[-1]["distance_nm"], 130)
        recovered = document_from_l1(document_to_l1(self.doc))
        self.assertEqual(recovered.extra["sw_assembly"], self.doc.extra["sw_assembly"])

    def test_distance_has_integer_precision_beyond_float_and_rounds_nearest_nm(self):
        first = asm.insert_component(self.doc, part_key="west",
                                     at_nm=(-2 ** 53 + 1, 0, 0))[0]
        second = asm.insert_component(self.doc, part_key="east",
                                      at_nm=(2 ** 53, 0, 0))[0]
        asm.measure_distance(self.doc, refs=[first, second])
        self.assertEqual(self.rows("measurements")[-1]["distance_nm"], 2 ** 54 - 1)
        for delta, expected in (((1, 1, 0), 1), ((1, 1, 1), 2), ((0, 0, 0), 0)):
            other = asm.insert_component(
                self.doc, part_key="remote",
                at_nm=tuple(self.rows("components")[0]["at_nm"][i] + delta[i] for i in range(3)))[0]
            asm.measure_distance(self.doc, refs=[self.base, other])
            self.assertEqual(self.rows("measurements")[-1]["distance_nm"], expected)
        before = copy.deepcopy(self.doc.extra)
        with self.assertRaises(CadError):
            asm.measure_distance(self.doc, refs=[self.base, "missing"])
        self.assertEqual(self.doc.extra, before)


class LayerFreezeTests(unittest.TestCase):
    def test_family_current_layer_cannot_be_frozen_after_switch(self):
        sketch = {"entities": [], "dimensions": []}
        organization.layer_create(sketch, name="WORK")
        organization.current_layer(sketch, layer="WORK")
        before = copy.deepcopy(sketch)
        with self.assertRaises(CadError) as caught:
            organization.layer_set(sketch, layer="WORK", frozen=True)
        self.assertEqual(caught.exception.reason, "current_layer_frozen")
        self.assertEqual(sketch, before)

    def test_family_freeze_changes_display_set_without_changing_entities(self):
        sketch = {"entities": [
            {"uuid": "wall", "kind": "line", "layer": "WALLS"},
            {"uuid": "base", "kind": "line", "layer": "0"}], "dimensions": []}
        organization.layer_create(sketch, name="WALLS")
        original = copy.deepcopy(sketch["entities"])
        organization.layer_set(sketch, layer="WALLS", frozen=True)
        self.assertEqual(layer_visible_entities(sketch), ["base"])
        organization.layer_set(sketch, layer="WALLS", frozen=False, locked=True)
        self.assertEqual(layer_visible_entities(sketch), ["wall", "base"])
        self.assertEqual(layer_visible_entities(sketch, purpose="edit"), ["base"])
        self.assertEqual(sketch["entities"], original)
        before = copy.deepcopy(sketch)
        with self.assertRaises(CadError) as caught:
            organization.layer_set(sketch, layer="0", color=5, frozen=True)
        self.assertEqual(caught.exception.reason, "current_layer_frozen")
        self.assertEqual(sketch, before)
        with self.assertRaises(CadError):
            organization.layer_set(sketch, layer="WALLS", color=4, frozen=1)
        self.assertEqual(sketch, before)


if __name__ == "__main__":
    unittest.main()
