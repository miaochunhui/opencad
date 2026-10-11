"""`.fcx` v1 读写与校验 —— 格式规范见 `oss-alternatives/docs/design/fcx-format.md`（冻结真源）。

设计要点（逐条对应规范的 I1–I5）：
* **I1 导出可再导入**：同内容两次导出，除 volatile 字段（`export_id`/`exported_at`/`notes`）外逐字节相同。
  实现手段：记录按规范排序键升序、JSON 规范化（`sort_keys` + 紧凑分隔符 + 不转义非 ASCII）、
  zip 条目固定 mtime、**不压缩**（`ZIP_STORED` ⇒ 不受 zlib 版本影响）、成员写入顺序固定。
* **I2 幂等**：去重键 = 记录 `id` / 附件 `sha256`；二次导入 `imported=0`。
* **I3 稳定 id**：id 原样搬运，绝不重编号。
* **I4 可被第三方解析**：zip + UTF-8 JSON/JSONL + 附件裸字节，不加密。
  **JSONL 的行分隔符只有 `\n`**（RFC 8259 / JSON Lines 约定）：读侧只按 `\n` 切（`_jsonl_lines()`，
  **不用 `str.splitlines()`** —— 它把 LS/PS/NEL 当行界，会让含这些码位的记录"写得出、读不回"）；
  写侧把 LS(U+2028)/PS(U+2029)/NEL(U+0085) 转义成 `\\uXXXX`（`_escape_container_json()`，无损，
  只作用于容器 JSONL 行与 manifest，**不改 `store` 的 digest 口径**）。
* **I5 全有或全无**：**先整体校验**（成员表 + 逐成员 sha256 + 清单字节数 + 计数），任何一项不过就抛错且**不写库**；
  写库阶段包在 `Vault._tx()` 的单个事务里（store 的 `_tx` 支持嵌套），中途异常由 SQLite 回滚。
  ⚠️ 诚实边界：blob 本体是内容寻址的普通文件，写入不经 SQLite 事务 ⇒ 极端情况下可能留下**孤儿 blob**
  （无害：同名同内容，下次导入直接复用；库内不会有半条记录）。这一点不满足"文件系统级全有或全无"，
  已登记为已知边界，不在判据里冒充。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

FORMAT_VERSION = 1
FORMAT_NAME = "fcx"
EXPORTER_NAME = "freechat-vault"
DEFAULT_EXPORTER_VERSION = "0.1.0"

MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "CHECKSUMS.sha256"
#: 记录文件（顺序即写入顺序，也是 CHECKSUMS 顺序的一部分）
JSONL_NAMES = ("chats.jsonl", "contacts.jsonl", "messages.jsonl", "reactions.jsonl", "attachments.jsonl")
#: 导入时的写入顺序（外键依赖：先人、再会话、再消息、再回应、最后附件元数据）
IMPORT_ORDER = ("contacts", "chats", "messages", "reactions", "attachments")

FIXED_ZIP_DATE = (1980, 1, 1, 0, 0, 0)
MAX_MEMBER_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 64 * 1024 ** 3
MAX_MEMBERS = 1_000_000


class FcxError(Exception):
    """.fcx 相关错误基类。"""


class InvalidArchiveError(FcxError):
    """归档损坏/不合规（成员缺失、校验和不符、路径穿越、超限……）——**不写库**。"""


class FutureFormatError(FcxError):
    """`format_version` 高于本实现支持的版本 —— **不写库**，提示用户升级。"""


@dataclass
class ImportStats:
    imported: int = 0
    skipped_duplicate: int = 0
    failed: int = 0
    warnings: list[str] = field(default_factory=list)
    source_kind: str = "fcx"

    def as_dict(self) -> dict:
        return {
            "imported": self.imported,
            "skipped_duplicate": self.skipped_duplicate,
            "failed": self.failed,
            "warnings": list(self.warnings),
            "source_kind": self.source_kind,
        }

    def merge(self, other: "ImportStats") -> "ImportStats":
        self.imported += other.imported
        self.skipped_duplicate += other.skipped_duplicate
        self.failed += other.failed
        self.warnings.extend(other.warnings)
        return self


# ------------------------------------------------------------------ 工具


def canonical_json(value: Any) -> str:
    """规范化 JSON：键升序、紧凑分隔符、不转义非 ASCII（规范 §3）。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


