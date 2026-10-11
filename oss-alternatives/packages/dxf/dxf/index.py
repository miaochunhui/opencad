"""dxf.index —— 图纸索引层：扫盘 → 解析 DXF → 灌进 `packages/vault` → 检索/图层/统计。

设计（改代码前先读；完整版见 `oss-alternatives/docs/design/dxf-format-notes.md`）
--------------------------------------------------------------------------------
* **真源在磁盘**：`*.dxf` 文件是唯一真源；vault 里存的只是**索引副本**。
  真源丢了 vault 还原不出图纸，反过来可以 —— 所以 `index_drawings()` 永远可重跑。
* **不改底座公开接口**：只用 `docs/design/impl-interfaces.md` §2/§3 冻结的方法
  （`Vault.upsert_chat/upsert_message/iter_chats/iter_messages/search/stats/content_digest`
  + `fcx.export_fcx/import_fcx/FcxArchive/ImportStats/FcxError`）。`BASE_METHODS` 把这份清单
  钉在代码里，供单测/文档双向核对。
* **数据模型映射**（图纸 → 聊天域记录，不做 schema 扩展）::

      chat:    id="dxf:<文件名>"      kind="group"  title=<文件名>
      message: id="ent:<文件名>/<序号>"  chat_id="dxf:<文件名>"  sender_id="dxf:local"
               kind="text"  text=<实体可检索摘要，如 "LINE 0,0 -> 10,10 [layer=轮廓]">
               sent_at=<文件 mtime, ISO-8601 秒精度>
               deleted_at=<墓碑时间>
               ext={type, layer, tags, source_path, mtime, …}

  「图纸名」= **文件名（含扩展名）**，例如 `plan-a.dxf`；`序号` = 该文件 `ENTITIES` 段里
  顶层实体的 1 基序号。同名不同目录的两份图纸会抢同一个会话 id ⇒ 后到的记
  `duplicate_drawing` 跳过（**不静默合并**）。
* **幂等**：每个实体算一个 `fingerprint`（图纸名 + 序号 + 类型 + **原文** + 子实体原文 +
  来源路径的 sha256，**不含 mtime/indexed_at**）。已存在且指纹相同、且没被墓碑掉 ⇒
  **一个字节都不写**。同一目录重复索引 `imported=0 updated=0 writes=0`，`content_digest()` 不变。
* **删除 = 墓碑**：图纸文件消失 ⇒ 该会话 + 它的所有实体消息写 `deleted_at`
  （顶层字段 + `ext.deleted_at`），**不物理删**、摘要保留；文件回来 ⇒ 清墓碑（`restored`）。
* **只索引顶层实体**：`POLYLINE` 的 `VERTEX…SEQEND` 挂在父实体上（`ext.children`），
  不单独占一条消息 —— 但它们的**原文进父实体的指纹**，改了会被看见。
* **纯标准库**：`os/posixpath/hashlib/json/sys/dataclasses/datetime`，零第三方依赖、不联网。
"""

from __future__ import annotations

import os
import posixpath
import re
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------
# sys.path 自举：`packages/dxf`（本包自己）与 `packages/vault`（底座）双双入路径，
# 使 `python3 -m unittest`（cwd=packages/dxf）等跑法**不需要**用户先设 PYTHONPATH。
# --------------------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))          # …/packages/dxf/dxf
_DXF_PKG_PARENT = os.path.dirname(_HERE)                    # …/packages/dxf
_PACKAGES_DIR = os.path.dirname(_DXF_PKG_PARENT)            # …/packages
_VAULT_PKG_PARENT = os.path.join(_PACKAGES_DIR, "vault")    # …/packages/vault


def bootstrap_sys_path() -> list:
    """把本包父目录与 `packages/vault` 插到 `sys.path` 首位（幂等）。返回插入的目录。"""
    added = []
    for path in (_DXF_PKG_PARENT, _VAULT_PKG_PARENT):
        if path and path not in sys.path:
            sys.path.insert(0, path)
            added.append(path)
    return added


bootstrap_sys_path()

from freechat_vault.fcx import (  # noqa: E402  —— 必须在 sys.path 自举之后
    FcxArchive,
    ImportStats,
    export_fcx,
    import_fcx,
)

from dxf.model import (  # noqa: E402
    SCHEMA as DOCUMENT_SCHEMA,
    Document,
    DxfError,
    canonical_json,
    sha256_text,
)
from dxf.reader import read as read_dxf  # noqa: E402

