"""Real two-user workspace and threaded/async request-context isolation tests."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[2]
CORE = ROOT / "oss-alternatives/packages/cadcore"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(APP))
from access_control import AuthController, AuthError, Principal
import request_context
import server
from cadcore.i18n import catalog


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="opencad-context-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.auth = AuthController(self.root / "auth", allow_registration=True)
        self.addCleanup(self.auth.close)
        secret = secrets.token_urlsafe(24)
        self.alice = self.auth.register("alice", secret)
        self.bob = self.auth.register("bob", secret)
        self.first = self.auth.login("alice", secret)
        self.second = self.auth.login("bob", secret)
        self.dirs = [str(CORE / "cadcore/data/i18n")]
        self.auth.set_preferences(self.alice["user_id"], locale="zh-CN")
        self.auth.set_preferences(self.bob["user_id"], locale="ja")

    def bind(self, ticket=None, project="alice-project"):
        ticket = ticket or self.first
        owner = self.alice if ticket is self.first else self.bob
        principal = self.auth.authenticate(ticket.token)
        self.auth.authorize(principal, owner_id=owner["user_id"])
        locale = self.auth.get_preferences(principal.user_id)["locale"]
        return request_context.bind(locale, principal=principal, project_id=project, dirs=self.dirs)

    def test_true_source_bindings(self):
        self.assertEqual(Path(request_context.__file__).resolve(), APP / "request_context.py")
        self.assertEqual(Path(server.__file__).resolve(), APP / "server.py")
        self.assertEqual(Path(catalog.__file__).resolve(), CORE / "cadcore/i18n/catalog.py")
        self.assertTrue((CORE / "cadcore/data/i18n/ja.json").is_file())

    def test_unbound_context_and_constructed_identity_fail_closed(self):
        with self.assertRaises(AuthError) as missing:
            request_context.current()
        self.assertEqual(missing.exception.code, "request_context_missing")
        with self.assertRaises(AuthError) as forged:
            with request_context.bind("en", principal=Principal(self.alice["user_id"]),
                                      project_id="alice-project", dirs=self.dirs):
                self.fail("forged principal was bound")
        self.assertEqual(forged.exception.code, "invalid_principal")

    def test_bad_project_cannot_change_current_context(self):
        with self.bind() as active:
            for value in (None, "", "x\n", 123):
                with self.subTest(value=value), self.assertRaises(AuthError):
                    with request_context.bind("ja", principal=self.second.principal,
                                              project_id=value, dirs=self.dirs):
                        self.fail("bad project accepted")
                self.assertIs(request_context.current(), active)
                self.assertEqual(catalog.active_locale(), "zh-CN")

    def test_nested_binding_and_exception_restore_outer_and_legacy(self):
        legacy = catalog.active_locale()
        with self.bind() as outer:
            self.assertIs(request_context.current(), outer)
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                with self.bind(self.second, "bob-project") as inner:
                    self.assertEqual(inner.locale, "ja")
                    self.assertEqual(catalog.active_locale(), "ja")
                    raise RuntimeError("fixture")
            self.assertIs(request_context.current(), outer)
            self.assertEqual(catalog.active_locale(), "zh-CN")
        self.assertEqual(catalog.active_locale(), legacy)
        with self.assertRaises(AuthError):
            request_context.current()

    def test_preference_changes_affect_next_request_not_active_snapshot(self):
        with self.bind() as active:
            old = active.catalog.t("cmd.line")
            self.auth.set_preferences(self.alice["user_id"], locale="en")
            self.assertEqual(catalog.active_locale(), "zh-CN")
            self.assertEqual(catalog.t("cmd.line"), old)
        with self.bind() as next_request:
            self.assertEqual(next_request.locale, "en")
            self.assertEqual(catalog.active_locale(), "en")
        self.assertEqual(self.auth.get_preferences(self.bob["user_id"]), {"locale": "ja"})

    def test_two_real_workspaces_document_history_language_and_cache_are_separate(self):
        core = server._load_cadcore()
        workspaces = []
        for ticket, project, standard, length in (
                (self.first, "alice-project", "gbt", 10_000_000),
                (self.second, "bob-project", "iso", 20_000_000)):
            with self.bind(ticket, project) as ctx:
                local_core = dict(core, i18n_catalog=ctx.catalog)
                workspace = server.Workspace(local_core, locale=ctx.locale, standard=standard,
                                             out_root=str(self.root / project))
                self.assertIs(workspace.catalog, ctx.catalog)
                initial = workspace.l1_text()
                workspace.command("add_line", {"p1": [0, 0], "p2": [length, 0]})
                self.assertNotEqual(workspace.l1_text(), initial)
                self.assertEqual(workspace.entity_count(), 1)
                self.assertEqual(len(workspace.undo_stack), 1)
                self.assertEqual(workspace.state()["locale"], ctx.locale)
                self.assertEqual(workspace.state()["standard"], standard)
                mesh = workspace.mesh()
                self.assertEqual(mesh["document_sha256"], hashlib.sha256(
                    workspace.l1_text().encode()).hexdigest())
                self.assertEqual(mesh["solids"], [])
                self.assertIs(workspace._mesh_cache, mesh)
                workspaces.append(workspace)
        alice, bob = workspaces
        self.assertTrue(alice.doc is not bob.doc)
        self.assertTrue(alice.undo_stack is not bob.undo_stack)
        self.assertTrue(alice._mesh_cache is not bob._mesh_cache)
        self.assertNotEqual(alice.l1_text(), bob.l1_text())
        bob_doc = bob.l1_text()
        alice.command("undo", {})
        self.assertEqual(alice.entity_count(), 0)
        self.assertEqual(bob.entity_count(), 1)
        self.assertEqual(bob.l1_text(), bob_doc)
        self.assertEqual(alice.catalog.active_locale(), "zh-CN")
        self.assertEqual(bob.catalog.active_locale(), "ja")
        self.assertNotEqual(alice.catalog.t("cmd.line"), bob.catalog.t("cmd.line"))

    def test_threads_interleave_fixed_identity_project_and_implicit_translation(self):
        barrier = threading.Barrier(2)

        def worker(ticket, project, expected_locale):
            with self.bind(ticket, project) as ctx:
                expected = ctx.catalog.t("cmd.line")
                for _ in range(12):
                    barrier.wait(timeout=10)
                    self.assertIs(request_context.current(), ctx)
                    self.assertEqual(catalog.active_locale(), expected_locale)
                    self.assertEqual(catalog.t("cmd.line"), expected)
                    self.assertEqual(ctx.project_id, project)
                    barrier.wait(timeout=10)
                return ctx.principal.user_id, expected

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, self.first, "alice-project", "zh-CN")
            second = pool.submit(worker, self.second, "bob-project", "ja")
            a, b = first.result(timeout=30), second.result(timeout=30)
        self.assertEqual(a[0], self.alice["user_id"])
        self.assertEqual(b[0], self.bob["user_id"])
        self.assertNotEqual(a[1], b[1])

    def test_async_tasks_interleave_and_cancel_without_context_leak(self):
        async def worker(ticket, project, expected_locale):
            with self.bind(ticket, project) as ctx:
                expected = catalog.t("cmd.line")
                for _ in range(15):
                    await asyncio.sleep(0)
                    self.assertIs(request_context.current(), ctx)
                    self.assertEqual(catalog.active_locale(), expected_locale)
                    self.assertEqual(catalog.t("cmd.line"), expected)
                return ctx.principal.user_id

        async def run():
            values = await asyncio.gather(
                worker(self.first, "alice-project", "zh-CN"),
                worker(self.second, "bob-project", "ja"))
            self.assertEqual(values, [self.alice["user_id"], self.bob["user_id"]])
            with self.bind() as outer:
                ready = asyncio.Event()

                async def cancellable():
                    with self.bind(self.second, "bob-project"):
                        ready.set()
                        await asyncio.Event().wait()

                task = asyncio.create_task(cancellable())
                await ready.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertIs(request_context.current(), outer)
                self.assertEqual(catalog.active_locale(), "zh-CN")

        asyncio.run(run())
        with self.assertRaises(AuthError):
            request_context.current()

    def test_explicit_worker_capture_preserves_snapshot_without_extending_authorization(self):
        with self.bind() as ctx:
            captured = request_context.capture(lambda: (
                request_context.current(), catalog.active_locale(), catalog.t("cmd.line")))
        self.auth.logout(self.first.token)
        with ThreadPoolExecutor(max_workers=2) as pool:
            value = pool.submit(captured).result(timeout=10)
        self.assertIs(value[0], ctx)
        self.assertEqual(value[1], "zh-CN")
        with self.assertRaises(AuthError) as revoked:
            self.auth.authorize(value[0].principal, owner_id=self.alice["user_id"])
        self.assertEqual(revoked.exception.code, "revoked_token")


if __name__ == "__main__":
    unittest.main(verbosity=2)