#: 容器的"伪行分隔符"：JSON **不要求**转义这三个码位，但 Python `str.splitlines()`、Java 的
#: `BufferedReader.readLine`、若干 JSONL 工具把它们**当行界** ⇒ 朴素 reader 会把一条记录切成两行。
#: 写侧把它们转成 `\uXXXX`（**无损**：`json.loads` 还原同一字符串），让别人也不再踩这个坑。
#: 只作用于**容器序列化**（JSONL 行 + manifest）；`store._canonical_json()` 的 digest 口径不动。
_CONTAINER_ESCAPES = (("\u0085", "\\u0085"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029"))


def _escape_container_json(text: str) -> str:
    """容器 JSON 的 LS/PS/NEL 转义（无该类码位时原样返回，保证既有归档逐字节不变）。"""
    if "\u0085" not in text and "\u2028" not in text and "\u2029" not in text:
        return text
    for char, escaped in _CONTAINER_ESCAPES:
        text = text.replace(char, escaped)
    return text


def _jsonl_lines(text: str) -> list:
    """JSONL 行切分：**只按 `\\n`**（RFC 8259 / JSON Lines 约定）。

    ⚠️ **不许用 `str.splitlines()`**：它把 LS(U+2028) / PS(U+2029) / NEL(U+0085) 也当行界，
    于是一条含这些码位的记录被切成两行 ⇒ 导出的归档"写得出、读不回"（2026-09-30 修的**数据丢失级**缺陷）。
    容忍 CRLF：行尾单个 `\\r` 去掉（与旧口径的**行号**一致，错误信息里的行号不变）。
    """
    return [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]


def _jsonl_bytes(records: list[dict]) -> bytes:
    if not records:
        return b""
    return ("\n".join(_escape_container_json(canonical_json(r)) for r in records) + "\n").encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _check_member_name(name: str) -> None:
    """zip-slip / 绝对路径 / 盘符 / 反斜杠 一律拒绝（规范 §6）。"""
    if not name or name.startswith("/") or name.startswith("\\"):
        raise InvalidArchiveError("非法成员名（绝对路径）：%r" % (name,))
    if "\\" in name:
        raise InvalidArchiveError("非法成员名（反斜杠）：%r" % (name,))
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise InvalidArchiveError("非法成员名（穿越/空段）：%r" % (name,))
    if len(name) > 3 and name[1] == ":":
        raise InvalidArchiveError("非法成员名（盘符）：%r" % (name,))


def _record_sort_key(name: str):
    """按规范 §3 的排序键（store 侧已按同一键排序，这里用于导出前兜底排序）。"""
    if name == "chats":
        return lambda r: (str(r.get("id", "")),)
    if name == "contacts":
        return lambda r: (str(r.get("id", "")),)
    if name == "messages":
        return lambda r: (str(r.get("chat_id", "")), str(r.get("sent_at", "")), str(r.get("id", "")))
    if name == "reactions":
        return lambda r: (str(r.get("message_id", "")), str(r.get("contact_id", "")),
                          str(r.get("emoji", "")), str(r.get("created_at", "")))
    if name == "attachments":
        return lambda r: (str(r.get("sha256", "")),)
    raise FcxError("未知记录类别：%r" % (name,))


# ------------------------------------------------------------------ 导出


def export_fcx(vault, out_path, *, exported_at: "str | None" = None,
               export_id: "str | None" = None, notes: str = "",
               exporter_version: str = DEFAULT_EXPORTER_VERSION,
               allow_missing_blobs: bool = False) -> dict:
    """把 vault 导出成 `.fcx`（原子写：先写临时文件再 rename）。返回**与归档内一致**的 manifest。

    附件本体缺失（元数据有、盘上没有）默认**报错**：那说明库本身不一致，导出成一个"引用不到的归档"
    只会把问题推到导入侧。确实要导出时用 `allow_missing_blobs=True`，此时**对应的附件记录整体跳过**
    （保持 `counts` 与 `manifest.blobs` 自洽），缺失清单写进 `notes`。
    """
    out_path = os.fspath(out_path)
    records: dict[str, list[dict]] = {}
    for name in IMPORT_ORDER:
        records[name] = list(getattr(vault, "iter_" + name)())
        records[name].sort(key=_record_sort_key(name))

    blobs: list[dict] = []
    blob_payload: dict[str, bytes] = {}
    missing: list[str] = []
    kept_attachments: list[dict] = []
    for att in records["attachments"]:
        sha = att.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            raise FcxError("附件元数据缺少合法 sha256：%r" % (att,))
        try:
            data = vault.get_blob(sha)
        except Exception:
            missing.append(sha)
            continue
        kept_attachments.append(att)
        blob_payload[sha] = data
        blobs.append({
            "sha256": sha,
            "path": "blobs/%s/%s" % (sha[:2], sha),
            "bytes": len(data),
            "mime": att.get("mime", ""),
            "name": att.get("name", ""),
        })
    if missing and not allow_missing_blobs:
        raise FcxError(
            "库不一致：%d 个附件只有元数据没有本体（sha256=%s…）。"
            "如确实要导出缺本体的归档，请显式传 allow_missing_blobs=True"
            % (len(missing), missing[0][:12]))
    if missing:
        notes = (notes + "\n" if notes else "") + \
            "导出时跳过 %d 个缺本体的附件：%s" % (len(missing), ",".join(sorted(missing)))
    records["attachments"] = kept_attachments
    blobs.sort(key=lambda b: b["path"])

    files = []
    payloads: dict[str, bytes] = {}
    for filename in JSONL_NAMES:
        key = filename[:-6]                      # "messages.jsonl" -> "messages"
        data = _jsonl_bytes(records[key])
        payloads[filename] = data
        files.append({"path": filename, "bytes": len(data), "sha256": _sha256_hex(data)})

    manifest = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "exporter": {"name": EXPORTER_NAME, "version": str(exporter_version)},
        "export_id": export_id or str(uuid.uuid4()),
        "exported_at": exported_at or _now_iso(),
        "timezone": datetime.now().astimezone().tzname() or "UTC",
        "counts": {k: len(v) for k, v in records.items()},
        "files": files,
        "blobs": blobs,
        "notes": notes,
    }
    manifest_bytes = (_escape_container_json(canonical_json(manifest)) + "\n").encode("utf-8")

    checksum_lines = ["%s  %s" % (f["sha256"], f["path"]) for f in files]
    for blob in blobs:
        checksum_lines.append("%s  %s" % (blob["sha256"], blob["path"]))
    checksums_bytes = ("\n".join(checksum_lines) + "\n").encode("utf-8")

    directory = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-fcx-", suffix=".part")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            _zip_add(zf, MANIFEST_NAME, manifest_bytes)
            for filename in JSONL_NAMES:
                _zip_add(zf, filename, payloads[filename])
            _zip_add(zf, CHECKSUMS_NAME, checksums_bytes)
            for blob in blobs:
                _zip_add(zf, blob["path"], blob_payload[blob["sha256"]])
        with open(tmp_path, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp_path, out_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise

    manifest = dict(manifest)
    return manifest


def _zip_add(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_DATE)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.create_system = 3                       # 固定为 Unix，避免平台差异
    zf.writestr(info, data)


# ------------------------------------------------------------------ 归档读取与校验


class FcxArchive:
    """只读、已校验的 `.fcx` 视图。构造即完成**全部校验**（I5 的前半段）。"""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = os.fspath(path)
        try:
            self._zf = zipfile.ZipFile(self.path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise InvalidArchiveError("无法打开归档：%s" % exc) from None
        try:
            self._validate()
        except BaseException:
            self._zf.close()
            raise

    # —— 校验 ——

    def _validate(self) -> None:
        infos = self._zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise InvalidArchiveError("成员数超限：%d > %d" % (len(infos), MAX_MEMBERS))
        total = 0
        names = set()
        for info in infos:
            _check_member_name(info.filename)
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise InvalidArchiveError("拒绝符号链接成员：%r" % (info.filename,))
            if info.file_size > MAX_MEMBER_BYTES:
                raise InvalidArchiveError("单成员超限：%r %d 字节" % (info.filename, info.file_size))
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise InvalidArchiveError("归档总体积超限：> %d 字节" % MAX_TOTAL_BYTES)
            names.add(info.filename)

        required = {MANIFEST_NAME, CHECKSUMS_NAME} | set(JSONL_NAMES)
        missing = required - names
        if missing:
            raise InvalidArchiveError("缺少必需成员：%s" % ", ".join(sorted(missing)))

        try:
            manifest = json.loads(self._zf.read(MANIFEST_NAME).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidArchiveError("manifest.json 不是合法 UTF-8 JSON：%s" % exc) from None
        if not isinstance(manifest, dict):
            raise InvalidArchiveError("manifest.json 顶层必须是对象")
        if manifest.get("format") != FORMAT_NAME:
            raise InvalidArchiveError("format 不是 %r：%r" % (FORMAT_NAME, manifest.get("format")))
        version = manifest.get("format_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise InvalidArchiveError("format_version 必须是整数：%r" % (version,))
        if version > FORMAT_VERSION:
            raise FutureFormatError(
                "归档 format_version=%d 高于本实现支持的 %d，请升级 freechat（不写库）"
                % (version, FORMAT_VERSION))
        if version < 1:
            raise InvalidArchiveError("format_version 必须 >= 1：%r" % (version,))
        self.manifest = manifest
        self._version = version

        # 逐成员 sha256 + 字节数（CHECKSUMS 覆盖 §1 的成员 2–7）
        self._checksums = self._parse_checksums(names)
        for filename in JSONL_NAMES:
            data = self._read_verified(filename)
            actual = data.count(b"\n")
            expected = (manifest.get("counts") or {}).get(filename[:-6])
            if isinstance(expected, int) and expected != actual:
                raise InvalidArchiveError(
                    "计数不符：%s 实际 %d 行，manifest 声明 %d" % (filename, actual, expected))

        for entry in manifest.get("files") or []:
            if not isinstance(entry, dict) or "path" not in entry:
                raise InvalidArchiveError("manifest.files 元素必须是含 path 的对象")
            data = self._read_verified(entry["path"])
            if entry.get("bytes") != len(data):
                raise InvalidArchiveError("manifest.files[%s] 字节数不符" % entry["path"])
            if entry.get("sha256") != _sha256_hex(data):
                raise InvalidArchiveError("manifest.files[%s] sha256 不符" % entry["path"])

        for blob in manifest.get("blobs") or []:
            if not isinstance(blob, dict):
                raise InvalidArchiveError("manifest.blobs 元素必须是对象")
            sha = blob.get("sha256")
            expected_path = "blobs/%s/%s" % (str(sha)[:2], sha)
            if blob.get("path") != expected_path:
                raise InvalidArchiveError("blob 路径不符合内容寻址约定：%r" % (blob.get("path"),))
            if expected_path not in names:
                raise InvalidArchiveError("manifest 声明的 blob 不在归档里：%s" % expected_path)
            data = self._read_verified(expected_path)
            if _sha256_hex(data) != sha:
                raise InvalidArchiveError("blob 内容与 sha256 不符：%s" % expected_path)
            if blob.get("bytes") != len(data):
                raise InvalidArchiveError("blob 字节数不符：%s" % expected_path)

        # 未被 CHECKSUMS 覆盖的业务成员（除 manifest/CHECKSUMS 自己）一律拒绝
        for name in names - {MANIFEST_NAME, CHECKSUMS_NAME}:
            if name not in self._checksums:
                raise InvalidArchiveError("成员未列入 CHECKSUMS.sha256：%s" % name)

    def _parse_checksums(self, names: set) -> dict:
        try:
            text = self._zf.read(CHECKSUMS_NAME).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidArchiveError("CHECKSUMS.sha256 不是 UTF-8：%s" % exc) from None
        table: dict[str, str] = {}
        for line in _jsonl_lines(text):
            line = line.strip()
            if not line:
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2 or len(parts[0]) != 64:
                raise InvalidArchiveError("CHECKSUMS.sha256 行格式非法：%r" % (line,))
            digest, name = parts
            _check_member_name(name)
            if name not in names:
                raise InvalidArchiveError("CHECKSUMS 指向不存在的成员：%s" % name)
            table[name] = digest
        if not table:
            raise InvalidArchiveError("CHECKSUMS.sha256 为空")
        return table

    def _read_verified(self, name: str) -> bytes:
        data = self._zf.read(name)
        expected = self._checksums.get(name)
        if expected is not None and _sha256_hex(data) != expected:
            raise InvalidArchiveError("成员 sha256 与 CHECKSUMS 不符：%s" % name)
        return data

    # —— 读取 ——

    def iter_jsonl(self, name: str) -> Iterator[dict]:
        filename = name if name.endswith(".jsonl") else name + ".jsonl"
        if filename not in JSONL_NAMES:
            raise FcxError("未知记录文件：%r" % (name,))
        data = self._read_verified(filename)
        for lineno, line in enumerate(_jsonl_lines(data.decode("utf-8")), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InvalidArchiveError("%s 第 %d 行不是合法 JSON：%s" % (filename, lineno, exc)) from None
            if not isinstance(rec, dict):
                raise InvalidArchiveError("%s 第 %d 行不是对象" % (filename, lineno))
            yield rec

    def read_blob(self, sha256: str) -> bytes:
        for blob in self.manifest.get("blobs") or []:
            if blob.get("sha256") == sha256:
                return self._read_verified(blob["path"])
        raise InvalidArchiveError("manifest 里没有这个 blob：%s" % (sha256,))

    def close(self) -> None:
        self._zf.close()

    def __enter__(self) -> "FcxArchive":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


# ------------------------------------------------------------------ 导入


def import_fcx(vault, path, *, dry_run: bool = False) -> ImportStats:
    """把 `.fcx` 导入 vault。

    **I5 全有或全无**：①结构校验（FcxArchive 构造）②**记录级预校验**（缺 id / 类型不对）都在写库前完成，
    任一不过即抛 `InvalidArchiveError` 且一个字都不写；③真正写库包在 `Vault._tx()` 单事务里，
    任何异常都由 SQLite 回滚 ⇒ 不会留下半条记录。
    """
    arch = FcxArchive(path)
    stats = ImportStats(source_kind="fcx")
    try:
        plan = {name: list(arch.iter_jsonl(name)) for name in IMPORT_ORDER}
        _prevalidate_records(plan)                      # 写库之前就把坏记录挡住（I5）
        total_records = sum(len(v) for v in plan.values())

        if dry_run:
            stats.warnings.append(
                "dry_run=1：归档已校验通过，计划导入 %d 条记录 / %d 个附件（未写库）"
                % (total_records, len(arch.manifest.get("blobs") or [])))
            return stats

        upsert_of = {
            "contacts": "upsert_contact",
            "chats": "upsert_chat",
            "messages": "upsert_message",
            "reactions": "upsert_reaction",
        }
        with _vault_tx(vault):
            before = vault.content_digest()
            existing_writes = 0                       # upsert 返回 False 的次数（= 覆盖了已存在记录）
            for name in IMPORT_ORDER[:-1]:
                upsert = getattr(vault, upsert_of[name])
                for rec in plan[name]:
                    if upsert(rec):
                        stats.imported += 1
                    else:
                        stats.skipped_duplicate += 1
                        existing_writes += 1
            for att in plan["attachments"]:
                sha = att["sha256"]
                data = arch.read_blob(sha)              # 缺失会在上面预校验时就被挡住
                existed = bool(vault.has_blob(sha))
                vault.put_blob(data, mime=att.get("mime", ""), name=att.get("name", ""),
                               ext=att.get("ext") or {})
                if existed:
                    stats.skipped_duplicate += 1
                else:
                    stats.imported += 1
            after = vault.content_digest()
        # 只有"确实覆盖了已存在记录"且"库内容真的变了"才告警（首次导入空库不得误报）
        if existing_writes > 0 and after != before:
            stats.warnings.append(
                "有 %d 条已存在的记录被覆盖且内容发生变化（LWW 语义，见 fcx-format §5.5）"
                % existing_writes)
    finally:
        arch.close()
    return stats


#: 各记录类别的必需键（写库前预校验，保证 I5：坏记录不会写进去一半）
_REQUIRED_KEYS = {
    "contacts": ("id",),
    "chats": ("id",),
    "messages": ("id", "chat_id", "sent_at"),
    "reactions": ("message_id", "contact_id", "emoji"),
    "attachments": ("sha256", "bytes"),
}


def _prevalidate_records(plan: dict) -> None:
    """写库前逐条预校验：缺必需键 / 键类型不对 / 附件 sha 不合法 ⇒ 抛错且不写库。"""
    for name, keys in _REQUIRED_KEYS.items():
        for index, rec in enumerate(plan[name]):
            for key in keys:
                value = rec.get(key)
                if value is None or (isinstance(value, str) and not value):
                    raise InvalidArchiveError(
                        "%s.jsonl 第 %d 条缺少必需键 %r（不写库）" % (name, index + 1, key))
                if not isinstance(value, (str, int)):
                    raise InvalidArchiveError(
                        "%s.jsonl 第 %d 条的 %r 类型非法：%r" % (name, index + 1, key, type(value).__name__))
    for index, att in enumerate(plan["attachments"]):
        sha = att.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise InvalidArchiveError(
                "attachments.jsonl 第 %d 条的 sha256 不是 64 位小写 hex：%r（不写库）" % (index + 1, sha))


def _vault_tx(vault):
    """拿 store 的事务上下文（支持嵌套 SAVEPOINT）；没有就退化为空上下文并留痕。"""
    tx = getattr(vault, "_tx", None)
    if callable(tx):
        return tx()
    return contextlib.nullcontext()