__all__ = [
    "DXF_CHAT_PREFIX",
    "ENTITY_ID_PREFIX",
    "DXF_SCHEMA",
    "MAX_DRAWING_BYTES",
    "DRAWING_EXTENSIONS",
    "BASE_METHODS",
    "EXT_REQUIRED_KEYS",
    "DxfIndexError",
    "Drawing",
    "ScanReport",
    "IndexStats",
    "chat_id_for",
    "entity_message_id",
    "drawing_name_for_path",
    "drawing_from_chat_id",
    "scan_drawings",
    "scan",
    "index_drawings",
    "load_drawings",
    "drawings",
    "entities",
    "layers",
    "entity_types",
    "search_entities",
    "stats",
    "bootstrap_sys_path",
    "read_dxf",
    "export_fcx",
    "import_fcx",
    "FcxArchive",
    "ImportStats",
]

# --------------------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------------------

#: 一张图纸在 vault 里落成**一个会话**：`dxf:<文件名>`（约定，见模块 docstring）
DXF_CHAT_PREFIX = "dxf:"
DXF_CHAT_KIND = "group"
DXF_SENDER_ID = "dxf:local"

#: 一个实体在 vault 里落成**一条消息**：`ent:<图纸名>/<序号>`
ENTITY_ID_PREFIX = "ent:"

DXF_SCHEMA = "dxf-index/1"

#: 单张图纸上限：超过即跳过（不算错误，记 warning + skipped）
MAX_DRAWING_BYTES = 32 * 1024 * 1024  # 32 MiB

#: 认的图纸扩展名
DRAWING_EXTENSIONS = (".dxf",)

#: 一律跳过的目录名（此外**任何**以 "." 开头的目录/文件也跳过）
SKIP_DIR_NAMES = frozenset({".git", "__pycache__", ".dxf-trash"})

#: 本包实际用到的 vault 公开方法（**不改底座公开接口**的自证清单）
BASE_METHODS = (
    "Vault.__init__",
    "Vault.close",
    "Vault.__enter__",
    "Vault.__exit__",
    "Vault.upsert_chat",
    "Vault.upsert_message",
    "Vault.iter_chats",
    "Vault.iter_messages",
    "Vault.search",
    "Vault.stats",
    "Vault.content_digest",
    "fcx.export_fcx",
    "fcx.import_fcx",
    "fcx.FcxArchive",
    "fcx.ImportStats",
    "fcx.FcxError",
    "store.VaultError",
    "store.DB_FILENAME",
)

#: 实体消息 `ext` 里**必需**的五个键（验收冻结：`ext = {type, layer, tags, source_path, mtime}`）
EXT_REQUIRED_KEYS = ("type", "layer", "tags", "source_path", "mtime")

_ILLEGAL_REL_CHARS = re.compile(r"[\x00-\x1f]")


class DxfIndexError(Exception):
    """索引层的可预期错误（目录不存在、参数非法）。"""


# --------------------------------------------------------------------------------------
# id 约定
# --------------------------------------------------------------------------------------


def drawing_name_for_path(path: str) -> str:
    """图纸名 = **文件名（含扩展名）**，例如 `/tmp/a/plan-b.dxf` → `plan-b.dxf`。"""
    return os.path.basename(os.fspath(path))


def chat_id_for(drawing: str) -> str:
    """图纸名 → 会话 id（`dxf:<文件名>`）。"""
    name = (drawing or "").strip()
    if not name:
        raise DxfIndexError("图纸名不能为空")
    if name.startswith(DXF_CHAT_PREFIX):
        return name
    return DXF_CHAT_PREFIX + name


def drawing_from_chat_id(chat_id: str) -> "str | None":
    """会话 id → 图纸名；不是本应用的会话返回 None。"""
    if isinstance(chat_id, str) and chat_id.startswith(DXF_CHAT_PREFIX):
        name = chat_id[len(DXF_CHAT_PREFIX):]
        return name or None
    return None


def entity_message_id(drawing: str, seq: int) -> str:
    """（图纸名, 序号）→ 消息 id（`ent:<图纸名>/<序号>`）。"""
    name = (drawing or "").strip()
    if not name:
        raise DxfIndexError("图纸名不能为空（seq=%r）" % (seq,))
    try:
        number = int(seq)
    except (TypeError, ValueError):
        raise DxfIndexError("实体序号必须是整数：%r" % (seq,)) from None
    if number < 1:
        raise DxfIndexError("实体序号从 1 起（得到 %r）" % (seq,))
    return "%s%s/%d" % (ENTITY_ID_PREFIX, name, number)


