"""tests.test_store —— A15「CAD 文档 ↔ vault 底座桥」单测（29 例，纯标准库 unittest）。

跑法（契约 D 段 G6 的命令；本仓 `tests/` 暂无 `__init__.py`，故补一条等价可跑法）::

    cd oss-alternatives/packages/cadcore
    python3 -m unittest discover -s tests -t .        # 契约原命令（见下方"已知阻塞"）
    python3 -m unittest discover -s tests -t tests    # 等价：把 tests 当顶层目录
    python3 tests/test_store.py -v                    # 直接跑本文件

**已知阻塞（不是本席文件造成）**：Python 3.10 的 `unittest discover` 要求起始目录可导入
（`tests/__init__.py` 在场），而该文件属 A20 名单、本席不得代写 ⇒ 契约原命令当前报
`ImportError: Start directory is not importable`。本文件因此自带 `sys.path` 兜底，
`-t tests` 与直跑两条路都能真跑出判定行。

设计一句话
----------
本桥的失效方式有两种，都必须被尺子看见：**"假成功"**（导出读不回来 / 索引指向的 blob 不在盘上
却照常给值）与**"假零"**（索引表不在却报 0 份文档）。所以每个"该绿"断言旁边都配一条"该红"
（篡改 blob、删 blob、缺 uuid、索引缺席），且**底座 `packages/vault/` 的逐文件 sha256 在整套
工作流前后必须一个 bit 都不变** —— 这是 A15 的承重约束（零改动复用），由本文件真算 sha256 钉住。

夹具自证
--------
1. `_codec()` 的替身**只在 A5 `cadcore/docmodel.py` 确实不在盘上时**注入（按文件是否存在判定，
   不按 import 成功与否），并断言替身在场与磁盘事实一致 ⇒ 真件落盘那天替身自动让位。
2. `_vault_tree_digest()` 先断言它**看得见文件**（`n > 0`）——看不见文件的尺子会把"零改动"假绿。
3. 所有落盘都进 `tempfile.TemporaryDirectory`，不碰仓库内任何别人的文件。
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import importlib.util
import inspect
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_PKG_ROOT = Path(__file__).resolve().parents[1]          # .../packages/cadcore
_PACKAGES_DIR = _PKG_ROOT.parent                         # .../packages
_STORE_FILE = _PKG_ROOT / "cadcore" / "store.py"
_REAL_DOCMODEL_FILE = _PKG_ROOT / "cadcore" / "docmodel.py"
_VAULT_PKG = _PACKAGES_DIR / "vault"

for _path in (str(_PKG_ROOT), str(_VAULT_PKG)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


# ── 装载被测件：优先按包导入，兄弟席位的 `__init__.py` 未落盘时按文件路径装载 ──────────────


def _load_store_module():
    try:
        from cadcore import store  # type: ignore[import-not-found]

        return store
    except Exception:
        spec = importlib.util.spec_from_file_location("cadcore_store_standalone", _STORE_FILE)
        if spec is None or spec.loader is None:  # pragma: no cover - 只有文件被删才会走到
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


store = _load_store_module()

from freechat_vault import fcx as vault_fcx  # noqa: E402  (底座：只读复用，测试拿它当参照实现)
from freechat_vault import store as vault_store  # noqa: E402


# ── L1 编解码夹具（A5 在场用真件；缺席才注入同签名替身）──────────────────────────────────


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _as_plain(value):
    if isinstance(value, Mapping):
        return dict(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return value.as_dict()


def _build_fixture_codec() -> types.ModuleType:
    """A5 `docmodel` 的最小同签名替身（**只在真件缺席时**注入，见模块 docstring 夹具自证 1）。"""

    @dataclasses.dataclass
    class FixtureDocument:
        schema_version: int
        meta: dict
        unit: str
        standard_profile: str
        projection_angle: object
        sketches: list
        features: list
        annotations: list
        sheets: list

    def new_document(*, unit="mm", standard_profile="iso", projection_angle=None,
                     title_key="doc.untitled"):
        return FixtureDocument(
            schema_version=1,
            meta={
                "doc_uuid": str(uuid.uuid4()),
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "app_version": "fixture-codec",
                "title_key": title_key,
            },
            unit=unit,
            standard_profile=standard_profile,
            projection_angle=projection_angle,
            sketches=[],
            features=[],
            annotations=[],
            sheets=[],
        )

    def document_to_l1(doc) -> str:
        return _canonical(_as_plain(doc))

    def document_from_l1(text):
        return json.loads(text)

    module = types.ModuleType("cadcore.docmodel")
    module.__doc__ = "test fixture standing in for the not-yet-landed A5 cadcore.docmodel"
    module.FixtureDocument = FixtureDocument
    module.new_document = new_document
    module.document_to_l1 = document_to_l1
    module.document_from_l1 = document_from_l1
    return module


def _codec():
    """返回 `(module, source)`；`source` ∈ {"real", "fixture"}，由**磁盘事实**决定。"""
    if _REAL_DOCMODEL_FILE.is_file():
        import cadcore.docmodel as real  # type: ignore[import-not-found]

        return real, "real"
    stub = sys.modules.get("cadcore.docmodel")
    if stub is None or not getattr(stub, "FixtureDocument", None):
        stub = _build_fixture_codec()
        sys.modules["cadcore.docmodel"] = stub
    return stub, "fixture"


CODEC, CODEC_SOURCE = _codec()


def _uuid_of(doc) -> str:
    meta = doc["meta"] if isinstance(doc, Mapping) else getattr(doc, "meta")
    return meta["doc_uuid"] if isinstance(meta, Mapping) else meta.doc_uuid


def _make_doc(*, unit="mm", standard_profile="iso", projection_angle=None, title_key="doc.untitled"):
    return CODEC.new_document(unit=unit, standard_profile=standard_profile,
                              projection_angle=projection_angle, title_key=title_key)


def _replace(doc, **changes):
    """改顶层字段：`dataclasses.replace` → Mapping 复制 → 深拷贝后就地赋值。"""
    if dataclasses.is_dataclass(doc) and not isinstance(doc, type):
        try:
            return dataclasses.replace(doc, **changes)
        except TypeError:
            pass
    if isinstance(doc, Mapping):
        return dict(doc, **changes)
    clone = copy.deepcopy(doc)
    for key, value in changes.items():
        setattr(clone, key, value)
    return clone


def _edit_meta(doc, **changes):
    """改 `meta` 子字段（保持 `doc_uuid` 不变）——用于"同 uuid、新内容"用例。"""
    meta = doc["meta"] if isinstance(doc, Mapping) else getattr(doc, "meta")
    if isinstance(meta, Mapping):
        new_meta = dict(meta, **changes)
    elif dataclasses.is_dataclass(meta) and not isinstance(meta, type):
        new_meta = dataclasses.replace(meta, **changes)
    else:
        new_meta = copy.deepcopy(meta)
        for key, value in changes.items():
            setattr(new_meta, key, value)
    return _replace(doc, meta=new_meta)


def _l1_text(doc) -> str:
    return CODEC.document_to_l1(doc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── 底座零改动尺子：`packages/vault/` 逐文件 sha256 ────────────────────────────────────


def _tree_digest(root: Path) -> tuple[int, str]:
    """(文件数, 汇总 sha256)。排除 `__pycache__`（解释器自己写的字节码，不是源码）。"""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        count += 1
    return count, digest.hexdigest()


def _vault_tree_digest() -> tuple[int, str]:
    return _tree_digest(_VAULT_PKG)


def _non_ascii_string_literals(path: Path) -> list:
    """AST 扫出**非 docstring**的含非 ASCII 字符的字符串字面量（i18n G1 门 `hardcoded_literals` 的口径）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstring_nodes.add(id(body[0].value))
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstring_nodes
                and any(ord(ch) > 127 for ch in node.value)):
            hits.append((node.lineno, node.value[:60]))
    return hits


