"""Fixed catalog snapshot, implicit API binding and legacy compatibility."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
from cadcore.i18n import catalog


class CatalogContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="catalog-context-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for code, text in (("en", "Line"), ("de", "Linie"), ("ja", "Sen")):
            self.write(code, {"cmd.line": text, "cmd.format": "Hello {name}"})
        self.dirs = [str(self.root)]
        self.previous = catalog.active_locale()

    def write(self, code, strings):
        path = self.root / (code + ".json")
        path.write_text(json.dumps({"_meta": {"code": code}, "strings": strings}))
        self.assertTrue(path.is_file())
        return path

    def test_context_is_real_source_and_disk_catalog(self):
        ctx = catalog.CatalogContext("de", dirs=self.dirs)
        self.assertEqual(Path(catalog.__file__).resolve(), CORE / "cadcore/i18n/catalog.py")
        active = ctx.active_catalog()
        self.assertEqual(Path(active.path), self.root / "de.json")
        self.assertEqual(dict(active.strings), json.loads(Path(active.path).read_text())["strings"])

    def test_bound_module_apis_use_context_without_global_switch_or_listener(self):
        before = catalog.stats()
        events = []
        unsubscribe = catalog.on_language_changed(events.append)
        self.addCleanup(unsubscribe)
        ctx = catalog.CatalogContext("de", dirs=self.dirs)
        with ctx.bind() as same:
            self.assertIs(same, ctx)
            self.assertEqual(catalog.active_locale(), "de")
            self.assertEqual(catalog.active_catalog(), ctx.active_catalog())
            self.assertEqual(catalog.active_chain(), ctx.active_chain())
            self.assertEqual(catalog.t("cmd.line"), "Linie")
            self.assertEqual(catalog.stats()["lookups"], 1)
        self.assertEqual(catalog.active_locale(), self.previous)
        self.assertEqual(catalog.stats()["lookups"], before["lookups"])
        self.assertEqual(events, [])

    def test_fixed_context_only_allows_same_locale_and_directories(self):
        ctx = catalog.CatalogContext("de", dirs=self.dirs)
        with ctx.bind():
            change = catalog.set_locale("de", dirs=self.dirs)
            self.assertFalse(change.changed)
            self.assertFalse(change.degraded)
            self.assertEqual(change.keys, 2)
            with self.assertRaises(catalog.CatalogError):
                catalog.set_locale("ja", dirs=self.dirs)
            with self.assertRaises(catalog.CatalogError):
                ctx.set_locale("de", dirs=[])
            self.assertEqual(catalog.active_locale(), "de")
        with self.assertRaises(AttributeError):
            ctx.locale = "ja"

    def test_snapshot_strings_and_nested_metadata_are_readonly(self):
        self.write("de", {"cmd.line": "Linie"})
        path = self.root / "de.json"
        data = json.loads(path.read_text())
        data["_meta"]["font_fallback"] = ["Fixture Font"]
        path.write_text(json.dumps(data))
        ctx = catalog.CatalogContext("de", dirs=self.dirs)
        with self.assertRaises(TypeError):
            ctx.active_catalog().strings["cmd.line"] = "Mutation"
        with self.assertRaises(TypeError):
            ctx.active_catalog().meta["font_fallback"][0] = "Mutation"
        self.assertEqual(ctx.t("cmd.line"), "Linie")

    def test_disk_updates_only_affect_new_snapshots(self):
        ctx = catalog.CatalogContext("de", dirs=self.dirs)
        self.write("de", {"cmd.line": "Neu"})
        fresh = catalog.CatalogContext("de", dirs=self.dirs)
        self.assertEqual(ctx.t("cmd.line"), "Linie")
        self.assertEqual(fresh.t("cmd.line"), "Neu")

    def test_missing_empty_or_malformed_catalog_fails_before_binding(self):
        for value in ("", None, 123, "de\n"):
            with self.subTest(value=value), self.assertRaises(catalog.CatalogError):
                catalog.CatalogContext(value, dirs=self.dirs)
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(catalog.LocaleUnavailable):
            catalog.CatalogContext("en", dirs=[str(empty)])
        (self.root / "de.json").write_text("{")
        with self.assertRaises(catalog.CatalogError):
            catalog.CatalogContext("de", dirs=self.dirs)
        self.assertEqual(catalog.active_locale(), self.previous)

    def test_nested_context_exception_restores_bindings(self):
        first = catalog.CatalogContext("de", dirs=self.dirs)
        second = catalog.CatalogContext("ja", dirs=self.dirs)
        with first.bind():
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                with second.bind():
                    self.assertEqual(catalog.t("cmd.line"), "Sen")
                    raise RuntimeError("fixture")
            self.assertEqual(catalog.t("cmd.line"), "Linie")
        self.assertEqual(catalog.active_locale(), self.previous)

    def test_binding_convenience_and_adapter_utility_contract(self):
        with catalog.bind("de", dirs=self.dirs) as ctx:
            self.assertEqual(ctx.default_dirs(), self.dirs)
            self.assertEqual(ctx.available_locales(), ["de", "en", "ja"])
            self.assertEqual(ctx.load_catalog("ja").strings["cmd.line"], "Sen")
            self.assertEqual(len(ctx.scan_catalogs(ctx.default_dirs())), 3)
            self.assertEqual(ctx.resolve_chain("de-AT"), ["de-AT", "de", "en"])
            self.assertEqual(ctx.t("cmd.format", name="Ada"), "Hello Ada")

    def test_observability_and_missing_keys_are_context_local(self):
        first = catalog.CatalogContext("de", dirs=self.dirs)
        second = catalog.CatalogContext("ja", dirs=self.dirs)
        before = catalog.untranslated_keys()
        self.assertEqual(first.t("only.missing"), "Missing")
        self.assertEqual(first.untranslated_keys(), [("only.missing", 1)])
        self.assertEqual(first.stats()["misses"], 1)
        self.assertEqual(second.stats()["misses"], 0)
        self.assertEqual(second.untranslated_keys(), [])
        self.assertEqual(catalog.untranslated_keys(), before)

    def test_fixed_context_cannot_subscribe_to_global_changes(self):
        with catalog.bind("de", dirs=self.dirs):
            with self.assertRaises(catalog.CatalogError):
                catalog.on_language_changed(lambda _: None)

    def test_legacy_api_still_switches_outside_binding(self):
        original_dirs = list(catalog._state["dirs"])
        self.addCleanup(lambda: catalog.set_locale(self.previous, dirs=original_dirs))
        first = catalog.set_locale("de", dirs=self.dirs)
        self.assertEqual(first.current, "de")
        self.assertEqual(catalog.t("cmd.line"), "Linie")
        with catalog.bind("ja", dirs=self.dirs):
            self.assertEqual(catalog.t("cmd.line"), "Sen")
        self.assertEqual(catalog.t("cmd.line"), "Linie")

    def test_thread_barriers_do_not_mix_locale_or_counters(self):
        contexts = [catalog.CatalogContext(code, dirs=self.dirs) for code in ("de", "ja")]
        barrier = threading.Barrier(2)

        def worker(ctx, expected):
            with ctx.bind():
                for _ in range(20):
                    barrier.wait(timeout=10)
                    self.assertEqual(catalog.t("cmd.line"), expected)
                    self.assertEqual(catalog.active_locale(), ctx.locale)
                    barrier.wait(timeout=10)
            return ctx.stats()["lookups"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, contexts[0], "Linie")
            second = pool.submit(worker, contexts[1], "Sen")
            self.assertEqual((first.result(timeout=30), second.result(timeout=30)), (20, 20))

    def test_async_interleaving_and_inherited_task_snapshot(self):
        first = catalog.CatalogContext("de", dirs=self.dirs)
        second = catalog.CatalogContext("ja", dirs=self.dirs)

        async def worker(ctx, expected):
            with ctx.bind():
                for _ in range(20):
                    await asyncio.sleep(0)
                    self.assertEqual(catalog.t("cmd.line"), expected)

        async def run():
            await asyncio.gather(worker(first, "Linie"), worker(second, "Sen"))
            with first.bind():
                inherited = asyncio.create_task(asyncio.sleep(0, result=catalog.active_locale()))
                with second.bind():
                    self.assertEqual(await inherited, "de")
                    self.assertEqual(catalog.active_locale(), "ja")
                self.assertEqual(catalog.active_locale(), "de")

        asyncio.run(run())
        self.assertEqual(catalog.active_locale(), self.previous)


if __name__ == "__main__":
    unittest.main(verbosity=2)