def _rel_posix(root: str, abspath: str) -> str:
    rel = os.path.relpath(abspath, root)
    return posixpath.normpath(rel.replace(os.sep, "/"))


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _iso_from_epoch(epoch: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------------------
# 扫盘
# --------------------------------------------------------------------------------------


@dataclass
class Drawing:
    """一张读进来的图纸（**纯函数**：只依赖这一个文件的内容 + stat）。"""

    name: str                      # 文件名（含扩展名）
    abspath: str
    path: str                      # 规范化相对路径（POSIX 分隔符）
    mtime: str                     # ISO-8601 带偏移、秒精度
    size: int
    document: Document
    warnings: tuple = ()

    def entities(self) -> list:
        return self.document.entities()

    def entity_fingerprint(self, seq: int, entity) -> str:
        return entity.fingerprint(drawing=self.name, seq=seq, source_path=self.path)

    def fingerprint(self) -> str:
        """整张图纸的指纹（内容口径，不含 mtime / indexed_at）。"""
        return sha256_text(canonical_json({
            "name": self.name,
            "path": self.path,
            "encoding": self.document.encoding,
            "entities": [
                self.entity_fingerprint(seq, entity)
                for seq, entity in enumerate(self.entities(), 1)
            ],
        }))


@dataclass
class ScanReport:
    """`scan_drawings()` 的完整结果：图纸 + 发现数 + 跳过原因 + 汇总 warning。"""

    root: str
    drawings: list = field(default_factory=list)
    discovered: int = 0
    skipped: list = field(default_factory=list)          # (relpath, reason)
    warnings: list = field(default_factory=list)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    def as_dict(self) -> dict:
        return {
            "root": self.root,
            "drawings": [drawing.name for drawing in self.drawings],
            "discovered": self.discovered,
            "skipped": [list(item) for item in self.skipped],
            "warnings": list(self.warnings),
        }


def scan_drawings(drawings_dir: str) -> ScanReport:
    """递归扫 `drawings_dir` 下的 `*.dxf`，逐个读成 `Document`。

    跳过（都留痕，**不静默**）：隐藏文件/隐藏目录、符号链接、> `MAX_DRAWING_BYTES`、
    读不了的文件、**解析失败的文件**（`DxfError` 的可读消息连行号一起进 `skipped` 与
    `warnings`）、以及**同名不同目录**的重复图纸（后到的跳过）。
    """
    root = os.path.abspath(os.fspath(drawings_dir))
    if not os.path.isdir(root):
        raise DxfIndexError("图纸目录不存在或不是目录：%s" % root)
    report = ScanReport(root=root)
    found: dict = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".") and name not in SKIP_DIR_NAMES
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in DRAWING_EXTENSIONS:
                continue
            abspath = os.path.join(dirpath, name)
            rel = _rel_posix(root, abspath)
            if _ILLEGAL_REL_CHARS.search(rel):
                report.skipped.append((rel, "illegal_chars"))
                report.warnings.append("%s：路径含控制字符，已跳过" % rel)
                continue
            report.discovered += 1
            if os.path.islink(abspath):
                report.skipped.append((rel, "symlink"))
                report.warnings.append("%s：符号链接，已跳过（真源必须在本库内）" % rel)
                continue
            if not os.path.isfile(abspath):
                report.skipped.append((rel, "not_a_file"))
                continue
            try:
                stat = os.stat(abspath)
            except OSError as exc:
                report.skipped.append((rel, "unreadable"))
                report.warnings.append("%s：stat 失败 %s" % (rel, exc))
                continue
            if stat.st_size > MAX_DRAWING_BYTES:
                report.skipped.append((rel, "too_large"))
                report.warnings.append(
                    "%s：%d 字节 > 上限 %d 字节，已跳过" % (rel, stat.st_size, MAX_DRAWING_BYTES)
                )
                continue
            previous = found.get(name)
            if previous is not None:
                report.skipped.append((rel, "duplicate_drawing"))
                report.warnings.append(
                    "%s：duplicate_drawing: 图纸名 %r 已被 %s 占用（会话 id 按文件名走）⇒ 这一份不索引"
                    % (rel, name, previous[0])
                )
                continue
            found[name] = (rel, abspath, stat)

    for name in sorted(found):
        rel, abspath, stat = found[name]
        try:
            document = read_dxf(abspath)
        except (DxfError, OSError) as exc:
            report.skipped.append((rel, "parse_error"))
            report.warnings.append("%s：解析失败（%s）" % (rel, exc))
            continue
        drawing = Drawing(
            name=name,
            abspath=abspath,
            path=rel,
            mtime=_iso_from_epoch(stat.st_mtime),
            size=stat.st_size,
            document=document,
            warnings=tuple(document.warnings),
        )
        report.warnings.extend("%s: %s" % (rel, item) for item in document.warnings)
        report.drawings.append(drawing)
    report.drawings.sort(key=lambda item: item.path)
    return report