_VAULT_SNAPSHOT = _vault_tree_digest()
_CAD_INDEX_NAME = "idx_cad_document_blobs_sha"   # 本桥新增的唯一索引对象（差分尺子的白名单）
DXF_BYTES = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"
STEP_BYTES = b"ISO-10303-21;\nHEADER;\nENDSEC;\n"


def _blob_files(root: Path) -> list[str]:
    blobs = Path(root) / "blobs"
    if not blobs.is_dir():
        return []
    return sorted(str(p.relative_to(blobs)) for p in blobs.rglob("*") if p.is_file())


def _attachments(vault) -> dict:
    return {rec["sha256"]: rec for rec in vault.iter_attachments()}


def _schema_rows(db_path) -> list:
    """`sqlite_master` 里所有非 `sqlite_*` 对象（type, name, DDL）—— 底座 schema 的差分尺子。"""
    conn = sqlite3.connect(str(db_path))
    try:
        return sorted(tuple(row) for row in conn.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master WHERE name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
        ).fetchall())
    finally:
        conn.close()


# ── 用例 ────────────────────────────────────────────────────────────────────────────


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cadcore-store-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.opened = []

    def open_store(self, name="A", **kwargs):
        vault = store.open_store(str(self.tmp / name), **kwargs)
        self.opened.append(vault)
        self.addCleanup(vault.close)
        return vault

    # —— 夹具自证 ────────────────────────────────────────────────────────────────

    def test_01_fixture_selfcheck_store_module_is_the_real_file(self):
        self.assertEqual(Path(store.__file__).resolve(), _STORE_FILE.resolve())
        self.assertTrue(callable(store.put_document))
        # 冻结签名面（MVP-INTERFACES §B A15）逐字核：形参名/缺省值/导出名被改就会被这条钉住
        expected = {
            "open_store": ["root", "create"],
            "put_document": ["vault", "doc", "blobs"],
            "get_document": ["vault", "doc_uuid"],
            "export_package": ["vault", "out_path"],
        }
        for name, params in expected.items():
            signature = inspect.signature(getattr(store, name))
            self.assertEqual(list(signature.parameters), params, "%s parameter names changed" % name)
            for param in signature.parameters.values():
                if param.name in ("create", "blobs"):
                    self.assertEqual(param.kind, inspect.Parameter.KEYWORD_ONLY,
                                     "%s must stay keyword-only" % param.name)
        self.assertIs(inspect.signature(store.open_store).parameters["create"].default, True)
        self.assertIsNone(inspect.signature(store.put_document).parameters["blobs"].default)
        for name in store.__all__:
            self.assertTrue(hasattr(store, name), "stale name in __all__: %s" % name)
        self.assertEqual(len(set(store.__all__)), len(store.__all__))

    def test_02_fixture_selfcheck_codec_source_matches_disk(self):
        expected = "real" if _REAL_DOCMODEL_FILE.is_file() else "fixture"
        self.assertEqual(CODEC_SOURCE, expected)
        self.assertIs(store._sibling_module("docmodel"), CODEC)
        # 注入的替身与真件都自称 `cadcore.docmodel`（store 按名字解析，行为一致）
        self.assertEqual(store._l1_encoder()[0], "cadcore.docmodel")
        self.assertEqual(store._l1_decoder()[0], "cadcore.docmodel")
        # 自带降级编码器必须自洽（A5 真缺席时的兜底口径）
        text = store._fallback_document_to_l1({"meta": {"doc_uuid": "u"}, "unit": "mm"})
        self.assertEqual(store._fallback_document_from_l1(text), {"meta": {"doc_uuid": "u"}, "unit": "mm"})
        if CODEC_SOURCE == "real":  # 真件在场 ⇒ 替身绝不参与
            self.assertFalse(hasattr(CODEC, "FixtureDocument"))
            self.assertNotEqual(store._l1_encoder()[0], store.FALLBACK_L1_CODEC)
        else:  # 替身是"装在 sys.modules 里的 A5" ⇒ 摘掉它必须落到显式降级编码器
            self.assertTrue(getattr(CODEC, "FixtureDocument", None) is not None)
            with mock.patch.dict(sys.modules):
                del sys.modules["cadcore.docmodel"]
                self.assertEqual(store._l1_encoder()[0], store.FALLBACK_L1_CODEC)
                self.assertEqual(store._l1_decoder()[0], store.FALLBACK_L1_CODEC)
            self.assertIs(store._sibling_module("docmodel"), CODEC)

    def test_03_fixture_selfcheck_vault_tree_ruler_sees_files(self):
        count, digest = _vault_tree_digest()
        self.assertGreater(count, 0, "vault tree ruler sees no files; zero-change would be fake-green")
        self.assertEqual(len(digest), 64)
        self.assertIn("store.py", [p.name for p in _VAULT_PKG.rglob("store.py")])
        # 尺子灵敏度自证（承重判据必须自证看得见红）：同一棵树两次 ⇒ 同值；改一个字节/加一个文件 ⇒ 变值
        probe = self.tmp / "ruler-probe"
        (probe / "pkg").mkdir(parents=True)
        (probe / "pkg" / "a.py").write_bytes(b"one\n")
        (probe / "pkg" / "b.py").write_bytes(b"two\n")
        first = _tree_digest(probe)
        self.assertEqual(_tree_digest(probe), first, "same tree hashed twice differs; ruler is noisy")
        (probe / "pkg" / "a.py").write_bytes(b"one!\n")
        second = _tree_digest(probe)
        self.assertNotEqual(second[1], first[1], "ruler did not notice a one-byte change")
        self.assertEqual(second[0], first[0])
        (probe / "pkg" / "c.py").write_bytes(b"three\n")
        third = _tree_digest(probe)
        self.assertEqual(third[0], first[0] + 1)
        self.assertNotEqual(third[1], second[1])

    def test_31_vault_lookup_falls_back_to_repo_relative_path(self):
        """底座定位兜底：`sys.path`/`sys.modules` 里都没有 vault 时，模块必须靠仓内相对位置找到它。"""
        with mock.patch.dict(os.environ, {store.STORES_ENV_DIRS: os.path.join(str(self.tmp), "vendored")}):
            roots = store._candidate_package_dirs("vault")
        self.assertEqual(roots[0], os.path.join(str(self.tmp), "vendored", "vault"),
                         "the env-var candidate must come first")
        self.assertIn(str(_VAULT_PKG), roots, "the repo-relative candidate must be present")

        saved_cache = dict(store._CACHE)
        self.addCleanup(lambda: (store._CACHE.clear(), store._CACHE.update(saved_cache)))
        with mock.patch.dict(sys.modules):
            for name in [n for n in list(sys.modules)
                         if n == "freechat_vault" or n.startswith("freechat_vault.")]:
                del sys.modules[name]
            store._CACHE.clear()
            trimmed = [p for p in sys.path if os.path.abspath(p or ".") != str(_VAULT_PKG)]
            with mock.patch.object(sys, "path", trimmed):
                module = store._load_vault_module()
                self.assertEqual(Path(module.__file__).resolve(),
                                 (_VAULT_PKG / "freechat_vault" / "store.py").resolve())
                self.assertIn(str(_VAULT_PKG), sys.path,
                              "the fallback hit must append that dir to sys.path")
                self.assertTrue(callable(getattr(module.Vault, "put_blob", None)))
        # 补丁退出后仍必须绑定到同一个底座文件（`_CACHE` 里可能是重新导入的同文件模块对象）
        self.assertEqual(Path(store._load_vault_module().__file__).resolve(),
                         (_VAULT_PKG / "freechat_vault" / "store.py").resolve())

    def test_32_fixture_stub_is_removed_on_module_teardown(self):
        """夹具替身不许留给同进程的兄弟席位（`tearDownModule` 必须真把 `sys.modules` 还原）。"""
        if CODEC_SOURCE != "fixture":
            self.skipTest("real cadcore.docmodel is on disk; nothing was injected")
        self.assertIs(sys.modules.get("cadcore.docmodel"), CODEC)
        tearDownModule()
        self.assertNotIn("cadcore.docmodel", sys.modules,
                         "stub leaked into sys.modules; sibling test modules would see a fake A5")
        sys.modules["cadcore.docmodel"] = CODEC      # 还原：后面的用例仍要它当 A5 替身
        self.assertIs(store._sibling_module("docmodel"), CODEC)

    def test_30_source_string_literals_are_ascii(self):
        """源码里面向用户/门禁的**字符串字面量**一律 ASCII（文案只许来自语言包，i18n G1 门口径）。"""
        for path in (_STORE_FILE, Path(__file__).resolve()):
            hits = _non_ascii_string_literals(path)
            self.assertEqual(hits, [], "%s has non-ASCII string literals: %s" % (path.name, hits))

    # —— open_store / 索引表 ─────────────────────────────────────────────────────

    def test_04_open_store_creates_vault_and_index_tables(self):
        vault = self.open_store("A")
        self.assertIsInstance(vault, vault_store.Vault)
        names = {row[1] for row in _schema_rows(vault.db_path) if row[0] == "table"}
        for table in store.INDEX_TABLES:
            self.assertIn(table, names)
        self.assertEqual(store.document_index_stats(vault)["documents"], 0)

    def test_05_open_store_create_false_requires_existing_vault(self):
        with self.assertRaises(vault_store.VaultError):
            store.open_store(str(self.tmp / "missing"), create=False)
        vault = self.open_store("B")
        doc = _make_doc()
        store.put_document(vault, doc)
        vault.close()
        reopened = self.open_store("B", create=False)
        self.assertEqual(store.get_l1_text(reopened, _uuid_of(doc)), _l1_text(doc))

    def test_06_ensure_index_is_idempotent_and_adds_only_new_tables(self):
        vault = self.open_store("A")
        first = store.ensure_index(vault)
        self.assertEqual(first["tables_present"], len(store.INDEX_TABLES))
        again = store.ensure_index(vault)
        self.assertEqual(again["tables_created"], 0)
        self.assertEqual(again["tables_present"], len(store.INDEX_TABLES))
        self.assertEqual(again["index_version"], store.INDEX_SCHEMA_VERSION)

    def test_07_vault_schema_unchanged_versus_pristine_vault(self):
        pristine = vault_store.Vault(str(self.tmp / "P"))
        pristine.close()
        used = self.open_store("U")
        doc = _make_doc()
        store.put_document(used, doc, blobs={"plate.dxf": DXF_BYTES})
        store.export_package(used, str(self.tmp / "schema-probe.fcx"))
        before = _schema_rows(Path(self.tmp / "P") / vault_store.DB_FILENAME)
        after = _schema_rows(used.db_path)
        self.assertGreater(len(before), 0, "comparison set is empty; this case would be meaningless")
        before_map, after_map = {row[1]: row for row in before}, {row[1]: row for row in after}
        self.assertEqual(sorted(set(before_map) - set(after_map)), [], "vault tables/indexes disappeared")
        for name, row in before_map.items():
            self.assertEqual(after_map[name], row, "vault object %s DDL changed" % name)
        extras = sorted(set(after_map) - set(before_map))
        self.assertEqual(extras, sorted(set(store.INDEX_TABLES) | {_CAD_INDEX_NAME}),
                         "bridge may only add its own two tables and one index")

    # —— put_document ───────────────────────────────────────────────────────────

    def test_08_put_document_addresses_l1_blob_by_sha256(self):
        vault = self.open_store("A")
        doc = _make_doc()
        counts = store.put_document(vault, doc)
        text = _l1_text(doc)
        sha = _sha256(text.encode("utf-8"))
        self.assertEqual(counts["documents_new"], 1)
        self.assertEqual(counts["revision"], 1)
        self.assertTrue(vault.has_blob(sha))
        self.assertEqual(vault.get_blob(sha).decode("utf-8"), text)
        att = _attachments(vault)[sha]
        self.assertEqual(att["name"], "%s.l1.json" % _uuid_of(doc))
        self.assertEqual(att["mime"], store.L1_MIME)
        self.assertEqual(att["ext"][store.BLOB_EXT_KEY]["kind"], store.L1_ROLE)
        self.assertEqual(att["ext"][store.BLOB_EXT_KEY]["hash_algo"], "sha256")
        self.assertEqual(store.HASH_ALGO, "sha256")

    def test_09_put_document_returns_int_counts_only(self):
        vault = self.open_store("A")
        counts = store.put_document(vault, _make_doc(), blobs={"plate.dxf": DXF_BYTES})
        self.assertTrue(counts)
        for key, value in counts.items():
            self.assertIsInstance(value, int, "count %s is not an int" % key)
            self.assertNotIsInstance(value, bool)
        self.assertEqual(counts["originals_new"], 1)
        self.assertEqual(counts["originals_dedup"], 0)

    def test_10_put_document_rejects_document_without_uuid(self):
        vault = self.open_store("A")
        broken = _replace(_make_doc(), meta={"title_key": "doc.untitled"})
        with self.assertRaises(store.StoreError):
            store.put_document(vault, broken)
        self.assertEqual(store.document_index_stats(vault)["documents"], 0)

    def test_11_put_document_rejects_bad_blobs_argument(self):
        vault = self.open_store("A")
        with self.assertRaises(store.StoreError):
            store.put_document(vault, _make_doc(), blobs=[("plate.dxf", DXF_BYTES)])
        with self.assertRaises(store.StoreError):
            store.put_document(vault, _make_doc(), blobs={"plate.dxf": "not-bytes"})
        with self.assertRaises(store.StoreError):
            store.put_document(vault, _make_doc(), blobs={"": DXF_BYTES})
        self.assertEqual(store.document_index_stats(vault)["documents"], 0)
        self.assertEqual(list(store.iter_documents(vault)), [], "rejected calls must write nothing")

    def test_12_put_document_is_byte_idempotent(self):
        vault = self.open_store("A")
        doc = _make_doc()
        first = store.put_document(vault, doc, blobs={"plate.dxf": DXF_BYTES})
        digest_before = vault.content_digest()
        metas_before = _canonical(list(vault.iter_attachments()))
        files_before = _blob_files(self.tmp / "A")
        second = store.put_document(vault, doc, blobs={"plate.dxf": DXF_BYTES})
        self.assertEqual(second["documents_unchanged"], 1)
        self.assertEqual(second["documents_new"], 0)
        self.assertEqual(second["documents_updated"], 0)
        self.assertEqual(second["revision"], first["revision"])
        self.assertEqual(second["l1_blobs_new"], 0)
        self.assertEqual(second["l1_blobs_dedup"], 1)
        self.assertEqual(second["originals_dedup"], 1)
        self.assertEqual(vault.content_digest(), digest_before)
        self.assertEqual(_canonical(list(vault.iter_attachments())), metas_before)
        self.assertEqual(_blob_files(self.tmp / "A"), files_before)
        self.assertEqual(store.get_l1_text(vault, _uuid_of(doc)), _l1_text(doc))

    def test_13_put_document_same_uuid_new_content_bumps_revision(self):
        vault = self.open_store("A")
        doc = _make_doc()
        store.put_document(vault, doc)
        updated = _edit_meta(doc, title_key="doc.revised")
        self.assertNotEqual(_l1_text(doc), _l1_text(updated),
                            "content did not actually change; this case would be meaningless")
        counts = store.put_document(vault, updated)
        self.assertEqual(counts["documents_updated"], 1)
        self.assertEqual(counts["documents_new"], 0)
        self.assertEqual(counts["revision"], 2)
        self.assertEqual(store.get_l1_text(vault, _uuid_of(doc)), _l1_text(updated))
        shas = {rec["sha256"] for rec in vault.iter_attachments()}
        self.assertIn(_sha256(_l1_text(doc).encode("utf-8")), shas,
                      "previous L1 blob must stay on disk (content addressing never overwrites)")
        self.assertIn(_sha256(_l1_text(updated).encode("utf-8")), shas)

    def test_14_put_document_originals_dedup_and_replace_role(self):
        vault = self.open_store("A")
        doc = _make_doc()
        store.put_document(vault, doc, blobs={"plate.dxf": DXF_BYTES, "plate.step": STEP_BYTES})
        first_shas = {rec["name"]: rec["sha256"] for rec in vault.iter_attachments()}
        self.assertIn("plate.dxf", first_shas)
        again = store.put_document(vault, doc, blobs={"plate.dxf": DXF_BYTES, "plate.step": STEP_BYTES})
        self.assertEqual(again["originals_dedup"], 2)
        self.assertEqual(again["originals_new"], 0)
        new_dxf = DXF_BYTES + b"999\n"
        changed = store.put_document(vault, doc, blobs={"plate.dxf": new_dxf, "plate.step": STEP_BYTES})
        self.assertEqual(changed["originals_new"], 1, "changed bytes must land as one new blob")
        self.assertEqual(changed["originals_dedup"], 1, "unchanged step bytes must hit the existing blob")
        with vault._tx() as conn:  # 白盒只读：确认 role 是"替换"而不是"堆积"
            roles = conn.execute("SELECT role, sha256 FROM cad_document_blobs WHERE doc_uuid = ? "
                                 "ORDER BY role", (_uuid_of(doc),)).fetchall()
        self.assertEqual([row["role"] for row in roles], ["plate.dxf", "plate.step"])
        self.assertEqual(dict((row["role"], row["sha256"]) for row in roles)["plate.dxf"],
                         _sha256(new_dxf))
        self.assertTrue(vault.has_blob(_sha256(DXF_BYTES)),
                        "previous original blob must stay on disk")

    # —— get_document / get_l1_text ──────────────────────────────────────────────

    def test_15_get_l1_text_roundtrip_is_byte_identical(self):
        vault = self.open_store("A")
        doc = _make_doc(unit="inch", standard_profile="gbt", projection_angle="first", title_key="doc.plate")
        store.put_document(vault, doc)
        text = store.get_l1_text(vault, _uuid_of(doc))
        self.assertEqual(text, _l1_text(doc))
        self.assertEqual(_l1_text(store.get_document(vault, _uuid_of(doc))), text)

    def test_16_get_document_unknown_uuid_raises(self):
        vault = self.open_store("A")
        store.put_document(vault, _make_doc())
        with self.assertRaises(store.DocumentNotFound):
            store.get_document(vault, "no-such-doc")
        with self.assertRaises(store.DocumentNotFound):
            store.get_l1_text(vault, "no-such-doc")

    def test_17_get_document_detects_tampered_blob(self):
        vault = self.open_store("A")
        doc = _make_doc()
        store.put_document(vault, doc)
        sha = _sha256(_l1_text(doc).encode("utf-8"))
        target = Path(vault._blob_path(sha))
        target.write_bytes(b"{}")   # 就地篡改：内容与"文件名即校验和"不符
        with self.assertRaises(store.StoreCorrupt):
            store.get_l1_text(vault, _uuid_of(doc))
        stats = store.document_index_stats(vault)
        self.assertEqual(stats["documents"], 1, "index row must survive (no silent row deletion)")
        self.assertEqual(stats["documents_missing_blob"], 0)

    def test_18_get_document_reports_missing_blob(self):
        vault = self.open_store("A")
        doc = _make_doc()
        store.put_document(vault, doc)
        Path(vault._blob_path(_sha256(_l1_text(doc).encode("utf-8")))).unlink()
        with self.assertRaises(store.StoreCorrupt):
            store.get_document(vault, _uuid_of(doc))
        self.assertEqual(store.document_index_stats(vault)["documents_missing_blob"], 1)

    # —— 三态记账 ────────────────────────────────────────────────────────────────

    def test_19_index_stats_zero_when_really_empty(self):
        vault = self.open_store("A")
        stats = store.document_index_stats(vault)
        self.assertEqual(stats["index_present"], 1)
        self.assertEqual(stats["documents"], 0, "a real zero must be reported as 0")
        self.assertEqual(stats["originals"], 0)
        self.assertEqual(stats["l1_bytes"], 0)
        self.assertEqual(list(store.iter_documents(vault)), [])

    def test_20_index_stats_uncaptured_is_minus_one_when_index_absent(self):
        raw = vault_store.Vault(str(self.tmp / "raw"))   # 只用底座、不经本桥 ⇒ 没有索引表
        self.addCleanup(raw.close)
        raw.put_blob(b"x", name="note.txt")
        stats = store.document_index_stats(raw)
        self.assertEqual(stats["index_present"], 0)
        self.assertEqual(stats["documents"], -1, "uncaptured must be -1, never a silent 0")
        self.assertEqual(stats["originals"], -1)
        self.assertEqual(stats["l1_bytes"], -1)
        self.assertEqual(stats["index_version"], -1, "absent index cannot report a version")
        empty = store.document_index_stats(self.open_store("A"))
        self.assertEqual(sorted(stats), sorted(empty),
                         "both states must expose the same key set (callers must not KeyError)")
        self.assertEqual([stats[key] for key in sorted(stats) if key != "index_present"],
                         [-1] * (len(stats) - 1), "every count in the absent state must be -1")
        with self.assertRaises(store.StoreIndexMissing):
            list(store.iter_documents(raw))
        with self.assertRaises(store.StoreIndexMissing):
            store.get_l1_text(raw, "any")

    def test_21_iter_documents_reports_na_fields_and_blob_presence(self):
        vault = self.open_store("A")
        doc = _make_doc(unit="mm", standard_profile="iso", projection_angle=None, title_key="doc.untitled")
        store.put_document(vault, doc)
        store.put_document(vault, _make_doc(unit="inch", standard_profile="ansi",
                                            projection_angle="third", title_key="doc.other"))
        rows = list(store.iter_documents(vault))
        self.assertEqual(len(rows), 2)
        by_uuid = {row["doc_uuid"]: row for row in rows}
        first = by_uuid[_uuid_of(doc)]
        self.assertIsNone(first["projection_angle"], "NA must stay None; never invented as 'first'")
        self.assertIn("projection_angle", first["na_fields"])
        self.assertEqual(first["blob_present"], 1)
        other = [row for row in rows if row["doc_uuid"] != _uuid_of(doc)][0]
        self.assertEqual(other["projection_angle"], "third")
        self.assertNotIn("projection_angle", other["na_fields"])
        self.assertEqual(first["revision"], 1)
        self.assertEqual(store.document_index_stats(vault)["na_rows"], 1)

    # —— export_package / .fcx 往返 ──────────────────────────────────────────────

    def test_22_export_package_returns_abs_path_and_valid_archive(self):
        vault = self.open_store("A")
        store.put_document(vault, _make_doc(), blobs={"plate.dxf": DXF_BYTES})
        out = store.export_package(vault, str(self.tmp / "pkg.fcx"))
        self.assertTrue(os.path.isabs(out))
        self.assertTrue(os.path.isfile(out))
        with vault_fcx.FcxArchive(out) as arch:
            self.assertEqual(arch.manifest["format"], vault_fcx.FORMAT_NAME)
            self.assertEqual(arch.manifest["counts"]["attachments"], 2)

    def test_23_export_package_record_payload_is_deterministic(self):
        vault = self.open_store("A")
        store.put_document(vault, _make_doc(), blobs={"plate.dxf": DXF_BYTES})
        first = store.export_package(vault, str(self.tmp / "one.fcx"))
        second = store.export_package(vault, str(self.tmp / "two.fcx"))
        self.assertNotEqual(first, second)
        with vault_fcx.FcxArchive(first) as a, vault_fcx.FcxArchive(second) as b:
            self.assertEqual(a._read_verified("attachments.jsonl"), b._read_verified("attachments.jsonl"))
            self.assertEqual(a.manifest["counts"], b.manifest["counts"])

    def test_24_export_package_refuses_missing_blob_and_writes_nothing(self):
        vault = self.open_store("A")
        doc = _make_doc()
        store.put_document(vault, doc)
        Path(vault._blob_path(_sha256(_l1_text(doc).encode("utf-8")))).unlink()
        out = self.tmp / "broken.fcx"
        with self.assertRaises(store.StoreCorrupt):
            store.export_package(vault, str(out))
        self.assertFalse(out.exists(), "failed preflight must not leave a half-written archive")

    def test_25_fcx_roundtrip_rebuilds_index_and_document(self):
        vault = self.open_store("A")
        doc = _make_doc(unit="inch", standard_profile="din", projection_angle=None, title_key="doc.plate")
        store.put_document(vault, doc, blobs={"plate.dxf": DXF_BYTES})
        l1_text = _l1_text(doc)
        dxf_sha = _sha256(DXF_BYTES)
        out = store.export_package(vault, str(self.tmp / "pkg.fcx"))

        fresh = vault_store.Vault(str(self.tmp / "B"))    # 新库：不经本桥（模拟另一台设备）
        self.addCleanup(fresh.close)
        stats = vault_fcx.import_fcx(fresh, out)          # 底座原生导入：文档索引随附件 ext 走
        self.assertGreaterEqual(stats.imported, 2)
        self.assertEqual(store.document_index_stats(fresh)["documents"], -1,
                         "fresh vault has no index yet, so the count is uncaptured")
        rebuilt = store.rebuild_index_from_blobs(fresh)
        self.assertEqual(rebuilt["l1_found"], 1)
        self.assertEqual(rebuilt["rebuilt"], 1)
        self.assertEqual(rebuilt["originals_linked"], 1)
        self.assertEqual(rebuilt["skipped_other"], 0)
        self.assertEqual(store.get_l1_text(fresh, _uuid_of(doc)), l1_text)
        self.assertEqual(_l1_text(store.get_document(fresh, _uuid_of(doc))), l1_text)
        self.assertTrue(fresh.has_blob(dxf_sha))
        self.assertEqual(fresh.get_blob(dxf_sha), DXF_BYTES)
        self.assertEqual([row["role"] for row in store.store_facts(fresh, doc_uuid=_uuid_of(doc))["document"]["originals"]],
                         ["plate.dxf"])
        again = store.export_package(fresh, str(self.tmp / "again.fcx"))
        with vault_fcx.FcxArchive(out) as a, vault_fcx.FcxArchive(again) as b:
            self.assertEqual(a._read_verified("attachments.jsonl"), b._read_verified("attachments.jsonl"))

    def test_26_rebuild_index_from_blobs_counts_are_real(self):
        vault = self.open_store("A")
        store.put_document(vault, _make_doc())
        vault.put_blob(b"not-a-cad-doc", name="notes.bin")        # 非 CAD 附件 ⇒ skipped_other
        counts = store.rebuild_index_from_blobs(vault)
        self.assertEqual(counts["scanned"], 2)
        self.assertEqual(counts["l1_found"], 1)
        self.assertEqual(counts["skipped_other"], 1)
        self.assertEqual(counts["missing_blob"], 0)
        self.assertEqual(counts["digest_mismatch"], 0)

        other = self.open_store("C")
        doc = _make_doc()
        store.put_document(other, doc)
        Path(other._blob_path(_sha256(_l1_text(doc).encode("utf-8")))).write_bytes(b"tampered")
        tampered = store.rebuild_index_from_blobs(other)
        self.assertEqual(tampered["digest_mismatch"], 1)
        self.assertEqual(tampered["rebuilt"], 0, "digest mismatch must not produce an index row")

    def test_27_document_survives_reopen(self):
        vault = self.open_store("A")
        doc = _make_doc(title_key="doc.reopen")
        store.put_document(vault, doc, blobs={"plate.dxf": DXF_BYTES})
        vault.close()
        reopened = self.open_store("A", create=False)
        self.assertEqual(store.get_l1_text(reopened, _uuid_of(doc)), _l1_text(doc))
        stats = store.document_index_stats(reopened)
        self.assertEqual(stats["documents"], 1)
        self.assertEqual(stats["documents_with_blob"], 1)
        self.assertEqual(stats["originals"], 1)

    def test_28_content_digest_moves_when_a_document_is_added(self):
        """`sync.py` push 以 `content_digest()` 判"内容变没变" ⇒ CAD 文档必须能被它看见。"""
        vault = self.open_store("A")
        before = vault.content_digest()
        doc = _make_doc()
        store.put_document(vault, doc)
        after = vault.content_digest()
        self.assertNotEqual(before, after)
        store.put_document(vault, doc)
        self.assertEqual(vault.content_digest(), after,
                         "re-putting identical content must not move the digest (idempotent)")

    # —— 底座零改动（承重约束）──────────────────────────────────────────────────

    def test_29_vault_package_files_sha256_unchanged_across_workflow(self):
        before = _vault_tree_digest()
        self.assertGreater(before[0], 0)
        vault = self.open_store("A")
        doc = _make_doc(unit="inch", projection_angle="third")
        store.put_document(vault, doc, blobs={"plate.dxf": DXF_BYTES})
        store.ensure_index(vault)
        store.rebuild_index_from_blobs(vault)
        store.document_index_stats(vault)
        list(store.iter_documents(vault))
        store.export_package(vault, str(self.tmp / "pkg.fcx"))
        fresh = vault_store.Vault(str(self.tmp / "B"))
        self.addCleanup(fresh.close)
        vault_fcx.import_fcx(fresh, str(self.tmp / "pkg.fcx"))
        store.rebuild_index_from_blobs(fresh)
        store.get_l1_text(fresh, _uuid_of(doc))
        after = _vault_tree_digest()
        self.assertEqual(before, after, "packages/vault/ was modified; A15 requires zero-change reuse")
        self.assertEqual(after, _VAULT_SNAPSHOT, "vault files changed while this suite was running")


