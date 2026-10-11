"""dxf.reader —— DXF（R12 起）ASCII 文本 → `Document`。

DXF 的形状（一句话）
--------------------
文件是**「组码行 + 值行」成对出现**的纯文本::

    0            ← 组码行
    SECTION      ← 值行
    2
    ENTITIES
    0
    LINE
    8
    0
    10
    0.0
    ...
    0
    EOF

读取时**必须原样留住**的东西（本模块逐条实现，判据见 `docs/design/dxf-format-notes.md`）：

1. **行尾风格** —— ``\\r\\n`` / ``\\n`` / ``\\r`` / 末行无行尾，逐行记在 `Line.newline`；
2. **组码与值之间的空白形态** —— 两行都原样留着（`Tag.raw_code` / `Tag.raw_value`），
   只在**语义取值**时才 `strip()`；
3. **数值的原始字符串表示** —— ``1.0`` 就是 ``1.0``，不会被写成 ``1``（`Tag.value` 解析、
   `Tag.raw_value` 保原文，只有显式赋值才重写那一行）；
4. **段与实体的原始顺序** —— `Document.sections` / `Section.entities` 都是文件顺序，从不排序；
5. **未知段 / 未知实体 / XDATA（1001+）/ 代理对象（`ACAD_PROXY_ENTITY`）逐字节原样** ——
   认不出就只留原文，不解释、不丢弃（见 `dxf.model.Entity.is_unknown`）。

坏输入**响亮报错**（`DxfError`，带 1 基**物理行号**）：组码不是整数、组码后面缺值行、
`SECTION` 没有 `ENDSEC`、文件不是以 `0/EOF` 收尾（截断）。**绝不静默截断。**

编码：先按 UTF-8 解，不行再试 GBK，**每次都验证「解出来的文本能原样编回原字节」**，
都不过就落到 latin-1（字节到码位的 1:1 映射，永远可逆）⇒ 任何字节序列都能无损往返，
能正确解码时又能让中文摘要可检索。

只依赖标准库；本模块**不碰** `packages/vault`。
"""

from __future__ import annotations

import os

from dxf.model import (
    Document,
    DxfError,
    Entity,
    Line,
    Section,
    Table,
    Tag,
)

__all__ = [
    "CODEC_CANDIDATES",
    "read",
    "read_bytes",
    "parse_bytes",
    "parse_text",
    "decode_bytes",
    "split_lines",
    "parse_tags",
    "DxfError",
]

#: 解码候选（按顺序试，每个都要通过"能原样编回原字节"的验证；latin-1 永远兜底）
CODEC_CANDIDATES = ("utf-8", "gbk")
_FALLBACK_CODEC = "latin-1"

#: 实体后面跟随的子实体（R12：老式多段线与带属性的块引用）
SUBENTITY_FOLLOWERS = {
    "POLYLINE": ("VERTEX", "SEQEND"),
    "INSERT": ("ATTRIB", "SEQEND"),
}

_NEWLINES = ("\r\n", "\n", "\r")


# --------------------------------------------------------------------------------------
# 解码 / 切行
# --------------------------------------------------------------------------------------


def decode_bytes(raw: bytes) -> tuple:
    """字节 → `(text, codec)`；**保证 `text.encode(codec) == raw`**（否则换下一个候选）。"""
    for codec in CODEC_CANDIDATES:
        try:
            text = raw.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
        try:
            if text.encode(codec) == raw:
                return (text, codec)
        except UnicodeEncodeError:      # pragma: no cover - 解出来就一定编得回去
            continue
    return (raw.decode(_FALLBACK_CODEC, errors="strict"), _FALLBACK_CODEC)


def split_lines(text: str, *, start_index: int = 0) -> list:
    """按 ``\\r\\n`` / ``\\n`` / ``\\r`` 切行；**行尾原样留在 `Line.newline` 里**。

    末行没有行尾时 `newline=""`。这样 `"".join(line.render() …) == text` 恒成立。
    """
    lines: list = []
    position = 0
    index = start_index
    length = len(text)
    while position < length:
        found = -1
        newline = ""
        for candidate in _NEWLINES:
            hit = text.find(candidate, position)
            if hit >= 0 and (found < 0 or hit < found or (hit == found and len(candidate) > len(newline))):
                found, newline = hit, candidate
        if found < 0:
            lines.append(Line(text=text[position:], newline="", index=index))
            break
        lines.append(Line(text=text[position:found], newline=newline, index=index))
        position = found + len(newline)
        index += 1
    return lines


# --------------------------------------------------------------------------------------
# 标签流
# --------------------------------------------------------------------------------------


def _strip_trailing_blank(lines: list, start: int) -> list:
    """`lines[start:]` 是否全是空白行；是就返回它们（供"文件尾多余空行"宽容处理）。"""
    tail = lines[start:]
    for line in tail:
        if line.text.strip():
            return []
    return tail