def scan(drawings_dir: str, *, warnings: "list | None" = None) -> list:
    """`scan_drawings()` 的轻量入口；`warnings` 传 list 可把警告取走。"""
    report = scan_drawings(drawings_dir)
    if warnings is not None:
        warnings.extend(report.warnings)
    return report.drawings


# --------------------------------------------------------------------------------------
# 索引（写进 vault）
# --------------------------------------------------------------------------------------


@dataclass
class IndexStats:
    """`index_drawings()` 的结果。`imported/updated/restored/unchanged` 互不重叠。"""

    drawings_dir: str = ""
    indexed_at: str = ""
    discovered: int = 0            # 扫到的 *.dxf 文件数（含被跳过的）
    indexed: int = 0               # 真正读成 Document 的图纸数
    skipped: int = 0               # 因超限/软链/读不了/解析失败/重名而跳过的文件数
    imported: int = 0              # vault 里**新建**的实体消息数
    updated: int = 0               # 已存在且内容变了 ⇒ 覆盖
    restored: int = 0              # 原先有墓碑、文件回来了 ⇒ 清墓碑的实体数
    unchanged: int = 0             # fingerprint 相同 ⇒ **一个字节都没写**
    deleted_entities: int = 0      # 本次新打墓碑的实体数
    restored_drawings: int = 0     # 文件回来了、会话墓碑被清掉的图纸数
    deleted_drawings: int = 0      # 本次新打墓碑的图纸数
    writes: int = 0                # upsert_message 调用次数（幂等判据：重复索引必须为 0）
    chats_written: int = 0         # upsert_chat 调用次数
    drawings: int = 0              # 索引后库里的活图纸数
    entities: int = 0              # 索引后库里的活实体数
    tombstoned_drawings: int = 0
    tombstoned_entities: int = 0
    unknown_entities: int = 0      # 活实体里"只保留不解释"的条数
    proxy_entities: int = 0
    xdata_entities: int = 0
    entities_by_type: dict = field(default_factory=dict)
    layers: int = 0                # 活实体用到的不同图层数
    warnings: list = field(default_factory=list)
    digest_before: str = ""
    digest_after: str = ""

    @property
    def digest_changed(self) -> bool:
        return self.digest_before != self.digest_after

    def as_dict(self) -> dict:
        return {
            "drawings_dir": self.drawings_dir,
            "indexed_at": self.indexed_at,
            "discovered": self.discovered,
            "indexed": self.indexed,
            "skipped": self.skipped,
            "imported": self.imported,
            "updated": self.updated,
            "restored": self.restored,
            "unchanged": self.unchanged,
            "deleted_entities": self.deleted_entities,
            "restored_drawings": self.restored_drawings,
            "deleted_drawings": self.deleted_drawings,
            "writes": self.writes,
            "chats_written": self.chats_written,
            "drawings": self.drawings,
            "entities": self.entities,
            "tombstoned_drawings": self.tombstoned_drawings,
            "tombstoned_entities": self.tombstoned_entities,
            "unknown_entities": self.unknown_entities,
            "proxy_entities": self.proxy_entities,
            "xdata_entities": self.xdata_entities,
            "entities_by_type": dict(self.entities_by_type),
            "layers": self.layers,
            "digest_before": self.digest_before,
            "digest_after": self.digest_after,
            "digest_changed": self.digest_changed,
            "warnings": list(self.warnings),
        }


def _chat_record(drawing: Drawing, stamp: str) -> dict:
    document = drawing.document
    return {
        "id": chat_id_for(drawing.name),
        "kind": DXF_CHAT_KIND,
        "title": drawing.name,
        "created_at": drawing.mtime or stamp,
        "updated_at": drawing.mtime or stamp,
        "ext": {
            "app": "dxf",
            "schema": DXF_SCHEMA,
            "source": "dxf-on-disk",
            "path": drawing.path,
            "mtime": drawing.mtime or stamp,
            "bytes": drawing.size,
            "encoding": document.encoding,
            "newline": document.newline_style,
            "sections": document.section_names(),
            "entity_count": document.entity_count(),
            "unknown_entities": len(document.unknown_entities()),
            "proxy_entities": len(document.proxy_entities()),
            "xdata_entities": len(document.xdata_entities()),
            "layers": document.layer_names(),
            "entity_types": document.entity_type_counts(),
            "document_schema": DOCUMENT_SCHEMA,
        },
    }


def _chat_needs_write(existing: "dict | None", record: dict) -> bool:
    if existing is None:
        return True
    return canonical_json(existing) != canonical_json(record)


