"""cadcore.store —— A15：CAD 文档 ↔ vault 底座桥（L1 文本真源按内容寻址进 blobs，索引只落新表）。

一句话用途
----------
把 A5 `Document`（L1 文本真源）与随附原件（DXF/STEP 等字节）持久化进 `oss-alternatives/packages/vault`
（SQLite/WAL + 内容寻址 blob + `.fcx` 开放格式），对外只暴露四个冻结入口：
`open_store` / `put_document` / `get_document` / `export_package`。

为什么这样设计
--------------
1. **底座零改动（承重约束）**：`packages/vault/` 一个字节都不改。连接与事务只走底座自己的
   `Vault._tx()`（`freechat_vault/fcx.py` 已有同款先例：`_vault_tx()` 就是 `getattr(vault, "_tx")`），
   不 monkeypatch、不建影子库、不动它的任何一张表。
2. **原件按内容寻址**：L1 文本字节与随附原件一律经 `Vault.put_blob()` 落 `blobs/<sha[:2]>/<sha256>`，
   索引表里只存 sha256 —— 同内容天然去重、"文件名即校验和"、无副本漂移。
3. **索引只落新表**：`cad_documents` / `cad_document_blobs` 两张**新表**（vault 的表一个字段都不动）。
   单独建表而不是往 `chats/messages` 里塞：CAD 文档不是聊天记录，语义不同就不共用行，
   否则底座的排序键、FTS、`.fcx` 计数全会被污染。
4. **`.fcx` 往返不靠旁路文件**：vault 的 fcx 只认五类记录（`IMPORT_ORDER` 固定），没有 CAD 记录类型；
   所以文档索引随**附件级 `ext`** 走 —— `put_blob(..., ext={"cadcore": {...}})` 会被 `fcx.export_fcx`
   写进 `attachments.jsonl`，被 `fcx.import_fcx` 原样写回底座 ⇒ 一个 `.fcx` 就能在另一台设备上
   **重建索引**（`rebuild_index_from_blobs`），全程零 schema 改动、零旁路清单文件。
5. **三态记账（契约口径 `0`=真为零 / `-1`=未采 / `NA`=不适用）**：
   `document_index_stats()` 在索引表缺失时给 `documents=-1`（未采）而**不是 0**；
   `projection_angle` 为 NULL 时如实给 `None`(NA) 并计入 `na_fields`；缺字段一律 `-1`/`None`，绝不补 0。
6. **零包内硬依赖**：A3 `errors.py` / A5 `docmodel.py` 尚未落盘时本模块仍可导入（缺席即降级、
   绝不在 import 期硬失败）。载入策略与 `cadcore/i18n/catalog.py` 同款：只为"能在兄弟模块落盘前单独跑起来"。

登记（与冻结契约的差异 / 欠定项，一律只登记不擅自改接口）
--------------------------------------------------------
1. 契约注释写「原件按 **BLAKE3** 进 blobs/」，但底座只有 sha256（`Vault.put_blob()` 的返回值与
   `blobs/<sha[:2]>/<sha256>` 路径闸门都是 `[0-9a-f]{64}`），且 BLAKE3 不是标准库。
   承重约束是"复用 vault 零改动" ⇒ 本实现用 **sha256** 做内容寻址，并把口径显式落在
   `HASH_ALGO` 常量、索引行 `hash_algo` 列与 blob `ext` 里（**不冒充 BLAKE3**）。
2. A5 `docmodel` 缺席时用**本模块自带的降级编码器**：规范化 JSON（键升序 + 紧凑分隔符 +
   `ensure_ascii=False`，与 vault/fcx 同一口径）。它**只**在 A5 不在场时生效，且编码器名字
   （`l1_codec`）逐条写进索引行与 blob `ext` —— 将来 A5 落盘后，旧数据的来源仍可辨认，
   不会出现"两套 L1 口径互相冒充"。解码侧优先用 A5 的解码器（`schema_version=1` 时迁移链是恒等）。
3. **不写 vault op-log**：vault v1 的同步明确"不做 CRDT/op-log 回放"（`sync.py` §6），写一条底座
   不认识的 op 只会让变更历史的语义变脏。**欠账**：CAD 文档的删除/改名目前只体现在索引表里。
4. `sync.py` 的 push 以 `Vault.content_digest()` 判"内容有没有变"，而该摘要**包含** `attachment_meta`
   ⇒ 新增/更新一份 CAD 文档（L1 blob 就是一行附件元数据）会改变摘要、自动触发一次新归档导出。
   这是结论不是假设，`tests/test_store.py` 里有对应用例把它钉住。
5. blob 落盘不经 SQLite 事务（底座自己的已知边界，见 `fcx.py` docstring）：极端情况下可能留下
   **孤儿 blob**（无害：同内容同名，下次复用），但**不会**留下半条索引行 —— 索引写入包在
   `Vault._tx()` 单事务里。
6. **两份文档共享同一份原件字节**（同 role 名、同内容）时：内容寻址下只有一个 blob（正确），
   而 blob 的 `ext` 只能记一个归属 ⇒ `.fcx` 导入新库后 `rebuild_index_from_blobs()` 只把该原件
   链回**最后写入的那份文档**。范围限定：`cad_document_blobs` 索引表按文档各记一行、不受影响，
   L1 文档本体也一条不丢 —— 受影响的只是"重建后附加原件的归属行"。要根治得给 blob 加多归属
   （vault 侧改动）或在 .fcx 里加旁路清单（改格式）⇒ 两者都越界，故只登记。

对外契约（照抄 `MVP-INTERFACES.md` §B A15）
------------------------------------------
`open_store(root, *, create=True)` · `put_document(vault, doc, *, blobs=None)` ·
`get_document(vault, doc_uuid)` · `export_package(vault, out_path)`
（另附只读辅助：`ensure_index` / `get_l1_text` / `iter_documents` / `document_index_stats` /
`rebuild_index_from_blobs` / `hash_algo` / `store_facts`）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping

__all__ = [
    "BLOB_EXT_KEY",
    "HASH_ALGO",
    "INDEX_SCHEMA_VERSION",
    "INDEX_TABLES",
    "L1_MIME",
    "L1_ROLE",
    "STORES_ENV_DIRS",
    "STATE_UNCAPTURED",
    "StoreError",
    "StoreDependencyMissing",
    "StoreIndexMissing",
    "StoreCorrupt",
    "DocumentNotFound",
    "open_store",
    "put_document",
    "get_document",
    "get_l1_text",
    "export_package",
    "ensure_index",
    "iter_documents",
    "document_index_stats",
    "rebuild_index_from_blobs",
    "store_facts",
]

# ── 契约面常量（判定行/取证用；都不是"可调参数"）────────────────────────────────────────
#: 内容寻址口径。契约注释写的是 BLAKE3，但底座只有 sha256 且 BLAKE3 非标准库 ⇒ 见模块 docstring 登记 1。
HASH_ALGO = "sha256"

#: blob 的附件级 `ext` 命名空间（`.fcx` 往返靠它，见模块 docstring 设计点 4）。
BLOB_EXT_KEY = "cadcore"

#: L1 文本真源 blob 的 MIME 与角色名。
L1_MIME = "application/vnd.cadcore.l1+json"
L1_ROLE = "l1"

#: 本桥自建的两张索引表（底座的表一张都不碰）。
INDEX_TABLES = ("cad_documents", "cad_document_blobs")

#: 索引结构版本（写进每一行；将来加列时靠它判定旧行）。
INDEX_SCHEMA_VERSION = 1

#: 三态口径里的"未采"哨兵（`0` 只留给"真为零"）。
STATE_UNCAPTURED = -1

#: 追加 `packages/` 级搜索路径（`os.pathsep` 分隔）；用于底座被 vendored 到别处的场合。
STORES_ENV_DIRS = "CADCORE_PACKAGES_DIRS"

#: 规范化 JSON 口径（与 `freechat_vault.store._canonical_json` / `fcx.canonical_json` 逐字一致）。
_JSON_KWARGS = {"sort_keys": True, "ensure_ascii": False, "separators": (",", ":")}

#: 降级 L1 编码器名（A5 `docmodel` 在场时**不会**被使用，但名字会落进索引行）。
FALLBACK_L1_CODEC = "cadcore.store.fallback-json-v1"

#: 按后缀给 MIME **提示**（未知后缀 ⇒ 空串 = NA，不猜）。
_MIME_HINTS = {
    ".dxf": "image/vnd.dxf",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".step": "model/step",
    ".stp": "model/step",
    ".stl": "model/stl",
    ".iges": "model/iges",
    ".igs": "model/iges",
    ".json": "application/json",
    ".txt": "text/plain",
}

#: 索引行里"允许为空/缺失"的字段（读侧据此算 `na_fields`，而不是把它们当成空串糊过去）。
_NA_FIELDS = ("unit", "standard_profile", "projection_angle", "title_key")

_HERE = os.path.dirname(os.path.abspath(__file__))            # <...>/packages/cadcore/cadcore
_PACKAGES_DIR = os.path.dirname(os.path.dirname(_HERE))       # <...>/packages
_CACHE: dict[str, Any] = {}


# ── 异常族（本模块自带，不硬依赖 A3 `errors.py`；落盘后可由调用方按语义映射）──────────────


class StoreError(Exception):
    """本桥的显式失败信号基类（不是静默降级、不是 None）。"""


class StoreDependencyMissing(StoreError):
    """承重依赖（`freechat_vault`）找不到 —— **显式降级信号**，绝不假装成功。"""


class StoreIndexMissing(StoreError):
    """索引表不在（该 vault 还没被 `open_store(create=True)` 或任何写操作碰过）。

    读侧**必须**报这个错而不是给空清单：空清单会把"未采"伪装成"0 份文档"。
    """


class StoreCorrupt(StoreError):
    """索引与盘上内容不一致（blob 缺失 / 内容与记录的 sha256 不符）。"""


class DocumentNotFound(StoreError):
    """索引里没有这个 `doc_uuid`。"""


# ── 承重依赖的懒加载（import 期零硬失败）──────────────────────────────────────────────


def _candidate_package_dirs(package_dir_name: str) -> list[str]:
    """按优先级给出可能存放 `packages/<name>` 的目录：环境变量在前，本仓相对位置兜底。"""
    roots: list[str] = []
    for part in os.environ.get(STORES_ENV_DIRS, "").split(os.pathsep):
        part = part.strip()
        if part:
            roots.append(os.path.join(part, package_dir_name))
    roots.append(os.path.join(_PACKAGES_DIR, package_dir_name))
    unique: list[str] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _import_sibling(full_name: str, package_dir_name: str, rel_parts: tuple[str, ...],
                    *, what: str) -> Any:
    """导入兄弟包模块：先按正常 sys.path，再按仓内相对位置；都没有 ⇒ `StoreDependencyMissing`。"""
    if importlib.util.find_spec(full_name.split(".", 1)[0]) is not None:
        if importlib.util.find_spec(full_name) is not None:
            return importlib.import_module(full_name)
    for root in _candidate_package_dirs(package_dir_name):
        if os.path.isfile(os.path.join(root, *rel_parts)):
            if root not in sys.path:
                sys.path.insert(0, root)
            return importlib.import_module(full_name)  # 目录在但包坏了：如实抛，不吞
    raise StoreDependencyMissing(
        "%s not found (expected package `%s`); set %s to its parent dir"
        % (what, package_dir_name, STORES_ENV_DIRS)
    )


def _load_vault_module() -> Any:
    """底座 `freechat_vault.store`（零改动复用）。"""
    mod = _CACHE.get("vault.store")
    if mod is None:
        mod = _import_sibling("freechat_vault.store", "vault",
                              ("freechat_vault", "store.py"), what="freechat_vault.store")
        _CACHE["vault.store"] = mod
    return mod


def _load_fcx_module() -> Any:
    """底座的 `.fcx` 读写层 `freechat_vault.fcx`。"""
    mod = _CACHE.get("vault.fcx")
    if mod is None:
        mod = _import_sibling("freechat_vault.fcx", "vault",
                              ("freechat_vault", "fcx.py"), what="freechat_vault.fcx")
        _CACHE["vault.fcx"] = mod
    return mod


def _sibling_module(module_name: str) -> Any:
    """`cadcore.<module_name>`（A3/A5）在不在场 —— 每次现查 `sys.modules`，不缓存。

    先查 `sys.modules` 是为了让调用方/测试能注入替身；这也让"A5 落盘后自动生效"成立。
    """
    full = "cadcore." + module_name
    mod = sys.modules.get(full)
    if mod is not None:
        return mod
    if importlib.util.find_spec(full) is None:
        return None
    return importlib.import_module(full)


# ── L1 编解码器（A5 在场就用它，缺席才降级）────────────────────────────────────────────


def _as_plain(value: Any, *, what: str) -> Any:
    """把文档对象变成纯 JSON 值：`Mapping` 原样 / dataclass 递归 / `as_dict()` 出口。"""
    if isinstance(value, Mapping):
        return dict(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return as_dict()
    raise StoreError("%s is neither a Mapping nor a dataclass; cannot serialize" % what)


def _fallback_document_to_l1(doc: Any) -> str:
    """降级编码器：规范化 JSON（**只**在 A5 `docmodel` 缺席时生效，见模块 docstring 登记 2）。"""
    return json.dumps(_as_plain(doc, what="document"), **_JSON_KWARGS)


def _fallback_document_from_l1(text: str) -> Any:
    """降级解码器：L1 文本就是 JSON ⇒ 给回纯 dict（鸭子类型的 `Document`）。"""
    try:
        return json.loads(text)
    except ValueError as exc:
        raise StoreError("L1 text is not valid JSON: %s" % exc) from exc


def _l1_encoder() -> tuple[str, Any]:
    """返回 `(codec_name, document_to_l1)`；A5 在场优先，缺席走降级编码器。"""
    mod = _sibling_module("docmodel")
    fn = getattr(mod, "document_to_l1", None) if mod is not None else None
    if callable(fn):
        return ("cadcore.docmodel", fn)
    return (FALLBACK_L1_CODEC, _fallback_document_to_l1)


def _l1_decoder() -> tuple[str, Any]:
    """返回 `(codec_name, document_from_l1)`；A5 在场优先（`schema_version=1` 时迁移链是恒等）。"""
    mod = _sibling_module("docmodel")
    fn = getattr(mod, "document_from_l1", None) if mod is not None else None
    if callable(fn):
        return ("cadcore.docmodel", fn)
    return (FALLBACK_L1_CODEC, _fallback_document_from_l1)


# ── 小工具 ──────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """UTC ISO8601（秒精度）—— 与契约 `created_at(ISO8601 UTC)` 同口径。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """从 dict / dataclass / 普通对象里取字段（Document 的确切形态由 A5 定，这里只做鸭子读取）。"""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        value = obj.get(key, default)
    else:
        value = getattr(obj, key, default)
    return default if value is None else value