def parse_tags(lines: list, *, warnings: "list | None" = None) -> list:
    """物理行 → 标签流。**坏输入在这里报错**（组码不是整数 / 缺值行）。"""
    tags: list = []
    index = 0
    total = len(lines)
    while index < total:
        code_line = lines[index]
        raw_code = code_line.text.strip()
        try:
            code = int(raw_code)
        except ValueError:
            tail = _strip_trailing_blank(lines, index)
            if tail and tags:
                if warnings is not None:
                    warnings.append(
                        "文件尾有 %d 行空白（第 %d 行起），已忽略并原样保留" % (len(tail), code_line.lineno))
                break
            if tail:
                raise DxfError(
                    "第 %d 行起全是空白行，文件里没有任何标签（空文件或只有空行）"
                    % code_line.lineno) from None
            raise DxfError(
                "第 %d 行：组码不是整数：%r（DXF 要求「组码行 + 值行」成对出现）"
                % (code_line.lineno, code_line.text)) from None
        if index + 1 >= total:
            raise DxfError(
                "第 %d 行：组码 %d 后面缺少值行（文件在此处结束 —— 像是被截断了）"
                % (code_line.lineno, code))
        tags.append(Tag(code=code, code_line=code_line, value_line=lines[index + 1]))
        index += 2
    return tags


def _tag_value_text(tag: Tag) -> str:
    value = tag.value
    return value.strip() if isinstance(value, str) else str(value).strip()


def _is_zero_tag(tag: Tag, name: str) -> bool:
    return tag.code == 0 and _tag_value_text(tag).upper() == name.upper()


# --------------------------------------------------------------------------------------
# 实体
# --------------------------------------------------------------------------------------


def _group_zero_tags(tags: list) -> tuple:
    """按组码 0 切分：返回 `([ [tag,…], … ], [散标签])`（散标签 = 首个 0 之前的）。"""
    groups: list = []
    orphans: list = []
    current: "list | None" = None
    for tag in tags:
        if tag.code == 0:
            if current is not None:
                groups.append(current)
            current = [tag]
        elif current is None:
            orphans.append(tag)
        else:
            current.append(tag)
    if current is not None:
        groups.append(current)
    return (groups, orphans)


def _entity_from_tags(tags: list, *, section_name: str, index: int = 0,
                      parent_type: str = "") -> Entity:
    kind = _tag_value_text(tags[0]) if tags else ""
    return Entity(type=kind, tags=list(tags), raw="".join(tag.render() for tag in tags),
                  section=section_name, index=index, parent_type=parent_type)


def parse_entities(tags: list, *, section_name: str = "ENTITIES") -> tuple:
    """一段标签 → `(实体列表, 散标签)`；`POLYLINE`/`INSERT` 的跟随子实体自动挂上。"""
    groups, orphans = _group_zero_tags(tags)
    flat = [_entity_from_tags(group, section_name=section_name) for group in groups]

    entities: list = []
    cursor = 0
    while cursor < len(flat):
        entity = flat[cursor]
        followers = SUBENTITY_FOLLOWERS.get(entity.type.upper())
        if not followers:
            entities.append(entity)
            cursor += 1
            continue
        children: list = []
        probe = cursor + 1
        while probe < len(flat) and flat[probe].type.upper() in followers:
            child = flat[probe]
            child.parent_type = entity.type
            children.append(child)
            probe += 1
            if child.type.upper() == "SEQEND":
                break
        entity.children = children
        entities.append(entity)
        cursor = probe if children else cursor + 1

    for seq, entity in enumerate(entities, 1):
        entity.index = seq
    return (entities, orphans)


def parse_tables(tags: list, *, section_name: str = "TABLES") -> tuple:
    """`TABLES` 段的标签 → `(表列表, 散标签)`；`0/TABLE` 与 `0/ENDTAB` 必须成对。"""
    tables: list = []
    orphans: list = []
    current: "list | None" = None
    for tag in tags:
        if _is_zero_tag(tag, "TABLE"):
            if current is not None:
                raise DxfError(
                    "第 %d 行：上一个 TABLE 没有闭合（缺少 0/ENDTAB）"
                    % current[0].code_line.lineno)
            current = [tag]
            continue
        if _is_zero_tag(tag, "ENDTAB"):
            if current is None:
                orphans.append(tag)
                continue
            current.append(tag)
            tables.append(current)
            current = None
            continue
        if current is None:
            orphans.append(tag)
        else:
            current.append(tag)
    if current is not None:
        raise DxfError(
            "第 %d 行：TABLE 没有闭合（缺少 0/ENDTAB）" % current[0].code_line.lineno)

    out: list = []
    for group in tables:
        name = ""
        body_start = 1
        if len(group) > 1 and group[1].code == 2:
            name = _tag_value_text(group[1])
            body_start = 2
        elif len(group) > 1 and group[1].code == 0:
            raise DxfError("第 %d 行：TABLE 后面缺少 2/<表名>" % group[0].code_line.lineno)
        body = group[body_start:-1]
        entries, entry_orphans = _group_zero_tags(body)
        out.append(Table(
            name=name,
            tags=list(group),
            entries=[_entity_from_tags(item, section_name=section_name) for item in entries],
            orphans=entry_orphans,
        ))
    return (out, orphans)


# --------------------------------------------------------------------------------------
# 段
# --------------------------------------------------------------------------------------