def _entity_record(drawing: Drawing, seq: int, entity, stamp: str) -> dict:
    """一个实体 → 一条消息记录（`ext` 里**必有** type/layer/tags/source_path/mtime 五键）。"""
    ext: dict = {
        "type": entity.type,
        "layer": entity.layer,
        "tags": [tag.as_dict() for tag in entity.tags],
        "source_path": drawing.path,
        "mtime": drawing.mtime or stamp,
        "app": "dxf",
        "schema": DXF_SCHEMA,
        "drawing": drawing.name,
        "seq": seq,
        "handle": entity.handle,
        "unknown": entity.is_unknown,
        "proxy": entity.is_proxy,
        "xdata_apps": entity.xdata_apps,
        "xdata_count": len(entity.xdata),
        "raw_sha256": sha256_text(entity.raw),
        "raw_bytes": len(entity.raw.encode("utf-8")),
        "fingerprint": drawing.entity_fingerprint(seq, entity),
        "indexed_at": stamp,
    }
    if entity.xdata:
        ext["xdata"] = [tag.as_dict() for tag in entity.xdata]
    if entity.children:
        ext["children"] = [child.as_dict() for child in entity.children]
    if entity.is_unknown:
        ext["preserved"] = "unknown-entity-kept-verbatim"
    if entity.is_proxy:
        ext["preserved"] = "proxy-entity-kept-verbatim"
    return {
        "id": entity_message_id(drawing.name, seq),
        "chat_id": chat_id_for(drawing.name),
        "sender_id": DXF_SENDER_ID,
        "kind": "text",
        "text": entity.summary(),
        "sent_at": drawing.mtime or stamp,
        "ext": ext,
    }


def _has_tombstone(record: dict) -> bool:
    ext = record.get("ext") or {}
    return bool(record.get("deleted_at") or (isinstance(ext, dict) and ext.get("deleted_at")))


def _tombstone(record: dict, chat_id: str, stamp: str) -> dict:
    """把一条已有的实体消息改写成墓碑（**正文一个字不改**，只加 `deleted_at`）。"""
    ext = dict(record.get("ext") or {})
    ext["deleted_at"] = stamp
    ext["indexed_at"] = stamp
    return {
        "id": record["id"],
        "chat_id": chat_id,
        "sender_id": record.get("sender_id") or DXF_SENDER_ID,
        "kind": record.get("kind") or "text",
        "text": record.get("text", ""),
        "sent_at": record.get("sent_at") or stamp,
        "deleted_at": stamp,
        "ext": ext,
    }