def _as_int(value: Any) -> int:
    """能取到整数就给整数，否则 `-1`（未采）—— **不补 0**。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return STATE_UNCAPTURED
    return value


def _as_text(value: Any) -> str:
    """能取到非空字符串就给字符串，否则空串（读侧据此算 `na_fields`）。"""
    return value if isinstance(value, str) else ""


def _doc_uuid(doc: Any) -> str:
    """读 `doc.meta.doc_uuid`；缺了就**报错**（绝不替用户编一个 uuid）。"""
    meta = _get(doc, "meta", {})
    doc_uuid = _as_text(_get(meta, "doc_uuid", ""))
    if not doc_uuid:
        raise StoreError("document has no meta.doc_uuid; refusing to invent one")
    return doc_uuid


def _doc_row_fields(doc: Any) -> dict[str, Any]:
    """从文档对象抽出要落索引的字段；取不到的用 `-1`/`None`/空串（三态口径，不补 0）。"""
    meta = _get(doc, "meta", {})
    angle = _get(doc, "projection_angle", None)
    return {
        "schema_version": _as_int(_get(doc, "schema_version", None)),
        "unit": _as_text(_get(doc, "unit", "")),
        "standard_profile": _as_text(_get(doc, "standard_profile", "")),
        "projection_angle": angle if isinstance(angle, str) and angle else None,
        "title_key": _as_text(_get(meta, "title_key", "")),
        "app_version": _as_text(_get(meta, "app_version", "")),
        "created_at": _as_text(_get(meta, "created_at", "")),
    }


def _mime_hint(name: str) -> str:
    """按后缀给 MIME 提示；未知 ⇒ 空串（NA，不猜）。"""
    return _MIME_HINTS.get(os.path.splitext(name)[1].lower(), "")


def _vault_conn(vault: Any) -> sqlite3.Connection:
    """拿底座的连接（零改动复用其内部连接；它关了/没开就**报错**，不偷偷自开第二条写连接）。"""
    conn = getattr(vault, "_conn", None)
    if conn is None:
        raise StoreError("vault connection unavailable (not open or already closed): %r" % (vault,))
    return conn


@contextmanager
def _tx(vault: Any):
    """底座的写事务：优先用 `Vault._tx()`（支持嵌套 SAVEPOINT）；它改内部实现时再退化自管事务。"""
    tx = getattr(vault, "_tx", None)
    if callable(tx):
        with tx() as conn:
            yield conn
        return
    conn = _vault_conn(vault)
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _table_present(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _index_present(vault: Any) -> bool:
    return _table_present(_vault_conn(vault), "cad_documents")


def _stored_blob_ext(vault: Any, sha256: str) -> dict:
    """读底座 `attachment_meta.ext`（读不到就是空 dict；坏 JSON 不静默当"没有"）。"""
    row = _vault_conn(vault).execute(
        "SELECT ext FROM attachment_meta WHERE sha256 = ?", (sha256,)
    ).fetchone()
    if row is None:
        return {}
    raw = row["ext"]
    try:
        value = json.loads(raw) if raw else {}
    except ValueError as exc:
        raise StoreCorrupt("attachment_meta.ext is not valid JSON for %s: %s" % (sha256, exc)) from exc
    return value if isinstance(value, dict) else {}


def _blob_needs_write(vault: Any, sha256: str, stable: dict) -> bool:
    """内容寻址去重的**幂等闸**：blob 在盘上且 `ext` 的稳定字段已一致 ⇒ 一个字节都不用改。

    为什么非要这道闸：`Vault.put_blob()` 每次都会 UPDATE `attachment_meta.ext`，而 `ext` 里带
    `updated_at` ⇒ 无脑重写会让"同一份文档 put 两次"产生两份不同的 `attachments.jsonl`，
    破坏 `.fcx` 的确定性（I1）。稳定字段（kind/doc_uuid/role）一致就跳过，写入才是真幂等。
    """
    if not vault.has_blob(sha256):
        return True
    stored = _stored_blob_ext(vault, sha256).get(BLOB_EXT_KEY)
    if not isinstance(stored, dict):
        return True
    return any(stored.get(key) != value for key, value in stable.items())


def _l1_blob_name(doc_uuid: str) -> str:
    return "%s.l1.json" % doc_uuid


# ── 索引：建表 / 读账 ────────────────────────────────────────────────────────────────

_INDEX_DDL = (
    """CREATE TABLE IF NOT EXISTS cad_documents (
        doc_uuid         TEXT PRIMARY KEY,
        schema_version   INTEGER NOT NULL DEFAULT -1,
        unit             TEXT NOT NULL DEFAULT '',
        standard_profile TEXT NOT NULL DEFAULT '',
        projection_angle TEXT,
        title_key        TEXT NOT NULL DEFAULT '',
        app_version      TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL DEFAULT '',
        updated_at       TEXT NOT NULL DEFAULT '',
        revision         INTEGER NOT NULL DEFAULT 1,
        l1_sha256        TEXT NOT NULL,
        l1_bytes         INTEGER NOT NULL DEFAULT 0,
        l1_name          TEXT NOT NULL DEFAULT '',
        l1_codec         TEXT NOT NULL DEFAULT '',
        hash_algo        TEXT NOT NULL DEFAULT '',
        index_version    INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS cad_document_blobs (
        doc_uuid TEXT NOT NULL,
        role     TEXT NOT NULL,
        sha256   TEXT NOT NULL,
        bytes    INTEGER NOT NULL DEFAULT 0,
        name     TEXT NOT NULL DEFAULT '',
        mime     TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (doc_uuid, role)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cad_document_blobs_sha ON cad_document_blobs(sha256)",
)

_DOC_COLUMNS = (
    "doc_uuid", "schema_version", "unit", "standard_profile", "projection_angle", "title_key",
    "app_version", "created_at", "updated_at", "revision", "l1_sha256", "l1_bytes", "l1_name",
    "l1_codec", "hash_algo", "index_version",
)


def ensure_index(vault: Any) -> dict[str, int]:
    """补齐本桥的两张索引表（幂等）；返回 `{"tables_created": n, "tables_present": m}`。

    建表走底座自己的 `_tx()`，只 `CREATE TABLE IF NOT EXISTS` 自己的表 —— 底座的表一个字段都不动。
    """
    created = 0
    with _tx(vault) as conn:
        for name in INDEX_TABLES:
            if not _table_present(conn, name):
                created += 1
        for ddl in _INDEX_DDL:
            conn.execute(ddl)
        present = sum(1 for name in INDEX_TABLES if _table_present(conn, name))
    return {"tables_created": created, "tables_present": present, "index_version": INDEX_SCHEMA_VERSION}


def _read_doc_row(vault: Any, doc_uuid: str) -> dict:
    """读一行索引；表不在 ⇒ `StoreIndexMissing`，行不在 ⇒ `DocumentNotFound`（都不是空返回）。"""
    conn = _vault_conn(vault)
    if not _index_present(vault):
        raise StoreIndexMissing(
            "index table cad_documents is absent in this vault; "
            "call open_store(create=True) or rebuild_index_from_blobs() first"
        )
    row = conn.execute(
        "SELECT %s FROM cad_documents WHERE doc_uuid = ?" % ", ".join(_DOC_COLUMNS), (doc_uuid,)
    ).fetchone()
    if row is None:
        raise DocumentNotFound("no CAD document indexed for doc_uuid=%s" % doc_uuid)
    return {key: row[key] for key in _DOC_COLUMNS}


def _originals_of(vault: Any, doc_uuid: str) -> list[dict]:
    """该文档的附加原件（`cad_document_blobs`，按 role 升序）。"""
    if not _index_present(vault):
        return []
    rows = _vault_conn(vault).execute(
        "SELECT doc_uuid, role, sha256, bytes, name, mime FROM cad_document_blobs "
        "WHERE doc_uuid = ? ORDER BY role", (doc_uuid,)
    ).fetchall()
    return [{key: row[key] for key in ("doc_uuid", "role", "sha256", "bytes", "name", "mime")}
            for row in rows]


def put_document(vault: Any, doc: Any, *, blobs: "Mapping[str, bytes] | None" = None) -> dict[str, int]:
    """把一份文档写进底座：L1 文本真源 + 随附原件按内容寻址进 `blobs/`，索引进新表。

    返回**纯整数计数**（`dict[str, int]`，三态口径：`0`=真为零）：
    `documents_new` / `documents_updated` / `documents_unchanged` / `l1_blobs_new` /
    `l1_blobs_dedup` / `originals_new` / `originals_dedup` / `index_rows` / `revision`。

    **幂等**：同一份 L1 文本重复 put ⇒ `documents_unchanged=1`，且**底座一个字节都不变**
    （`_blob_needs_write()` 闸门：blob 已在盘上、`ext` 稳定字段一致就不再写 `attachment_meta`）。
    内容变了（同一 `doc_uuid` 新 L1 文本）⇒ `revision` 递增，旧 blob **不删**（内容寻址 = 不覆盖历史）。
    """
    if blobs is not None and not isinstance(blobs, Mapping):
        raise StoreError("blobs must be a Mapping[str, bytes]; got %s" % type(blobs).__name__)

    doc_uuid = _doc_uuid(doc)
    codec_name, to_l1 = _l1_encoder()
    text = to_l1(doc)
    if not isinstance(text, str):
        raise StoreError("L1 encoder %s returned %s, expected str" % (codec_name, type(text).__name__))
    payload = text.encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    if HASH_ALGO != "sha256":  # pragma: no cover - 常量被改时才可能命中，防"名实不符"
        raise StoreError("HASH_ALGO=%r but Vault.put_blob addresses blobs by sha256" % HASH_ALGO)

    fields = _doc_row_fields(doc)
    l1_name = _l1_blob_name(doc_uuid)
    originals: list[tuple[str, bytes]] = []
    for key, data in (blobs or {}).items():
        if not isinstance(key, str) or not key:
            raise StoreError("blobs keys must be non-empty file names; got %r" % (key,))
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise StoreError("blobs[%r] must be bytes; got %s" % (key, type(data).__name__))
        originals.append((key, bytes(data)))

    ensure_index(vault)
    now = _now_iso()
    l1_new = originals_new = originals_dedup = index_rows = 0
    documents_new = documents_updated = documents_unchanged = 0
    l1_dedup = 0

    with _tx(vault) as conn:
        prev = conn.execute(
            "SELECT l1_sha256, revision FROM cad_documents WHERE doc_uuid = ?", (doc_uuid,)
        ).fetchone()
        if prev is None:
            revision, documents_new = 1, 1
        elif prev["l1_sha256"] == sha:
            revision, documents_unchanged = int(prev["revision"]), 1
        else:
            revision, documents_updated = int(prev["revision"]) + 1, 1

        # ① L1 文本真源（原件本体）——内容寻址，天然去重
        l1_stable = {"kind": L1_ROLE, "doc_uuid": doc_uuid}
        if _blob_needs_write(vault, sha, l1_stable):
            l1_ext = {
                BLOB_EXT_KEY: dict(
                    l1_stable,
                    schema_version=fields["schema_version"],
                    unit=fields["unit"],
                    standard_profile=fields["standard_profile"],
                    projection_angle=fields["projection_angle"],   # None = NA，JSON null 原样带走
                    title_key=fields["title_key"],
                    app_version=fields["app_version"],
                    created_at=fields["created_at"],
                    updated_at=now,
                    revision=revision,
                    l1_codec=codec_name,
                    hash_algo=HASH_ALGO,
                    index_version=INDEX_SCHEMA_VERSION,
                )
            }
            vault.put_blob(payload, mime=L1_MIME, name=l1_name, ext=l1_ext)
            l1_new = 1
        else:
            l1_dedup = 1

        # ② 随附原件（DXF/STEP/... 字节）——同一把内容寻址尺子
        for key, data in originals:
            data_sha = hashlib.sha256(data).hexdigest()
            stable = {"kind": "original", "doc_uuid": doc_uuid, "role": key}
            if _blob_needs_write(vault, data_sha, stable):
                vault.put_blob(data, mime=_mime_hint(key), name=key,
                               ext={BLOB_EXT_KEY: dict(stable, hash_algo=HASH_ALGO, revision=revision)})
                originals_new += 1
            else:
                originals_dedup += 1
            conn.execute(
                "INSERT INTO cad_document_blobs(doc_uuid, role, sha256, bytes, name, mime) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(doc_uuid, role) DO UPDATE SET "
                "sha256 = excluded.sha256, bytes = excluded.bytes, name = excluded.name, "
                "mime = excluded.mime",
                (doc_uuid, key, data_sha, len(data), key, _mime_hint(key)),
            )
            index_rows += 1

        # ③ 文档索引行（L1 逐字节没变就**不重写**，保证"重复 put ⇒ 库逐字节不变"）
        if not documents_unchanged:
            conn.execute(
                "INSERT INTO cad_documents(doc_uuid, schema_version, unit, standard_profile, "
                "projection_angle, title_key, app_version, created_at, updated_at, revision, "
                "l1_sha256, l1_bytes, l1_name, l1_codec, hash_algo, index_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(doc_uuid) DO UPDATE SET schema_version = excluded.schema_version, "
                "unit = excluded.unit, standard_profile = excluded.standard_profile, "
                "projection_angle = excluded.projection_angle, title_key = excluded.title_key, "
                "app_version = excluded.app_version, created_at = excluded.created_at, "
                "updated_at = excluded.updated_at, revision = excluded.revision, "
                "l1_sha256 = excluded.l1_sha256, l1_bytes = excluded.l1_bytes, "
                "l1_name = excluded.l1_name, l1_codec = excluded.l1_codec, "
                "hash_algo = excluded.hash_algo, index_version = excluded.index_version",
                (doc_uuid, fields["schema_version"], fields["unit"], fields["standard_profile"],
                 fields["projection_angle"], fields["title_key"], fields["app_version"],
                 fields["created_at"], now, revision, sha, len(payload), l1_name, codec_name,
                 HASH_ALGO, INDEX_SCHEMA_VERSION),
            )
            index_rows += 1

    return {
        "documents_new": documents_new,
        "documents_updated": documents_updated,
        "documents_unchanged": documents_unchanged,
        "l1_blobs_new": l1_new,
        "l1_blobs_dedup": l1_dedup,
        "originals_new": originals_new,
        "originals_dedup": originals_dedup,
        "index_rows": index_rows,
        "revision": revision,
    }


def _read_verified_l1(vault: Any, row: dict) -> bytes:
    """按索引行读 L1 blob 并**逐字节校验** sha256（缺失/不符都报 `StoreCorrupt`，不给旧值/空值）。"""
    sha = row["l1_sha256"]
    if not vault.has_blob(sha):
        raise StoreCorrupt("indexed L1 blob is missing on disk: doc_uuid=%s sha256=%s"
                           % (row["doc_uuid"], sha))
    data = vault.get_blob(sha)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha:
        raise StoreCorrupt("L1 blob content does not match its address: doc_uuid=%s expected=%s actual=%s"
                           % (row["doc_uuid"], sha, actual))
    return data


def get_l1_text(vault: Any, doc_uuid: str) -> str:
    """取该文档的 **L1 文本真源原文**（读前校验 sha256）；索引/内容有问题就抛错。"""
    row = _read_doc_row(vault, doc_uuid)
    return _read_verified_l1(vault, row).decode("utf-8")


def get_document(vault: Any, doc_uuid: str) -> Any:
    """读回 `Document`（L1 文本 → A5 解码器；A5 缺席时降级为纯 dict，见模块 docstring 登记 2）。"""
    text = get_l1_text(vault, doc_uuid)
    _codec_name, from_l1 = _l1_decoder()
    return from_l1(text)


def iter_documents(vault: Any) -> Iterator[dict]:
    """按 `doc_uuid` 升序遍历索引（只读；每份文档附 `blob_present` 与 `na_fields`）。

    索引表不在 ⇒ `StoreIndexMissing`，**不返回空清单**（空清单会把"未采"伪装成"0 份文档"）。
    """
    if not _index_present(vault):
        raise StoreIndexMissing("index table cad_documents is absent; nothing can be enumerated")
    rows = _vault_conn(vault).execute(
        "SELECT %s FROM cad_documents ORDER BY doc_uuid" % ", ".join(_DOC_COLUMNS)
    ).fetchall()
    for row in rows:
        rec = {key: row[key] for key in _DOC_COLUMNS}
        rec["na_fields"] = sorted(key for key in _NA_FIELDS if not rec.get(key))
        rec["blob_present"] = 1 if vault.has_blob(rec["l1_sha256"]) else 0
        rec["originals"] = len(_originals_of(vault, rec["doc_uuid"]))
        yield rec


def document_index_stats(vault: Any) -> dict[str, int]:
    """索引只读账（全是整数）。**索引表不在 ⇒ 每个计数字段给 `-1`（未采），不是 0。**

    两态的**键集合完全一致**（缺席态给 `-1`），调用方不必为"表在不在"写两套取键逻辑。
    `na_rows` = 至少有一个字段为空（NA）的文档行数；与"未采"的 `-1` 分开报。
    """
    if not _index_present(vault):
        keys = ("index_present", "index_version", "documents", "documents_with_blob",
                "documents_missing_blob", "originals", "l1_bytes", "revisions_total", "na_rows")
        return dict({key: STATE_UNCAPTURED for key in keys}, index_present=0)
    conn = _vault_conn(vault)
    documents = int(conn.execute("SELECT COUNT(*) FROM cad_documents").fetchone()[0])
    originals = int(conn.execute("SELECT COUNT(*) FROM cad_document_blobs").fetchone()[0])
    l1_bytes = int(conn.execute("SELECT COALESCE(SUM(l1_bytes), 0) FROM cad_documents").fetchone()[0])
    revisions = int(conn.execute("SELECT COALESCE(SUM(revision), 0) FROM cad_documents").fetchone()[0])
    present = 0
    na_rows = 0
    for rec in iter_documents(vault):
        present += rec["blob_present"]
        na_rows += 1 if rec["na_fields"] else 0
    return {
        "index_present": 1,
        "index_version": INDEX_SCHEMA_VERSION,
        "documents": documents,
        "documents_with_blob": present,
        "documents_missing_blob": documents - present,
        "originals": originals,
        "l1_bytes": l1_bytes,
        "revisions_total": revisions,
        "na_rows": na_rows,
    }


def rebuild_index_from_blobs(vault: Any) -> dict[str, int]:
    """从底座现有的 blob 元数据**重建 CAD 索引**（`.fcx` 导入到新库之后的收口动作）。

    原理：索引随附件级 `ext` 走（模块 docstring 设计点 4）⇒ `fcx.import_fcx()` 把归档灌进新库后，
    本函数扫描 `attachment_meta`，按 `ext.cadcore.kind` 复原 `cad_documents` / `cad_document_blobs`，
    并对每个 blob **现算 sha256** 与元数据核对（不符只计数、不写假行）。返回纯整数计数：

    `scanned` / `l1_found` / `rebuilt` / `originals_linked` / `skipped_other` /
    `missing_blob` / `digest_mismatch` / `docs_in_index`。
    """
    ensure_index(vault)
    docs: dict[str, dict] = {}
    originals: list[tuple[str, str, str, int, str, str]] = []
    scanned = skipped_other = missing_blob = digest_mismatch = 0
    for att in vault.iter_attachments():
        scanned += 1
        info = _stored_blob_ext(vault, att["sha256"]).get(BLOB_EXT_KEY)
        if not isinstance(info, dict) or info.get("kind") not in (L1_ROLE, "original"):
            skipped_other += 1
            continue
        sha = att["sha256"]
        if not vault.has_blob(sha):
            missing_blob += 1
            continue
        if hashlib.sha256(vault.get_blob(sha)).hexdigest() != sha:
            digest_mismatch += 1
            continue
        doc_uuid = _as_text(info.get("doc_uuid"))
        if not doc_uuid:
            skipped_other += 1
            continue
        if info.get("kind") == "original":
            originals.append((doc_uuid, _as_text(info.get("role")) or att["name"],
                              sha, int(att["bytes"]), att["name"], att["mime"]))
            continue
        docs[doc_uuid] = {
            "doc_uuid": doc_uuid,
            "schema_version": _as_int(info.get("schema_version")),
            "unit": _as_text(info.get("unit")),
            "standard_profile": _as_text(info.get("standard_profile")),
            "projection_angle": info.get("projection_angle") if isinstance(info.get("projection_angle"), str) else None,
            "title_key": _as_text(info.get("title_key")),
            "app_version": _as_text(info.get("app_version")),
            "created_at": _as_text(info.get("created_at")),
            "updated_at": _as_text(info.get("updated_at")),
            "revision": _as_int(info.get("revision")),
            "l1_sha256": sha,
            "l1_bytes": int(att["bytes"]),
            "l1_name": att["name"],
            "l1_codec": _as_text(info.get("l1_codec")) or FALLBACK_L1_CODEC,
            "hash_algo": _as_text(info.get("hash_algo")) or HASH_ALGO,
            "index_version": _as_int(info.get("index_version")),
        }
    rebuilt = linked = 0
    with _tx(vault) as conn:
        for doc_uuid, rec in sorted(docs.items()):
            conn.execute(
                "INSERT INTO cad_documents(doc_uuid, schema_version, unit, standard_profile, "
                "projection_angle, title_key, app_version, created_at, updated_at, revision, "
                "l1_sha256, l1_bytes, l1_name, l1_codec, hash_algo, index_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(doc_uuid) DO UPDATE SET schema_version = excluded.schema_version, "
                "unit = excluded.unit, standard_profile = excluded.standard_profile, "
                "projection_angle = excluded.projection_angle, title_key = excluded.title_key, "
                "app_version = excluded.app_version, created_at = excluded.created_at, "
                "updated_at = excluded.updated_at, revision = excluded.revision, "
                "l1_sha256 = excluded.l1_sha256, l1_bytes = excluded.l1_bytes, "
                "l1_name = excluded.l1_name, l1_codec = excluded.l1_codec, "
                "hash_algo = excluded.hash_algo, index_version = excluded.index_version",
                tuple(rec[key] for key in _DOC_COLUMNS),
            )
            rebuilt += 1
        for doc_uuid, role, sha, size, name, mime in sorted(originals):
            if doc_uuid not in docs and not _doc_row_exists(conn, doc_uuid):
                skipped_other += 1
                continue
            conn.execute(
                "INSERT INTO cad_document_blobs(doc_uuid, role, sha256, bytes, name, mime) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(doc_uuid, role) DO UPDATE SET "
                "sha256 = excluded.sha256, bytes = excluded.bytes, name = excluded.name, "
                "mime = excluded.mime",
                (doc_uuid, role, sha, size, name, mime),
            )
            linked += 1
        docs_in_index = int(conn.execute("SELECT COUNT(*) FROM cad_documents").fetchone()[0])
    return {
        "scanned": scanned,
        "l1_found": len(docs),
        "rebuilt": rebuilt,
        "originals_linked": linked,
        "skipped_other": skipped_other,
        "missing_blob": missing_blob,
        "digest_mismatch": digest_mismatch,
        "docs_in_index": docs_in_index,
    }


def _doc_row_exists(conn: sqlite3.Connection, doc_uuid: str) -> bool:
    return conn.execute("SELECT 1 FROM cad_documents WHERE doc_uuid = ?", (doc_uuid,)).fetchone() is not None


def _indexed_missing_blobs(vault: Any) -> list[str]:
    """索引里指向但盘上找不到的 L1 blob（导出前预检用；索引表不在 ⇒ 空清单，见下）。"""
    if not _index_present(vault):
        # 索引表不在 ⇒ 这个库里**按构造**没有任何 CAD 索引行，没有"漏检"可言（不是静默 0）。
        return []
    missing = []
    for row in _vault_conn(vault).execute("SELECT doc_uuid, l1_sha256 FROM cad_documents").fetchall():
        if not vault.has_blob(row["l1_sha256"]):
            missing.append("%s:%s" % (row["doc_uuid"], row["l1_sha256"]))
    return sorted(missing)


def export_package(vault: Any, out_path: str) -> str:
    """导出 `.fcx` 包（复用 `freechat_vault.fcx.export_fcx`），返回**绝对路径**。

    两道自检，缺一不返回成功：①索引里每份 L1 的 blob 必须真在盘上（缺 ⇒ `StoreCorrupt`，
    不许导出"引用不到的归档"）；②产出的归档必须能通过底座自己的全套校验（`FcxArchive` 构造 =
    成员表 + 逐成员 sha256 + 计数 + zip-slip 全查）—— **导出后读不回来的包不算导出成功**。
    代价登记：②会把归档整体再读一遍（I/O 约 ×2）。MVP 取"正确优先"；真要省这一步就得给
    冻结签名加可选入参（A15 只有 `(vault, out_path)`）⇒ 不在本席权限内，故不做。
    """
    fcx = _load_fcx_module()
    out = os.path.abspath(os.fspath(out_path))
    missing = _indexed_missing_blobs(vault)
    if missing:
        raise StoreCorrupt("refusing to export: %d indexed L1 blob(s) missing on disk (%s)"
                           % (len(missing), ", ".join(missing[:3])))
    fcx.export_fcx(vault, out)
    with fcx.FcxArchive(out) as arch:      # 读不回来 = 不合格（不许假装成功）
        manifest = arch.manifest
    if not isinstance(manifest, dict) or manifest.get("format") != fcx.FORMAT_NAME:
        raise StoreCorrupt("exported archive failed self-validation: %s" % out)
    return out


def open_store(root: str, *, create: bool = True) -> Any:
    """打开（或创建）vault 底座，返回**底座原生的** `Vault` 实例；`create=True` 时补齐索引表。

    `create=False` ⇒ 库必须已存在（缺则由底座报 `VaultError`），且本函数**不写库**
    （索引表留给第一次写操作 `put_document` / `rebuild_index_from_blobs` 自己建）。
    """
    vault_module = _load_vault_module()
    vault = vault_module.Vault(root, create=create)
    if not create:
        return vault
    try:
        ensure_index(vault)
    except BaseException:
        vault.close()
        raise
    return vault


def store_facts(vault: Any, *, doc_uuid: "str | None" = None) -> dict[str, Any]:
    """判定行取证用只读快照：底座身份 + 索引账 + 可选单文档账（全部现读，不缓存）。"""
    stats = document_index_stats(vault)
    facts: dict[str, Any] = {
        "vault_root": getattr(vault, "root", ""),
        "db_filename": os.path.basename(str(getattr(vault, "db_path", ""))),
        "index_tables": list(INDEX_TABLES),
        "hash_algo": HASH_ALGO,
        "index_version": INDEX_SCHEMA_VERSION,
        "l1_codec_source": _l1_encoder()[0],
        "index": stats,
    }
    if doc_uuid is not None:
        row = _read_doc_row(vault, doc_uuid)
        row["na_fields"] = sorted(key for key in _NA_FIELDS if not row.get(key))
        row["blob_present"] = 1 if vault.has_blob(row["l1_sha256"]) else 0
        row["originals"] = _originals_of(vault, doc_uuid)
        facts["document"] = row
    return facts
