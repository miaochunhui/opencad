"""freechat_vault.store —— Vault 数据底座（U1）：SQLite(WAL) + FTS5 + 内容寻址 blob + op-log。

冻结接口：`oss-alternatives/docs/design/impl-interfaces.md` §2（签名/字段名/文件名逐字一致）。
格式规范：`oss-alternatives/docs/design/fcx-format.md` §3（记录字段与排序键）。
语言约束：Python 3.10，**只用标准库**（sqlite3 / hashlib / json / os / re / tempfile）。

设计要点（改代码前先读）
------------------------
* **事务**：连接以 `isolation_level=None`（autocommit）打开，写操作一律走显式
  `BEGIN IMMEDIATE`；任一写方法失败即 `ROLLBACK`，不留半成品（fcx-format I5）。
  `_tx()` 支持嵌套：最外层 BEGIN/COMMIT，内层用 SAVEPOINT。
* **FTS5**：`messages_fts` 是**独立** FTS5 虚表（不用 external content —— 因为要写入"按字切开"
  的中文检索文档）。FTS 行号 = `messages` 行号，更新走 rowid 删除 + 重插（O(1)，不做全表扫）。
* **中文按字切分**：裸 unicode61 **不会**把「你好世界」切成字（本机 sqlite 3.37 实测
  `MATCH '世界'` 零命中），而冻结文档要求「中文按字切分可用」⇒ 入库侧与查询侧都调用
  `_fts_document()` 给 CJK 逐字插空格，两侧同一变换，检索才对称。
* **未知顶层键**：fcx-format §3 说「未知字段一律放进 ext」。本实现不擅自改写用户数据，
  而是存进内部 `extra` 列，读出时原样还原（保证 round-trip 不丢字段）。
* **content_digest**：只覆盖 contacts/chats/messages/reactions/attachment_meta
  （含 blob 的 sha256 集合，**不含 blob 本体**），**不含 ops** —— ops 是变更历史；
  若把它算进摘要，同一份内容重复导入（追加 op）就会改摘要，破坏 fcx-format I2「内容哈希不变」。
* **附件 ext 缺口**：冻结签名 `put_blob(data, *, mime, name)` 没有传 `ext` 的入口，
  所以本实现里 `attachment_meta.ext` 恒为 `{}`（U2 导出时该字段会丢 —— 本 docstring 即为登记，
  Lead 可收进 `docs/design/impl-notes-U1.md` 后定夺是否给 `put_blob` 加可选 `ext`）。

冻结文档里欠定/自相矛盾的地方（一律**按文档实现**，只在此登记，不擅自改接口）
----------------------------------------------------------------------------
1. fcx-format §5.3 写「去重键：chat/contact/message/reaction = `id`」，但 §3.4 的 reaction 记录
   **没有 `id` 字段** ⇒ 本实现对 reaction 用 §3.4 的四元组
   `(message_id, contact_id, emoji, created_at)` 当主键（它同时就是 §3.4 的排序键）；
   若调用方硬塞了 `id`，原样存进 `extra`，不丢。
2. fcx-format §3.1（chats）与 §3.2（contacts）**没给排序键** ⇒ 本实现按 `id` 升序。
3. 排序键 `(chat_id, sent_at, id)` 是**文本**比较（存的就是 ISO-8601 串）。若同一库里混用
   不同时区偏移，文本序 ≠ 真实时间序 —— 要保时间序，导出侧需先归一化到同一偏移。
4. impl-interfaces §2 的表清单没有"存未知顶层键"的位置 ⇒ 本实现额外加了内部 `extra` 列
   （只是存储实现细节，不动任何冻结的记录字段）。
5. `search()` 的返回值：冻结文档只写「结果按 rank」，未说返回体含 rank ⇒ 本实现返回**纯消息
   记录**（与 `iter_messages` 同构，可直接回灌 upsert），rank 只用于排序，不塞进记录。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator

__all__ = [
    "DB_FILENAME",
    "BLOBS_DIRNAME",
    "SCHEMA_VERSION",
    "VaultError",
    "SchemaVersionError",
    "FutureSchemaError",
    "Vault",
]

DB_FILENAME = "vault.sqlite3"
BLOBS_DIRNAME = "blobs"
SCHEMA_VERSION = 1

# blob 标识必须是 64 位小写 hex（sha256）；这同时是路径安全闸（拒绝 `..`/绝对路径，见 fcx-format §6）。
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")

# CJK 区段：unicode61 会把整段汉字/假名/谚文当成**一个** token，故逐字切开。
_CJK_RANGES = (
    (0x3040, 0x30FF),    # 平假名 + 片假名
    (0x3400, 0x4DBF),    # CJK 扩展 A
    (0x4E00, 0x9FFF),    # CJK 基本区
    (0xF900, 0xFAFF),    # CJK 兼容表意
    (0xAC00, 0xD7AF),    # 谚文音节
    (0x20000, 0x2FA1F),  # CJK 扩展 B 及以后
)

# 记录字段（fcx-format §3）。不在这些键里的顶层键进 `extra`，读出时原样还原。
_CONTACT_KEYS = frozenset({"id", "display_name", "aliases", "ext"})
_CHAT_KEYS = frozenset({"id", "kind", "title", "created_at", "updated_at", "ext"})
_MESSAGE_KEYS = frozenset(
    {
        "id",
        "chat_id",
        "sender_id",
        "kind",
        "text",
        "sent_at",
        "edited_at",
        "deleted_at",
        "reply_to",
        "attachments",
        "ext",
    }
)
_REACTION_KEYS = frozenset({"message_id", "contact_id", "emoji", "created_at", "ext"})

# 排序键（fcx-format §3.3/§3.4/§3.5）。§3.1/§3.2 没给 chats/contacts 的排序键，
# 本实现按 `id` 升序（与"导出确定性"目标一致；欠定项已登记在本文件 docstring 末尾）。
_ORDER_CONTACTS = "id"
_ORDER_CHATS = "id"
_ORDER_MESSAGES = "chat_id, sent_at, id"
_ORDER_REACTIONS = "message_id, contact_id, emoji, created_at"
_ORDER_ATTACHMENTS = "sha256"

_SCHEMA_DDL = (
    """CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS contacts (
        id           TEXT PRIMARY KEY,
        display_name TEXT NOT NULL DEFAULT '',
        aliases      TEXT NOT NULL DEFAULT '[]',
        ext          TEXT NOT NULL DEFAULT '{}',
        extra        TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS chats (
        id         TEXT PRIMARY KEY,
        kind       TEXT NOT NULL DEFAULT 'group',
        title      TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT '',
        ext        TEXT NOT NULL DEFAULT '{}',
        extra      TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id          TEXT PRIMARY KEY,
        chat_id     TEXT NOT NULL DEFAULT '',
        sender_id   TEXT NOT NULL DEFAULT '',
        kind        TEXT NOT NULL DEFAULT 'text',
        "text"      TEXT NOT NULL DEFAULT '',
        sent_at     TEXT NOT NULL DEFAULT '',
        edited_at   TEXT,
        deleted_at  TEXT,
        reply_to    TEXT,
        attachments TEXT NOT NULL DEFAULT '[]',
        ext         TEXT NOT NULL DEFAULT '{}',
        extra       TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_messages_order ON messages(chat_id, sent_at, id)",
    """CREATE TABLE IF NOT EXISTS reactions (
        message_id TEXT NOT NULL,
        contact_id TEXT NOT NULL DEFAULT '',
        emoji      TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        ext        TEXT NOT NULL DEFAULT '{}',
        extra      TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (message_id, contact_id, emoji, created_at)
    )""",
    """CREATE TABLE IF NOT EXISTS attachment_meta (
        sha256 TEXT PRIMARY KEY,
        bytes  INTEGER NOT NULL DEFAULT 0,
        mime   TEXT NOT NULL DEFAULT '',
        name   TEXT NOT NULL DEFAULT '',
        ext    TEXT NOT NULL DEFAULT '{}',
        extra  TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        message_id UNINDEXED,
        "text",
        chat_id,
        tokenize = 'unicode61'
    )""",
    """CREATE TABLE IF NOT EXISTS ops (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        rec TEXT NOT NULL
    )""",
)


class VaultError(Exception):
    """Vault 层的通用错误（非法记录、blob 缺失、库已关闭等）。"""


class SchemaVersionError(VaultError):
    """库的 schema_version 低于本程序支持的版本（需要迁移）。"""


class FutureSchemaError(VaultError):
    """库的 schema_version 高于本程序支持的版本（请升级 freechat，不写库）。"""


# --------------------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------------------


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    for low, high in _CJK_RANGES:
        if low <= code <= high:
            return True
    return False


def _fts_document(value: str) -> str:
    """把 CJK 连写切成单字后再交给 unicode61；非 CJK 文本原样返回。

    入库与查询必须用**同一个**函数，否则两侧分词不对称，中文检索会假阴性。
    """
    if not value:
        return ""
    if not any(_is_cjk_char(ch) for ch in value):
        return value
    return "".join(" %s " % ch if _is_cjk_char(ch) else ch for ch in value)


def _fts_fallback_query(text: str) -> str | None:
    """把用户输入拆成"逐词引号短语 + AND"，用于原始 FTS 语法不合法时兜底。"""
    terms = []
    for raw in text.split():
        term = raw.strip().strip('"')
        if not term or term.upper() in ("AND", "OR", "NOT", "NEAR"):
            continue
        phrase = _fts_document(term).replace('"', '""')
        terms.append('"%s"' % phrase)
    if not terms:
        return None
    return " AND ".join(terms)


def _canonical_json(value: Any) -> str:
    """规范化 JSON：键序固定、紧凑分隔符、不转义非 ASCII（fcx-format §3 同款口径）。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _json_dump(value: Any, name: str) -> str:
    try:
        return _canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise VaultError("字段 %s 不是可 JSON 序列化的值：%s" % (name, exc)) from exc


def _json_load(text: Any, name: str, factory: type) -> Any:
    if text is None or text == "":
        return factory()
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise VaultError("库内字段 %s 不是合法 JSON：%s" % (name, exc)) from exc


def _as_str(value: Any, name: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise VaultError("字段 %s 必须是字符串（得到 %s）" % (name, type(value).__name__))
    if required and not value:
        raise VaultError("字段 %s 不能为空" % name)
    return value


def _as_opt_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise VaultError("字段 %s 必须是字符串或 null（得到 %s）" % (name, type(value).__name__))
    return value


def _as_list(value: Any, name: str) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    raise VaultError("字段 %s 必须是数组（得到 %s）" % (name, type(value).__name__))


def _as_dict(value: Any, name: str) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise VaultError("字段 %s 必须是 JSON 对象（得到 %s）" % (name, type(value).__name__))


def _as_bytes(value: Any, name: str = "data") -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise VaultError("字段 %s 必须是 bytes（得到 %s）" % (name, type(value).__name__))


def _split_rec(rec: Any, known: frozenset) -> tuple[dict, dict]:
    """拆成 (规范字段, 未知顶层键)。未知键进 `extra` 列原样保存，读出时还原。"""
    if not isinstance(rec, dict):
        raise VaultError("记录必须是 dict（得到 %s）" % type(rec).__name__)
    fields: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in rec.items():
        if key in known:
            fields[key] = value
        else:
            extra[key] = value
    return fields, extra


def _merge_extra(rec: dict, raw: Any, name: str) -> dict:
    extra = _json_load(raw, name, dict)
    if not isinstance(extra, dict):
        raise VaultError("库内字段 %s 必须是 JSON 对象" % name)
    for key, value in extra.items():
        rec.setdefault(key, value)
    return rec


def _message_from_row(row: sqlite3.Row) -> dict:
    rec: dict[str, Any] = {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "sender_id": row["sender_id"],
        "kind": row["kind"],
        "text": row["text"],
        "sent_at": row["sent_at"],
    }
    # 可选键：缺省即不出现，**不写 null**（fcx-format §3.3）
    for key in ("edited_at", "deleted_at", "reply_to"):
        value = row[key]
        if value is not None:
            rec[key] = value
    rec["attachments"] = _json_load(row["attachments"], "messages.attachments", list)
    rec["ext"] = _json_load(row["ext"], "messages.ext", dict)
    return _merge_extra(rec, row["extra"], "messages.extra")


# --------------------------------------------------------------------------------------
# Vault
# --------------------------------------------------------------------------------------


class Vault:
    """一个 vault 目录 = `vault.sqlite3`（WAL）+ `blobs/<sha[:2]>/<sha256>`。

    `with Vault(path) as v: ...` 可用；`close()` 幂等；关闭后再调用任何方法抛 `VaultError`。
    """

    def __init__(self, root: "str | os.PathLike", *, create: bool = True) -> None:
        self.root = os.path.abspath(os.fspath(root))
        self.db_path = os.path.join(self.root, DB_FILENAME)
        self.blobs_dir = os.path.join(self.root, BLOBS_DIRNAME)
        self._conn: sqlite3.Connection | None = None
        self._tx_depth = 0

        if create:
            os.makedirs(self.root, exist_ok=True)
            os.makedirs(self.blobs_dir, exist_ok=True)
        elif not os.path.isfile(self.db_path):
            raise VaultError("Vault 不存在：%s（create=False）" % self.db_path)

        conn = sqlite3.connect(self.db_path, isolation_level=None)  # autocommit + 显式 BEGIN
        conn.row_factory = sqlite3.Row
        self._conn = conn
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema(create=create)
        except BaseException:
            self._conn = None
            try:
                conn.close()
            except sqlite3.Error:
                pass
            raise

    # —— 生命周期 ——

    def _init_schema(self, *, create: bool) -> None:
        conn = self._conn
        assert conn is not None
        has_meta = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
            ).fetchone()
            is not None
        )
        if not has_meta:
            if not create:
                raise VaultError("不是 freechat Vault（缺少 meta 表）：%s" % self.db_path)
            with self._tx():
                for ddl in _SCHEMA_DDL:
                    conn.execute(ddl)
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            return

        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            raise SchemaVersionError("Vault 的 meta 表缺少 schema_version：%s" % self.db_path)
        raw = row[0]
        try:
            version = int(str(raw).strip())
        except (TypeError, ValueError):
            raise SchemaVersionError("schema_version 非法：%r" % (raw,)) from None
        if version > SCHEMA_VERSION:
            raise FutureSchemaError(
                "Vault schema_version=%d 高于本程序支持的 %d：请升级 freechat（不写库）"
                % (version, SCHEMA_VERSION)
            )
        if version < SCHEMA_VERSION:
            raise SchemaVersionError(
                "Vault schema_version=%d 低于本程序支持的 %d：需要迁移"
                % (version, SCHEMA_VERSION)
            )
        if create:
            with self._tx():
                for ddl in _SCHEMA_DDL:
                    conn.execute(ddl)

    def close(self) -> None:
        """关闭连接，幂等。"""
        conn, self._conn = self._conn, None
        self._tx_depth = 0
        if conn is not None:
            conn.close()

    def __enter__(self) -> "Vault":
        self._conn_or_raise()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def __repr__(self) -> str:  # pragma: no cover - 只为调试
        state = "closed" if self._conn is None else "open"
        return "<Vault %s %s>" % (self.root, state)

    def _conn_or_raise(self) -> sqlite3.Connection:
        conn = self._conn
        if conn is None:
            raise VaultError("Vault 已关闭")
        return conn

    @contextmanager
    def _tx(self):
        """显式事务：最外层 BEGIN IMMEDIATE / COMMIT / ROLLBACK，内层 SAVEPOINT。"""
        conn = self._conn_or_raise()
        depth = self._tx_depth
        if depth == 0:
            conn.execute("BEGIN IMMEDIATE")
            self._tx_depth = 1
            try:
                yield conn
            except BaseException:
                self._tx_depth = 0
                conn.execute("ROLLBACK")
                raise
            try:
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                self._tx_depth = 0
        else:
            name = "sp_%d" % depth
            conn.execute("SAVEPOINT %s" % name)
            self._tx_depth = depth + 1
            try:
                yield conn
            except BaseException:
                self._tx_depth = depth
                conn.execute("ROLLBACK TO %s" % name)
                conn.execute("RELEASE %s" % name)
                raise
            else:
                self._tx_depth = depth
                conn.execute("RELEASE %s" % name)

    def _row_exists(self, table: str, where: str, params: tuple) -> bool:
        conn = self._conn_or_raise()
        sql = "SELECT 1 FROM %s WHERE %s LIMIT 1" % (table, where)
        return conn.execute(sql, params).fetchone() is not None

    # —— 写入：True = 新插入，False = 覆盖了已存在记录 ——

    def upsert_contact(self, rec: dict) -> bool:
        fields, extra = _split_rec(rec, _CONTACT_KEYS)
        cid = _as_str(fields.get("id"), "id", required=True)
        display_name = _as_str(fields.get("display_name"), "display_name")
        aliases = _as_list(fields.get("aliases"), "aliases")
        for index, alias in enumerate(aliases):
            if not isinstance(alias, str):
                raise VaultError("aliases[%d] 必须是字符串" % index)
        ext = _as_dict(fields.get("ext"), "ext")
        params = (
            cid,
            display_name,
            _json_dump(aliases, "aliases"),
            _json_dump(ext, "ext"),
            _json_dump(extra, "extra"),
        )
        with self._tx() as conn:
            inserted = not self._row_exists("contacts", "id = ?", (cid,))
            conn.execute(
                "INSERT INTO contacts(id, display_name, aliases, ext, extra) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name, "
                "aliases = excluded.aliases, ext = excluded.ext, extra = excluded.extra",
                params,
            )
            return inserted

    def upsert_chat(self, rec: dict) -> bool:
        fields, extra = _split_rec(rec, _CHAT_KEYS)
        cid = _as_str(fields.get("id"), "id", required=True)
        kind = _as_str(fields.get("kind"), "kind") or "group"
        title = _as_str(fields.get("title"), "title")
        created_at = _as_str(fields.get("created_at"), "created_at")
        updated_at = _as_str(fields.get("updated_at"), "updated_at")
        ext = _as_dict(fields.get("ext"), "ext")
        params = (
            cid,
            kind,
            title,
            created_at,
            updated_at,
            _json_dump(ext, "ext"),
            _json_dump(extra, "extra"),
        )
        with self._tx() as conn:
            inserted = not self._row_exists("chats", "id = ?", (cid,))
            conn.execute(
                "INSERT INTO chats(id, kind, title, created_at, updated_at, ext, extra) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, title = excluded.title, "
                "created_at = excluded.created_at, updated_at = excluded.updated_at, "
                "ext = excluded.ext, extra = excluded.extra",
                params,
            )
            return inserted

    def upsert_message(self, rec: dict) -> bool:
        fields, extra = _split_rec(rec, _MESSAGE_KEYS)
        mid = _as_str(fields.get("id"), "id", required=True)
        chat_id = _as_str(fields.get("chat_id"), "chat_id")
        sender_id = _as_str(fields.get("sender_id"), "sender_id")
        kind = _as_str(fields.get("kind"), "kind") or "text"
        text = _as_str(fields.get("text"), "text")
        sent_at = _as_str(fields.get("sent_at"), "sent_at")
        edited_at = _as_opt_str(fields.get("edited_at"), "edited_at")
        deleted_at = _as_opt_str(fields.get("deleted_at"), "deleted_at")
        reply_to = _as_opt_str(fields.get("reply_to"), "reply_to")
        attachments = _as_list(fields.get("attachments"), "attachments")
        for index, item in enumerate(attachments):
            if not isinstance(item, dict):
                raise VaultError("attachments[%d] 必须是对象" % index)
        ext = _as_dict(fields.get("ext"), "ext")
        params = (
            mid,
            chat_id,
            sender_id,
            kind,
            text,
            sent_at,
            edited_at,
            deleted_at,
            reply_to,
            _json_dump(attachments, "attachments"),
            _json_dump(ext, "ext"),
            _json_dump(extra, "extra"),
        )
        with self._tx() as conn:
            inserted = not self._row_exists("messages", "id = ?", (mid,))
            conn.execute(
                "INSERT INTO messages(id, chat_id, sender_id, kind, \"text\", sent_at, edited_at, "
                "deleted_at, reply_to, attachments, ext, extra) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET chat_id = excluded.chat_id, "
                "sender_id = excluded.sender_id, kind = excluded.kind, \"text\" = excluded.\"text\", "
                "sent_at = excluded.sent_at, edited_at = excluded.edited_at, "
                "deleted_at = excluded.deleted_at, reply_to = excluded.reply_to, "
                "attachments = excluded.attachments, ext = excluded.ext, extra = excluded.extra",
                params,
            )
            row = conn.execute("SELECT rowid FROM messages WHERE id = ?", (mid,)).fetchone()
            rowid = int(row[0])
            self._fts_delete(rowid)
            self._fts_insert(rowid, mid, text, chat_id)
            return inserted

    def upsert_reaction(self, rec: dict) -> bool:
        fields, extra = _split_rec(rec, _REACTION_KEYS)
        message_id = _as_str(fields.get("message_id"), "message_id", required=True)
        contact_id = _as_str(fields.get("contact_id"), "contact_id")
        emoji = _as_str(fields.get("emoji"), "emoji")
        created_at = _as_str(fields.get("created_at"), "created_at")
        ext = _as_dict(fields.get("ext"), "ext")
        key = (message_id, contact_id, emoji, created_at)
        params = key + (
            _json_dump(ext, "ext"),
            _json_dump(extra, "extra"),
        )
        with self._tx() as conn:
            inserted = not self._row_exists(
                "reactions",
                "message_id = ? AND contact_id = ? AND emoji = ? AND created_at = ?",
                key,
            )
            conn.execute(
                "INSERT INTO reactions(message_id, contact_id, emoji, created_at, ext, extra) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(message_id, contact_id, emoji, created_at) DO UPDATE SET "
                "ext = excluded.ext, extra = excluded.extra",
                params,
            )
            return inserted

    def put_blob(self, data: bytes, *, mime: str = "", name: str = "",
                 ext: "dict | None" = None) -> str:
        """写入 blob（内容寻址、天然去重），返回 sha256 十六进制。

        `ext`：附件级自由元数据（fcx-format §3.5 的 `ext`）。**接口修订 2026-09-29（Lead）**：
        原冻结签名没有 `ext`，对抗核验实证"attachments.jsonl 的 ext 在导入→再导出时必丢"，
        故补此关键字参数；缺省 `None` ⇒ 与旧行为完全一致（向后兼容）。
        """
        blob = _as_bytes(data)
        mime_s = _as_str(mime, "mime")
        name_s = _as_str(name, "name")
        ext_d = _as_dict(ext, "ext") if ext is not None else {}
        sha = hashlib.sha256(blob).hexdigest()
        path = self._blob_path(sha)
        if not os.path.isfile(path):
            directory = os.path.dirname(path)
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-blob-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(blob)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)  # 原子落盘：读到的一半文件不会以 sha 名字出现
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        with self._tx() as conn:
            row = conn.execute(
                "SELECT mime, name, ext FROM attachment_meta WHERE sha256 = ?", (sha,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO attachment_meta(sha256, bytes, mime, name, ext, extra) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sha, len(blob), mime_s, name_s, _canonical_json(ext_d), _canonical_json({})),
                )
            else:
                # 空串/空字典不覆盖已有元数据（同一内容被不同来源重复导入时不至于把 name/mime/ext 抹掉）
                conn.execute(
                    "UPDATE attachment_meta SET bytes = ?, mime = ?, name = ?, ext = ? WHERE sha256 = ?",
                    (len(blob), mime_s or row["mime"], name_s or row["name"],
                     _canonical_json(ext_d) if ext_d else row["ext"], sha),
                )
        return sha

    def _blob_path(self, sha256: str) -> str:
        if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
            raise VaultError("非法 blob 标识（要求 64 位小写 hex sha256）：%r" % (sha256,))
        return os.path.join(self.blobs_dir, sha256[:2], sha256)

    # —— 读取（迭代器按 fcx-format §3 的排序键升序）——

    def iter_contacts(self) -> Iterator[dict]:
        rows = self._conn_or_raise().execute(
            "SELECT id, display_name, aliases, ext, extra FROM contacts ORDER BY " + _ORDER_CONTACTS
        ).fetchall()
        for row in rows:
            rec = {
                "id": row["id"],
                "display_name": row["display_name"],
                "aliases": _json_load(row["aliases"], "contacts.aliases", list),
                "ext": _json_load(row["ext"], "contacts.ext", dict),
            }
            yield _merge_extra(rec, row["extra"], "contacts.extra")

    def iter_chats(self) -> Iterator[dict]:
        rows = self._conn_or_raise().execute(
            "SELECT id, kind, title, created_at, updated_at, ext, extra FROM chats ORDER BY "
            + _ORDER_CHATS
        ).fetchall()
        for row in rows:
            rec = {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "ext": _json_load(row["ext"], "chats.ext", dict),
            }
            yield _merge_extra(rec, row["extra"], "chats.extra")

    def iter_messages(self, chat_id: "str | None" = None) -> Iterator[dict]:
        conn = self._conn_or_raise()
        sql = (
            'SELECT id, chat_id, sender_id, kind, "text", sent_at, edited_at, deleted_at, '
            "reply_to, attachments, ext, extra FROM messages"
        )
        params: list = []
        if chat_id is not None:
            sql += " WHERE chat_id = ?"
            params.append(_as_str(chat_id, "chat_id"))
        sql += " ORDER BY " + _ORDER_MESSAGES
        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            yield _message_from_row(row)

    def iter_reactions(self) -> Iterator[dict]:
        rows = self._conn_or_raise().execute(
            "SELECT message_id, contact_id, emoji, created_at, ext, extra FROM reactions "
            "ORDER BY " + _ORDER_REACTIONS
        ).fetchall()
        for row in rows:
            rec = {
                "message_id": row["message_id"],
                "contact_id": row["contact_id"],
                "emoji": row["emoji"],
                "created_at": row["created_at"],
                "ext": _json_load(row["ext"], "reactions.ext", dict),
            }
            yield _merge_extra(rec, row["extra"], "reactions.extra")

    def iter_attachments(self) -> Iterator[dict]:
        rows = self._conn_or_raise().execute(
            "SELECT sha256, bytes, mime, name, ext, extra FROM attachment_meta ORDER BY "
            + _ORDER_ATTACHMENTS
        ).fetchall()
        for row in rows:
            rec = {
                "sha256": row["sha256"],
                "bytes": int(row["bytes"]),
                "mime": row["mime"],
                "name": row["name"],
                "ext": _json_load(row["ext"], "attachment_meta.ext", dict),
            }
            yield _merge_extra(rec, row["extra"], "attachment_meta.extra")

    def get_blob(self, sha256: str) -> bytes:
        path = self._blob_path(sha256)
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except FileNotFoundError:
            raise VaultError("blob 不存在：%s" % sha256) from None

    def has_blob(self, sha256: str) -> bool:
        """blob 文件是否在盘上（内容寻址 ⇒ 文件名即内容，不看 DB 元数据）。"""
        if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
            return False
        return os.path.isfile(self._blob_path(sha256))

    def search(self, query: str, *, limit: "int | None" = 50) -> list[dict]:
        """FTS5 全文检索，结果按 bm25 `rank` 升序（越靠前越相关），返回完整消息记录。"""
        conn = self._conn_or_raise()
        if not isinstance(query, str):
            raise VaultError("query 必须是字符串（得到 %s）" % type(query).__name__)
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise VaultError("limit 必须是非负整数或 None")
            if limit == 0:
                return []
        text = query.strip()
        if not text:
            return []

        sql = (
            'SELECT m.id, m.chat_id, m.sender_id, m.kind, m."text", m.sent_at, m.edited_at, '
            "m.deleted_at, m.reply_to, m.attachments, m.ext, m.extra, f.rank AS _rank "
            "FROM messages_fts f JOIN messages m ON m.rowid = f.rowid "
            # 注意：MATCH 左侧必须是 FTS5 表名；写成别名 `f MATCH ?` 会报 no such column: f
            "WHERE messages_fts MATCH ? ORDER BY f.rank, m.chat_id, m.sent_at, m.id"
        )
        params: list = [_fts_document(text)]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # 用户输入不是合法 FTS5 表达式（如未闭合引号）⇒ 退回"逐词 AND"，不把语法错抛给 CLI。
            # 兜底表达式永远是合法 FTS5；它若还报错就说明是内部 bug，**必须响**（不许静默返回空）。
            fallback = _fts_fallback_query(text)
            if fallback is None:
                return []
            params[0] = fallback
            rows = conn.execute(sql, params).fetchall()
        return [_message_from_row(row) for row in rows]

    def stats(self) -> dict:
        conn = self._conn_or_raise()
        counts = {}
        for key, table in (
            ("chats", "chats"),
            ("contacts", "contacts"),
            ("messages", "messages"),
            ("reactions", "reactions"),
            ("attachments", "attachment_meta"),
        ):
            counts[key] = int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])
        counts["bytes"] = int(
            conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM attachment_meta").fetchone()[0]
        )
        return counts

    def content_digest(self) -> str:
        """库内容确定性摘要（sha256 hex）。

        覆盖 contacts/chats/messages/reactions/attachment_meta，按各自排序键升序规范化 JSON，
        **不含 blob 本体**（含 blob sha256 集合），**不含 ops**（变更历史，见模块 docstring）。
        """
        digest = hashlib.sha256()
        digest.update(b"freechat-vault content-digest v1\n")
        for name, records in (
            ("contacts", self.iter_contacts()),
            ("chats", self.iter_chats()),
            ("messages", self.iter_messages()),
            ("reactions", self.iter_reactions()),
            ("attachments", self.iter_attachments()),
        ):
            digest.update(("# %s\n" % name).encode("utf-8"))
            for rec in records:
                digest.update(_canonical_json(rec).encode("utf-8"))
                digest.update(b"\n")
        return digest.hexdigest()

    # —— op-log（N6 同步用；本版只需追加与读取）——

    def append_op(self, op: dict) -> None:
        if not isinstance(op, dict):
            raise VaultError("op 必须是 dict（得到 %s）" % type(op).__name__)
        payload = _json_dump(op, "op")
        with self._tx() as conn:
            conn.execute("INSERT INTO ops(rec) VALUES (?)", (payload,))

    def iter_ops(self) -> Iterator[dict]:
        rows = self._conn_or_raise().execute("SELECT rec FROM ops ORDER BY seq").fetchall()
        for row in rows:
            op = _json_load(row["rec"], "ops.rec", dict)
            if not isinstance(op, dict):
                raise VaultError("库内字段 ops.rec 必须是 JSON 对象")
            yield op

    # —— FTS 内部同步 ——

    def _fts_delete(self, rowid: int) -> None:
        self._conn_or_raise().execute("DELETE FROM messages_fts WHERE rowid = ?", (rowid,))

    def _fts_insert(self, rowid: int, message_id: str, text: str, chat_id: str) -> None:
        self._conn_or_raise().execute(
            'INSERT INTO messages_fts(rowid, message_id, "text", chat_id) VALUES (?, ?, ?, ?)',
            (rowid, message_id, _fts_document(text), _fts_document(chat_id)),
        )