def index_drawings(vault, drawings_dir: str, *, indexed_at: "str | None" = None) -> IndexStats:
    """把 `drawings_dir` 下的图纸 upsert 进 vault（一张图纸一个会话，一个实体一条消息）。

    幂等（同一目录重复跑 ⇒ `imported=0 updated=0 writes=0`）、删文件走墓碑、文件回来自动复活。
    只用冻结的公开方法；本函数**不改** `packages/vault/` 下任何文件。
    """
    stamp = indexed_at or _now_iso()
    report = scan_drawings(drawings_dir)
    acc = IndexStats(
        drawings_dir=report.root,
        indexed_at=stamp,
        discovered=report.discovered,
        indexed=len(report.drawings),
        skipped=report.skipped_count,
        warnings=list(report.warnings),
    )
    live_names = {drawing.name for drawing in report.drawings}

    acc.digest_before = vault.content_digest()

    existing_chats: dict = {}
    for chat in vault.iter_chats():
        existing_chats[chat.get("id")] = chat

    existing_entities: dict = {}
    for record in vault.iter_messages():
        chat_id = record.get("chat_id") or ""
        if not chat_id.startswith(DXF_CHAT_PREFIX):
            continue
        ext = record.get("ext") or {}
        if not isinstance(ext, dict) or ext.get("schema") != DXF_SCHEMA:
            continue                                   # 不是本应用写的消息：不碰
        existing_entities.setdefault(chat_id, {})[record["id"]] = record

    # —— 1) 活图纸：会话 + 实体 ——
    for drawing in report.drawings:
        chat_id = chat_id_for(drawing.name)
        previous_chat = existing_chats.get(chat_id)
        record = _chat_record(drawing, stamp)
        if previous_chat is not None and (previous_chat.get("ext") or {}).get("deleted_at"):
            acc.restored_drawings += 1
            acc.warnings.append("%s：restored: 图纸文件回来了，已清除会话墓碑" % drawing.name)
        if _chat_needs_write(previous_chat, record):
            vault.upsert_chat(record)
            acc.chats_written += 1

        seen = existing_entities.get(chat_id, {})
        seen_ids: set = set()
        for seq, entity in enumerate(drawing.entities(), 1):
            message_id = entity_message_id(drawing.name, seq)
            seen_ids.add(message_id)
            previous = seen.get(message_id)
            previous_ext = (previous or {}).get("ext") or {}
            tombstoned = _has_tombstone(previous) if previous else False
            fingerprint = drawing.entity_fingerprint(seq, entity)
            if previous is not None and not tombstoned and previous_ext.get("fingerprint") == fingerprint:
                acc.unchanged += 1
                continue
            inserted = vault.upsert_message(_entity_record(drawing, seq, entity, stamp))
            acc.writes += 1
            if inserted:
                acc.imported += 1
            elif tombstoned:
                acc.restored += 1
            else:
                acc.updated += 1
        # 图纸还在、但里面的实体**少了**（文件在别处被改过）⇒ 给消失的实体打墓碑。
        # 不打的话它们会永远留在检索与统计里当"活实体"（Ghost）。
        for message_id, record in sorted(seen.items()):
            if message_id in seen_ids or _has_tombstone(record):
                continue
            vault.upsert_message(_tombstone(record, chat_id, stamp))
            acc.writes += 1
            acc.deleted_entities += 1

    # —— 2) 死图纸：vault 里有、磁盘上没了 ⇒ 墓碑 ——
    for chat_id, chat in sorted(existing_chats.items()):
        name = drawing_from_chat_id(chat_id)
        if name is None or name in live_names:
            continue
        chat_ext = dict(chat.get("ext") or {})
        if chat_ext.get("schema") != DXF_SCHEMA:
            continue                                   # 别的应用恰好用了 dxf: 前缀：不碰
        if chat_ext.get("deleted_at"):
            continue                                   # 已经是墓碑、文件仍未回来 ⇒ 不重写（幂等）
        for message_id, record in sorted(existing_entities.get(chat_id, {}).items()):
            if _has_tombstone(record):
                acc.unchanged += 1
                continue
            vault.upsert_message(_tombstone(record, chat_id, stamp))
            acc.writes += 1
            acc.deleted_entities += 1
        chat_ext["deleted_at"] = stamp
        chat_ext["indexed_at"] = stamp
        tombstone_chat = dict(chat)
        tombstone_chat["ext"] = chat_ext
        tombstone_chat["updated_at"] = chat.get("updated_at") or stamp
        vault.upsert_chat(tombstone_chat)
        acc.chats_written += 1
        acc.deleted_drawings += 1

    acc.digest_after = vault.content_digest()

    info = stats(vault)
    acc.drawings = info["drawings"]
    acc.entities = info["entities"]
    acc.tombstoned_drawings = info["tombstoned_drawings"]
    acc.tombstoned_entities = info["tombstoned_entities"]
    acc.unknown_entities = info["unknown_entities"]
    acc.proxy_entities = info["proxy_entities"]
    acc.xdata_entities = info["xdata_entities"]
    acc.entities_by_type = info["by_type"]
    acc.layers = info["layers"]
    return acc


# --------------------------------------------------------------------------------------
# 读侧：把库里的消息还原成"图纸视图"
# --------------------------------------------------------------------------------------


def _entity_view(record: dict, name: str) -> dict:
    ext = record.get("ext") or {}
    deleted_at = ext.get("deleted_at") or record.get("deleted_at") or None
    return {
        "id": record["id"],
        "drawing": name,
        "chat_id": record.get("chat_id", ""),
        "seq": ext.get("seq", -1),
        "type": ext.get("type", ""),
        "layer": ext.get("layer", "0"),
        "handle": ext.get("handle", ""),
        "tags": list(ext.get("tags") or []),
        "xdata": list(ext.get("xdata") or []),
        "xdata_apps": list(ext.get("xdata_apps") or []),
        "children": list(ext.get("children") or []),
        "unknown": bool(ext.get("unknown")),
        "proxy": bool(ext.get("proxy")),
        "summary": record.get("text", ""),
        "text": record.get("text", ""),
        "sent_at": record.get("sent_at", ""),
        "source_path": ext.get("source_path", ""),
        "mtime": ext.get("mtime", ""),
        "raw_sha256": ext.get("raw_sha256", ""),
        "raw_bytes": ext.get("raw_bytes", -1),
        "indexed_at": ext.get("indexed_at", ""),
        "deleted": bool(deleted_at),
        "deleted_at": deleted_at,
        "ext": ext,
    }