def _split_section_groups(tags: list) -> tuple:
    """把标签流切成 `(前导, [段标签组], 尾随)`；`SECTION`/`ENDSEC` 必须配对。"""
    preamble: list = []
    sections: list = []
    epilogue: list = []
    current: "list | None" = None
    seen_section = False
    for tag in tags:
        if _is_zero_tag(tag, "SECTION"):
            if current is not None:
                raise DxfError(
                    "第 %d 行：上一个 SECTION 没有闭合（缺少 0/ENDSEC）"
                    % current[0].code_line.lineno)
            current = [tag]
            seen_section = True
            continue
        if current is None:
            (epilogue if seen_section else preamble).append(tag)
            continue
        current.append(tag)
        if _is_zero_tag(tag, "ENDSEC"):
            sections.append(current)
            current = None
    if current is not None:
        raise DxfError(
            "第 %d 行：SECTION 没有闭合（缺少 0/ENDSEC）—— 文件像是被截断了"
            % current[0].code_line.lineno)
    return (preamble, sections, epilogue)


def _build_section(group: list, *, warnings: list) -> Section:
    head = group[0]
    if len(group) < 2 or group[1].code != 2:
        raise DxfError("第 %d 行：SECTION 后面缺少 2/<段名>" % head.code_line.lineno)
    name = _tag_value_text(group[1])
    body = group[2:-1] if len(group) >= 3 else []
    section = Section(name=name, tags=list(group), body_tags=list(body))
    upper = name.upper()
    if upper == "ENTITIES":
        section.entities, section.orphans = parse_entities(body, section_name=name)
    elif upper == "TABLES":
        section.tables, section.orphans = parse_tables(body, section_name=name)
    elif upper not in ("HEADER", "CLASSES", "BLOCKS", "OBJECTS"):
        warnings.append("未知段 %r（第 %d 行）—— 原样保留、不解释" % (name, head.code_line.lineno))
    return section


def _check_epilogue(epilogue: list, tags: list, warnings: list) -> None:
    """R12 要求文件以 `0/EOF` 收尾；不是就报错（**截断不许静默通过**）。"""
    eof = None
    for tag in epilogue:
        if _is_zero_tag(tag, "EOF"):
            eof = tag
    if eof is not None:
        # `0/EOF` 之后还有标签：原样留着，但要说一声（正常 R12 文件里 EOF 就是最后一个）
        after = [tag for tag in epilogue if tag is not eof]
        if after:
            warnings.append(
                "0/EOF（第 %d 行）之后还有 %d 个标签，已原样保留（正常 R12 文件里 EOF 是最后一个）"
                % (eof.value_line.lineno, len(after)))
        return
    tail = tags[-1] if tags else None
    if tail is None:
        raise DxfError("文件里没有任何标签（空文件）")
    raise DxfError(
        "文件在第 %d 行结束，最后一段是 %r —— 缺少 0/EOF 收尾标记（文件被截断？）"
        % (tail.value_line.lineno, _tag_value_text(tail)))


# --------------------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------------------


def parse_text(text: str, *, encoding: str = "utf-8", path: str = "",
               raw_bytes: "bytes | None" = None) -> Document:
    """解析已是文本的 DXF 内容（`raw_bytes` 传原字节可启用"未改 ⇒ 逐字节相等"自检）。"""
    bom = ""
    if text.startswith("\ufeff"):
        bom, text = "\ufeff", text[1:]
    lines = split_lines(text)
    warnings: list = []
    tags = parse_tags(lines, warnings=warnings)
    if not tags:
        raise DxfError("文件里没有任何标签（空文件或只有空行）：%s" % (path or "<内存>"))
    preamble, groups, epilogue = _split_section_groups(tags)
    if not groups:
        raise DxfError("文件里没有任何 SECTION 段：%s" % (path or "<内存>"))
    _check_epilogue(epilogue, tags, warnings)
    document = Document(
        path=path,
        encoding=encoding,
        bom=bom,
        lines=lines,
        raw_lines=[line.render() for line in lines],
        input_text=text,
        raw_bytes=raw_bytes,
        preamble=preamble,
        epilogue=epilogue,
        warnings=warnings,
    )
    document.sections = [_build_section(group, warnings=warnings) for group in groups]
    return document


def parse_bytes(raw: bytes, *, path: str = "") -> Document:
    """字节 → `Document`（自动选编码，**保证能原样编回**）。"""
    text, codec = decode_bytes(raw)
    return parse_text(text, encoding=codec, path=path, raw_bytes=raw)


def read_bytes(raw: bytes, *, path: str = "") -> Document:
    """`parse_bytes` 的别名（读侧惯用名）。"""
    return parse_bytes(raw, path=path)


def read(path: str) -> Document:
    """读一个 DXF 文件 → `Document`。

    坏输入抛 `DxfError`（带物理行号）；能读出来的东西**一个字都不丢**。
    """
    target = os.fspath(path)
    try:
        with open(target, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise DxfError("读不了这个文件：%s（%s）" % (target, exc)) from None
    if not raw:
        raise DxfError("文件是空的：%s" % target)
    return parse_bytes(raw, path=target)