# ── 判定行：跑完打一行机读结果（数字以当次输出为准）────────────────────────────────────


class _VerdictSuite(unittest.TestSuite):
    @staticmethod
    def _count(suite) -> tuple[int, int]:
        """(整个 suite 的用例数, 其中属于本模块的用例数)——目录级发现会把兄弟席位的用例一起交进来。"""
        total = own = 0
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                sub_total, sub_own = _VerdictSuite._count(item)
                total += sub_total
                own += sub_own
            else:
                total += 1
                test_id = item.id()
                # 三种装载形态都算"本模块"：`test_store.X` / `tests.test_store.X` / 直跑时的 `__main__.X`
                own += 1 if ("test_store" in test_id or test_id.startswith("__main__.")) else 0
        return total, own

    def run(self, result, debug=False):
        _total, own = self._count(self)
        super().run(result, debug=debug)
        unexpected = len(getattr(result, "unexpectedSuccesses", []))
        verdict = "PASS" if (result.wasSuccessful() and unexpected == 0) else "FAIL"
        files, digest = _vault_tree_digest()
        print(
            "CADCORE_STORE_TESTS verdict=%s tests=%d suite_tests=%d failures=%d errors=%d skipped=%d "
            "codec=%s vault_files=%d vault_tree_sha256=%s"
            % (verdict, own, result.testsRun, len(result.failures), len(result.errors),
               len(result.skipped), CODEC_SOURCE, files, digest)
        )
        return result


def load_tests(loader, tests, pattern):
    """unittest 钩子：保持自动发现，同时在跑完时打印判定行。"""
    return _VerdictSuite(tests)


def tearDownModule():
    """不给同进程的兄弟席位留状态：**夹具替身用完就摘**（真件在场时本函数是 no-op）。

    替身只代表"A5 还不存在"这一事实；留在 `sys.modules` 里会让同进程的其它测试模块误以为
    `cadcore.docmodel` 已经落盘 —— 那是把测试夹具伪装成真件，必须还原成真实状态。
    """
    if CODEC_SOURCE == "fixture" and sys.modules.get("cadcore.docmodel") is CODEC:
        del sys.modules["cadcore.docmodel"]


if __name__ == "__main__":  # pragma: no cover - 允许 `python3 tests/test_store.py`
    unittest.main(verbosity=2)