def load_drawings(vault, *, include_deleted: bool = False) -> dict:
    """读出库里属于本应用的图纸（按图纸名建 dict）。不是本应用写的一律忽略。

    每张图纸：`{name, chat_id, title, entities, deleted, deleted_at, source_path, mtime, …}`；
    `entities` 是 `{序号: 实体视图}`，迭代顺序＝vault 的 `(chat_id, sent_at, id)` 序。
    """
    out: dict = {}
    for chat in vault.iter_chats():
        name = drawing_from_chat_id(chat.get("id", ""))
        if name is None:
            continue
        ext = chat.get("ext") or {}
        if ext.get("schema") != DXF_SCHEMA:
            continue
        deleted_at = ext.get("deleted_at") or None
        if deleted_at and not include_deleted:
            continue
        out[name] = {
            "name": name,
            "chat_id": chat.get("id", ""),
            "title": chat.get("title") or name,
            "entities": {},
            "source_path": ext.get("path", ""),
            "mtime": ext.get("mtime", ""),
            "bytes": ext.get("bytes", -1),
            "encoding": ext.get("encoding", ""),
            "newline": ext.get("newline", ""),
            "sections": list(ext.get("sections") or []),
            "layers": list(ext.get("layers") or []),
            "entity_types": dict(ext.get("entity_types") or {}),
            "created_at": chat.get("created_at", ""),
            "updated_at": chat.get("updated_at", ""),
            "deleted": bool(deleted_at),
            "deleted_at": deleted_at,
            "chat_ext": ext,
        }
    for record in vault.iter_messages():
        name = drawing_from_chat_id(record.get("chat_id", ""))
        if name is None or name not in out:
            continue
        ext = record.get("ext") or {}
        if not isinstance(ext, dict) or ext.get("schema") != DXF_SCHEMA:
            continue                                   # 不是本应用写的实体：不认
        view = _entity_view(record, name)
        if view["deleted"] and not include_deleted:
            continue
        key = view["seq"] if isinstance(view["seq"], int) and view["seq"] > 0 else view["id"]
        out[name]["entities"][key] = view
    return out


def _live_drawings(vault, *, include_deleted: bool = False) -> list:
    views = [view for view in load_drawings(vault, include_deleted=include_deleted).values()
             if include_deleted or not view["deleted"]]
    views.sort(key=lambda view: view["name"])
    return views


def drawings(vault, *, include_deleted: bool = False) -> list:
    """库里的图纸视图（按图纸名升序）。"""
    return _live_drawings(vault, include_deleted=include_deleted)


def _iter_entity_views(vault, *, drawing: "str | None" = None,
                       include_deleted: bool = False) -> list:
    out: list = []
    for view in _live_drawings(vault, include_deleted=include_deleted):
        if drawing is not None and view["name"] != drawing:
            continue
        for entity in view["entities"].values():
            out.append(entity)
    out.sort(key=lambda item: (item["drawing"], item["seq"] if isinstance(item["seq"], int) else 0))
    return out


def entities(vault, entity_type: "str | None" = None, *, drawing: "str | None" = None,
             include_deleted: bool = False, limit: "int | None" = None,
             unknown_only: bool = False) -> list:
    """按类型挑实体，返回**实体视图**列表（`{drawing, seq, type, layer, summary, tags, …}`）。

    * `entity_type=None` ⇒ 全部类型；大小写不敏感；
    * `drawing=` 限定单张图纸；`unknown_only=True` 只要"只保留不解释"的那些；
    * `limit=None` = 不限。参数非法抛 `DxfIndexError`（不静默返回空）。
    """
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise DxfIndexError("limit 必须是非负整数或 None")
    wanted = (entity_type or "").strip().upper()
    result = []
    for view in _iter_entity_views(vault, drawing=drawing, include_deleted=include_deleted):
        if wanted and str(view["type"]).upper() != wanted:
            continue
        if unknown_only and not view["unknown"]:
            continue
        result.append(view)
    if limit is not None:
        result = result[:limit]
    return result


def entity_types(vault, *, include_deleted: bool = False) -> dict:
    """类型 → 条数（活实体口径）。"""
    counts: dict = {}
    for view in _iter_entity_views(vault, include_deleted=include_deleted):
        counts[view["type"]] = counts.get(view["type"], 0) + 1
    return counts


def layers(vault, *, drawing: "str | None" = None, include_deleted: bool = False) -> list:
    """图层表：`[{layer, entities, drawings, types}]`，按 (条数降序, 图层名升序)。

    数字是**现算**的（从实体消息的 `ext.layer` 数出来），不是从 DXF 的 LAYER 表读来的声明值。
    """
    table: dict = {}
    for view in _iter_entity_views(vault, drawing=drawing, include_deleted=include_deleted):
        name = view["layer"] or "0"
        bucket = table.setdefault(name, {"layer": name, "entities": 0, "drawings": [], "types": {}})
        bucket["entities"] += 1
        if view["drawing"] not in bucket["drawings"]:
            bucket["drawings"].append(view["drawing"])
        kind = view["type"]
        bucket["types"][kind] = bucket["types"].get(kind, 0) + 1
    for bucket in table.values():
        bucket["drawings"].sort()
    return [table[key] for key in sorted(table, key=lambda item: (-table[item]["entities"], item))]


def _terms(query: str) -> list:
    return [part for part in re.split(r"\s+", (query or "").strip()) if part]


def _snippet(text: str, query: str, width: int = 120) -> str:
    if not text:
        return ""
    lowered = text.casefold()
    for term in _terms(query):
        position = lowered.find(term.casefold())
        if position >= 0:
            start = max(0, position - width // 3)
            piece = text[start:start + width].replace("\n", " ")
            return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")
    piece = text[:width].replace("\n", " ")
    return piece + ("…" if len(text) > width else "")


def search_entities(vault, query: str, *, limit: int = 20, drawing: "str | None" = None,
                    include_deleted: bool = False) -> list:
    """走 vault 的 FTS5 检索（`Vault.search`），只返回本应用的实体消息。

    每条命中：`{drawing, seq, id, type, layer, summary, snippet, rank}`。
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise DxfIndexError("limit 必须是非负整数")
    if limit == 0 or not (query or "").strip():
        return []
    by_id: dict = {}
    for view in _iter_entity_views(vault, drawing=drawing, include_deleted=include_deleted):
        by_id[view["id"]] = view
    window = limit * 10 + 50
    records = vault.search(query, limit=window)
    if len(records) >= window:
        records = vault.search(query, limit=None)      # 过取窗口被截断 ⇒ 退化成一次全库检索
    hits: list = []
    for rank, record in enumerate(records, 1):
        view = by_id.get(record.get("id"))
        if view is None:
            continue
        hits.append({
            "drawing": view["drawing"],
            "seq": view["seq"],
            "id": view["id"],
            "type": view["type"],
            "layer": view["layer"],
            "summary": view["summary"],
            "snippet": _snippet(view["summary"], query),
            "rank": rank,
        })
        if len(hits) >= limit:
            break
    return hits


def stats(vault) -> dict:
    """图纸库统计。

    口径（**活实体 = 既不在死图纸里、自己也没被打墓碑**）：图纸在、但里面的某个实体被删了
    （见 `index_drawings` 的"消失即墓碑"分支）时，那条实体属于墓碑栏，**不许算进活实体**。
    """
    all_views = list(load_drawings(vault, include_deleted=True).values())
    live = sorted((view for view in all_views if not view["deleted"]), key=lambda item: item["name"])
    dead = [view for view in all_views if view["deleted"]]
    live_entities: list = []
    dead_entities = 0
    for view in all_views:
        for entity in view["entities"].values():
            if view["deleted"] or entity["deleted"]:
                dead_entities += 1
            else:
                live_entities.append(entity)
    by_type: dict = {}
    by_layer: dict = {}
    unknown = 0
    proxy = 0
    xdata = 0
    children = 0
    raw_bytes = 0
    stamps: list = []
    for entity in live_entities:
        by_type[entity["type"]] = by_type.get(entity["type"], 0) + 1
        layer = entity["layer"] or "0"
        by_layer[layer] = by_layer.get(layer, 0) + 1
        unknown += 1 if entity["unknown"] else 0
        proxy += 1 if entity["proxy"] else 0
        xdata += 1 if entity["xdata"] else 0
        children += len(entity["children"])
        if isinstance(entity["raw_bytes"], int) and entity["raw_bytes"] >= 0:
            raw_bytes += entity["raw_bytes"]
        if entity.get("indexed_at"):
            stamps.append(entity["indexed_at"])
    return {
        "drawings": len(live),
        "tombstoned_drawings": len(dead),
        "total_drawings": len(all_views),
        "entities": len(live_entities),
        "tombstoned_entities": dead_entities,
        "total_entities": len(live_entities) + dead_entities,
        "unknown_entities": unknown,
        "proxy_entities": proxy,
        "xdata_entities": xdata,
        "child_entities": children,
        "by_type": by_type,
        "by_layer": by_layer,
        "layers": len(by_layer),
        "entity_raw_bytes": raw_bytes,
        # 没索引过任何东西时报 None（"没采到"不与"真为零"同形，AGENTS E2.3）
        "indexed_at": max(stamps) if stamps else None,
        "schema": DXF_SCHEMA,
        "source_paths": sorted(view["source_path"] for view in live if view["source_path"]),
        "vault_stats": vault.stats(),
    }
