#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""annotation 族 w16 实现片：**多重引线（multi-leader）**。

对应清单条目
------------
`docs/research/cad-parity/features/autocad-annotation.json` 的 must_have 第 16 条
「多重引线」（status：`status/autocad-annotation.json` 判 partial：只有
`ops/annotation.py:435-444 add_leader` 的单引线 + 可选文案 key）。

本片补的正是那条 partial 的三个缺口：

1. **多引线集合 / 多引线段**：一条注解挂 N 条引线分支，每条分支是 ≥2 点的折线
   （不是只有一个箭头落点）；分支可以共用内容锚点（AutoCAD 多重引线的"多引线"语义）。
2. **内容块承载**：文字内容按 L1 口径落在注解本体（`text_key` 走语言包；`literal=True`
   时才允许人话字面量，且 `text` 与 `literal` 同层落 `args`）。
3. **引线样式关联**：样式名必须在本文件的样式表里解析得到（悬空 ⇒
   `CadError(reason="unknown_mleader_style")`），并把解析来源记进 `args["style_source"]`
   （`document` = 文档里真有这条样式；`implicit` = 文档尚无样式表，按缺省口径放行）。

真几何 / 真数据后果
-------------------
* 逐段长度用欧氏距离**真算**（`math.hypot`，四舍五入到整数纳米），零长段拒收
  （`degenerate_leader_segment`）—— 不复用调用方给的长度。
* 每条分支落 `branch_lengths_nm`，整条注解落 `total_length_nm = Σ 分支长`；
  多分支必须**汇聚到同一内容锚点**，否则 `leaders_do_not_converge`。
* `multileader_extend` 追加分支后**重算**全部长度账（不是追加一条记录就完事）。

冻结接口
--------
    FAMILY = "annotation"
    OPERATIONS: dict[str, Callable]        # op(sk_or_doc, **kwargs) -> list[str]
    非法参数 -> cadcore.errors.CadError(reason=<机器可读 token>)

口径：长度一律整数纳米（`bool`/浮点拒收）；本文件不含任何 locale 字面量
（字面量只出现在 `literal=True` 的调用方入参里）。

自测：`python3 cadcore/ops/features/annotation__w16.py`（末尾打
`FEATURE_OK family=annotation op=add_multileader verdict=PASS`）。
"""

from __future__ import annotations

import math
import os
import re
import sys
import uuid as _uuid
from collections.abc import Mapping
from typing import Any, Callable

# 直接以脚本方式运行（python3 cadcore/ops/features/annotation__w16.py）时 sys.path[0]
# 是 features/ 而不是包根 ⇒ 这里把"含 cadcore 包的目录"补进 sys.path。
_PROBE = os.path.dirname(os.path.abspath(__file__))
while True:
    if os.path.isdir(os.path.join(_PROBE, "cadcore")):
        if _PROBE not in sys.path:
            sys.path.insert(0, _PROBE)
        break
    _PARENT = os.path.dirname(_PROBE)
    if _PARENT == _PROBE:
        break
    _PROBE = _PARENT

from cadcore.errors import CadError
from cadcore.sketch.constraints import UnknownReference, find_entity

__all__ = ["FAMILY", "OPERATIONS", "add_multileader", "multileader_extend"]

FAMILY = "annotation"

#: 注解 kind（本片只落这一种）。
_MULTILEADER_KIND = "multileader"

#: i18n key 形状（与 `docmodel._KEY_LIKE_RE` 同源口径：点分 ASCII 标识符）。
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")

#: 样式记录的特征键（用来把"样式表"与"别的一堆带 name 的列表"区分开）。
_STYLE_KEYS = ("arrow_size_nm", "arrow_size", "height_nm", "text_height_nm", "font",
               "font_style", "grid_line_nm", "paper_height_nm", "width_factor", "slant_micro")


# --------------------------------------------------------------------------------------
# 校验器（全部在写入之前跑完）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(message, reason=reason, **details)


def _nm(value: Any, what: str, *, positive: bool = False) -> int:
    """长度入参 → 整数纳米（`bool` 与浮点一律拒）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_length", "%s must be an integer number of nanometres" % what)
    if positive and value <= 0:
        raise _fail("non_positive_length", "%s must be positive" % what)
    return value


def _point(value: Any, what: str) -> list[int]:
    """点入参 → `[x_nm, y_nm]`（整数纳米对）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("bad_position", "%s must be an (x_nm, y_nm) pair" % what)
    coords: list[int] = []
    for axis, item in zip(("x", "y"), value):
        if isinstance(item, bool) or not isinstance(item, int):
            raise _fail("bad_position", "%s.%s must be an integer number of nanometres"
                        % (what, axis))
        coords.append(item)
    return coords


def _branch(value: Any, what: str) -> list[list[int]]:
    """一条引线分支 → 折线点列（至少 2 点 ⇒ 至少一段）。"""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise _fail("bad_leader_branch", "%s must be a polyline of at least 2 points" % what)
    return [_point(item, "%s[%d]" % (what, index)) for index, item in enumerate(value)]


def _branches(value: Any, what: str) -> list[list[list[int]]]:
    """引线集合入参 → 分支列表（非空；每个分支至少一段）。"""
    if not isinstance(value, (list, tuple)) or not value:
        raise _fail("bad_leader_set", "%s must be a non-empty list of leader polylines" % what)
    return [_branch(item, "%s[%d]" % (what, index)) for index, item in enumerate(value)]


def _style_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("bad_style_name", "%s must be a non-empty style name" % what)
    return value


def _key(value: Any, what: str) -> str:
    """i18n key 入参 → 合法 key（人话文案不是 key）。"""
    if not isinstance(value, str) or not _KEY_RE.match(value):
        raise _fail("bad_text_key", "%s must be an i18n key (dotted ascii identifier)" % what)
    return value


def _ref(value: Any, what: str) -> list[str]:
    """单个引用 → `[uuid, sub_shape_name]`（L1 §C）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("bad_reference", "%s must be a (uuid, sub_shape_name) pair" % what)
    entity_uuid = value[0]
    if not isinstance(entity_uuid, str) or not entity_uuid:
        raise _fail("bad_uuid", "%s uuid must be a uuid string" % what)
    try:
        _uuid.UUID(entity_uuid)
    except (ValueError, AttributeError, TypeError) as exc:
        raise _fail("bad_uuid", "%s uuid is not a uuid: %r" % (what, entity_uuid)) from exc
    name = value[1]
    if not isinstance(name, str) or not name:
        raise _fail("bad_reference", "%s sub-shape name must be a non-empty string" % what)
    return [entity_uuid, name]


# --------------------------------------------------------------------------------------
# 文档载荷访问（裸草图没有 annotations[] ⇒ 明确报错，不静默丢弃）
# --------------------------------------------------------------------------------------


def _annotations(sk_or_doc: Any) -> list:
    """可写的 `annotations[]` 真源那一份（**不是**副本）。"""
    if isinstance(sk_or_doc, Mapping):
        bucket = sk_or_doc.get("annotations")
        if isinstance(bucket, list):
            return bucket
        if "entities" in sk_or_doc:
            raise _fail("annotation_needs_document",
                        "a bare sketch has no annotations[] to write; pass the document")
        raise _fail("bad_document", "document mapping has no annotations list")
    bucket = getattr(sk_or_doc, "annotations", None)
    if isinstance(bucket, list):
        return bucket
    raise _fail("bad_document", "unsupported sk_or_doc: %s" % type(sk_or_doc).__name__)


def _sketches(sk_or_doc: Any) -> list:
    if isinstance(sk_or_doc, Mapping):
        sketches = sk_or_doc.get("sketches")
        if isinstance(sketches, list) and sketches:
            return sketches
        if "entities" in sk_or_doc:
            return [sk_or_doc]
        return []
    sketches = getattr(sk_or_doc, "sketches", None)
    return list(sketches) if isinstance(sketches, list) else []


def _require_ref(sk_or_doc: Any, ref: list[str]) -> list[str]:
    """引用必须能在草图里解析到实体（悬空 ⇒ `unknown_reference`）。"""
    for sketch in _sketches(sk_or_doc):
        try:
            resolved = find_entity(sketch, ref[0])
        except UnknownReference:
            resolved = None
        if resolved is not None:
            return ref
    raise _fail("unknown_reference", "reference %s does not resolve to an entity" % ref[0])


# --------------------------------------------------------------------------------------
# 样式表解析（不硬编码 bucket 名：文档里任何"带 name 的样式记录表"都算数）
# --------------------------------------------------------------------------------------


def _style_tables(sk_or_doc: Any) -> list:
    """文档里的样式记录表：`[(bucket_key, records), ...]`（缺省口径下为空）。"""
    tables: list = []
    items = sk_or_doc.items() if isinstance(sk_or_doc, Mapping) else ()
    for key, value in items:
        if not isinstance(value, list) or not value:
            continue
        if not all(isinstance(item, Mapping) and isinstance(item.get("name"), str)
                   for item in value):
            continue
        styleish = "style" in str(key).lower() or any(
            field in item for item in value for field in _STYLE_KEYS
        )
        if styleish:
            tables.append((str(key), value))
    return tables


def _resolve_style(sk_or_doc: Any, name: str) -> str:
    """样式名 → 解析来源（`document` / `implicit`）；文档有样式表但缺这条 ⇒ 抛错。"""
    tables = _style_tables(sk_or_doc)
    for _bucket, records in tables:
        for record in records:
            if record.get("name") == name:
                return "document"
    if tables:
        raise _fail("unknown_mleader_style",
                    "style %r is not defined in the document style tables" % name)
    return "implicit"


# --------------------------------------------------------------------------------------
# 真几何：逐段长度与汇聚校验
# --------------------------------------------------------------------------------------


def _segments(points: list[list[int]], what: str) -> list[int]:
    """折线的逐段长度（整数纳米，真算）；零长段 ⇒ `degenerate_leader_segment`。"""
    lengths: list[int] = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length = int(round(math.hypot(float(delta_x), float(delta_y))))
        if length <= 0:
            raise _fail("degenerate_leader_segment",
                        "%s segment %d has zero length" % (what, index))
        lengths.append(length)
    return lengths


def _anchor_of(branches: list[list[list[int]]], at_nm: Any) -> list[int]:
    """内容锚点：多分支必须汇聚到同一点（可由 `at_nm` 点名）。"""
    ends = [branch[-1] for branch in branches]
    anchor = ends[0]
    if at_nm is not None:
        anchor = _point(at_nm, "at_nm")
    for index, end in enumerate(ends):
        if end != anchor:
            raise _fail("leaders_do_not_converge",
                        "leader branch %d ends at %r but the content anchor is %r"
                        % (index, end, anchor))
    return list(anchor)


def _geometry(branches: list[list[list[int]]]) -> dict:
    """引线集合 → 真几何账（逐段长 / 逐分支长 / 总长 / 箭头尖）。"""
    segments = [_segments(branch, "leaders[%d]" % index)
                for index, branch in enumerate(branches)]
    branch_lengths = [sum(items) for items in segments]
    return {
        "segments_nm": segments,
        "branch_lengths_nm": branch_lengths,
        "total_length_nm": sum(branch_lengths),
        "arrow_tips_nm": [list(branch[0]) for branch in branches],
    }


# --------------------------------------------------------------------------------------
# 冻结操作：新增 / 追加分支
# --------------------------------------------------------------------------------------


def add_multileader(sk_or_doc, *, leaders, text_key=None, literal=False, text=None,
                    style="STANDARD", target=None, at_nm=None, arrow_size_nm=None):
    """**多重引线**：N 条引线分支 + 一个内容块 + 一条引线样式关联。

    * `leaders`：引线集合，每条分支是 ≥2 点的折线（`[[(x_nm, y_nm), ...], ...]`）；
      所有分支**必须汇聚到同一内容锚点**（`at_nm` 可显式点名该锚点；不给就用各分支末点）。
    * `text_key` / `literal=True`+`text`：内容块（口径同 `annotation.add_text`）。
    * `style`：引线样式名，必须能在文档样式表里解析到（否则 `unknown_mleader_style`）。
    * `target`（可选）：箭头所指实体引用，悬空 ⇒ `unknown_reference`。
    * `arrow_size_nm`（可选）：正整数纳米，落 `args`。

    返回 `[annotation_uuid]`。
    """
    branches = _branches(leaders, "leaders")
    anchor = _anchor_of(branches, at_nm)
    geometry = _geometry(branches)
    if not isinstance(literal, bool):
        raise _fail("bad_flag", "literal must be a bool")
    style_name = _style_name(style, "style")
    style_source = _resolve_style(sk_or_doc, style_name)
    arrow_size = None if arrow_size_nm is None else _nm(arrow_size_nm, "arrow_size_nm",
                                                        positive=True)
    ref = None if target is None else _ref(target, "target")
    if ref is not None:
        _require_ref(sk_or_doc, ref)

    node: dict[str, Any] = {"uuid": str(_uuid.uuid4()), "kind": _MULTILEADER_KIND}
    if ref is not None:
        node["target"] = ref
    args: dict[str, Any] = {
        "leaders_nm": branches,
        "segments_nm": geometry["segments_nm"],
        "branch_lengths_nm": geometry["branch_lengths_nm"],
        "total_length_nm": geometry["total_length_nm"],
        "arrow_tips_nm": geometry["arrow_tips_nm"],
        "text_anchor_nm": anchor,
        "style": style_name,
        "style_source": style_source,
        "branch_count": len(branches),
    }
    if arrow_size is not None:
        args["arrow_size_nm"] = arrow_size
    if literal:
        if not isinstance(text, str) or not text.strip():
            raise _fail("bad_literal_text", "literal=True needs a non-empty text")
        node["literal"] = True
        args["text"] = text
        args["literal"] = True
    else:
        if text_key is None:
            raise _fail("missing_text_key", "a non-literal multileader needs an i18n text_key")
        node["text_key"] = _key(text_key, "text_key")
        node["literal"] = False
    node["args"] = args
    _annotations(sk_or_doc).append(node)
    return [node["uuid"]]


def multileader_extend(sk_or_doc, *, target, points):
    """给已有多重引线**追加一条引线分支**（真几何：全账重算），返回 `[annotation_uuid]`。

    `target` 是多引线注解 uuid；`points` 是新分支折线（≥2 点），其末点必须等于该注解
    现有的内容锚点（否则 `leaders_do_not_converge`）。
    """
    annotation_uuid = target
    if not isinstance(annotation_uuid, str) or not annotation_uuid:
        raise _fail("bad_uuid", "target must be a multileader annotation uuid string")

    node = None
    for item in _annotations(sk_or_doc):
        if isinstance(item, dict) and item.get("uuid") == annotation_uuid:
            node = item
            break
    if node is None:
        raise _fail("annotation_not_found", "no annotation with uuid %s" % annotation_uuid)
    if node.get("kind") != _MULTILEADER_KIND:
        raise _fail("not_a_multileader", "annotation %s is kind %r"
                    % (annotation_uuid, node.get("kind")))

    args = node.get("args")
    if not isinstance(args, dict):
        raise _fail("bad_multileader_node", "multileader %s has no args mapping"
                    % annotation_uuid)
    existing = args.get("leaders_nm")
    if not isinstance(existing, list) or not existing:
        raise _fail("bad_multileader_node", "multileader %s has no leader branches"
                    % annotation_uuid)
    anchor = args.get("text_anchor_nm")
    if not isinstance(anchor, list) or len(anchor) != 2:
        raise _fail("bad_multileader_node", "multileader %s has no content anchor"
                    % annotation_uuid)

    branch = _branch(points, "points")
    if branch[-1] != anchor:
        raise _fail("leaders_do_not_converge",
                    "new branch ends at %r but the content anchor is %r"
                    % (branch[-1], anchor))

    branches = [list(item) for item in existing] + [branch]
    geometry = _geometry(branches)
    args["leaders_nm"] = branches
    args["segments_nm"] = geometry["segments_nm"]
    args["branch_lengths_nm"] = geometry["branch_lengths_nm"]
    args["total_length_nm"] = geometry["total_length_nm"]
    args["arrow_tips_nm"] = geometry["arrow_tips_nm"]
    args["branch_count"] = len(branches)
    return [annotation_uuid]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "add_multileader": add_multileader,
    "multileader_extend": multileader_extend,
}


# --------------------------------------------------------------------------------------
# 自带 selftest（硬断言；`python3 cadcore/ops/features/annotation__w16.py`）
# --------------------------------------------------------------------------------------


def _selftest() -> list[tuple[str, bool, str]]:
    """返回 `[(用例名, 是否通过, 说明), ...]`；每条都是硬断言。"""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str) -> None:
        results.append((name, bool(condition), detail))

    def reason_of(exc: BaseException) -> str:
        return getattr(exc, "reason", "")

    doc: dict[str, Any] = {
        "annotations": [],
        "sketches": [],
        "mleader_styles": [{"name": "STD", "arrow_size_nm": 2_500_000}],
    }
    branch_a = [[0, 0], [10_000_000, 0], [20_000_000, 5_000_000]]
    branch_b = [[0, 10_000_000], [10_000_000, 10_000_000], [20_000_000, 5_000_000]]
    seg = 11_180_340           # hypot(10 mm, 5 mm) 四舍五入到纳米
    branch_len = 10_000_000 + seg
    total = 2 * branch_len     # 42_360_680

    returned = add_multileader(doc, leaders=[branch_a, branch_b], literal=True,
                              text="NOTE A", style="STD", arrow_size_nm=2_500_000)
    node = doc["annotations"][0] if doc["annotations"] else {}
    args = node.get("args", {}) if isinstance(node, dict) else {}
    check("add_multileader/basic",
          returned == [node.get("uuid")] and node.get("kind") == "multileader"
          and len(doc["annotations"]) == 1,
          "returned=%r kind=%r n=%d" % (returned, node.get("kind"), len(doc["annotations"])))
    check("add_multileader/geometry",
          args.get("segments_nm") == [[10_000_000, seg], [10_000_000, seg]]
          and args.get("branch_lengths_nm") == [branch_len, branch_len]
          and args.get("total_length_nm") == total
          and args.get("text_anchor_nm") == [20_000_000, 5_000_000]
          and args.get("arrow_tips_nm") == [[0, 0], [0, 10_000_000]]
          and args.get("branch_count") == 2,
          "segments=%r branch=%r total=%r anchor=%r"
          % (args.get("segments_nm"), args.get("branch_lengths_nm"),
             args.get("total_length_nm"), args.get("text_anchor_nm")))
    check("add_multileader/content+style",
          node.get("literal") is True and args.get("text") == "NOTE A"
          and args.get("literal") is True and args.get("style") == "STD"
          and args.get("style_source") == "document"
          and args.get("arrow_size_nm") == 2_500_000,
          "literal=%r style=%r source=%r"
          % (node.get("literal"), args.get("style"), args.get("style_source")))

    extra = [[30_000_000, 15_000_000], [20_000_000, 5_000_000]]
    extra_len = 14_142_136    # hypot(10 mm, 10 mm) 四舍五入到纳米
    extended = multileader_extend(doc, target=returned[0], points=extra)
    check("multileader_extend/recompute",
          extended == returned
          and len(args.get("leaders_nm", [])) == 3
          and args.get("branch_lengths_nm") == [branch_len, branch_len, extra_len]
          and args.get("total_length_nm") == total + extra_len
          and args.get("branch_count") == 3
          and len(doc["annotations"]) == 1,
          "extended=%r branch=%r total=%r"
          % (extended, args.get("branch_lengths_nm"), args.get("total_length_nm")))

    caught = ""
    try:
        add_multileader(doc, leaders=[[[0, 0], [0, 0], [5_000_000, 0]]],
                        literal=True, text="X", style="STD")
    except CadError as exc:
        caught = reason_of(exc)
    check("negative/degenerate_segment", caught == "degenerate_leader_segment",
          "reason=%r" % (caught,))

    caught = ""
    try:
        add_multileader(doc, leaders=[branch_a, [[0, 10_000_000], [3_000_000, 3_000_000]]],
                        text_key="note.leader", style="STD")
    except CadError as exc:
        caught = reason_of(exc)
    check("negative/not_converging", caught == "leaders_do_not_converge",
          "reason=%r" % (caught,))

    caught = ""
    try:
        add_multileader(doc, leaders=[branch_a, branch_b], text_key="note.leader",
                        style="NOPE")
    except CadError as exc:
        caught = reason_of(exc)
    check("negative/unknown_style", caught == "unknown_mleader_style", "reason=%r" % (caught,))

    caught = ""
    try:
        add_multileader(doc, leaders=[branch_a, branch_b], style="STD")
    except CadError as exc:
        caught = reason_of(exc)
    check("negative/missing_content_key", caught == "missing_text_key", "reason=%r" % (caught,))

    check("negative/no_partial_write",
          len(doc["annotations"]) == 1 and doc["annotations"][0]["uuid"] == returned[0],
          "n=%d" % (len(doc["annotations"]),))
    return results


def _main() -> int:
    results = _selftest()
    failures = [(name, detail) for name, ok, detail in results if not ok]
    print("SELFTEST family=%s op=add_multileader cases=%d failed=%d"
          % (FAMILY, len(results), len(failures)))
    for name, detail in failures:
        print("SELFTEST_FAIL %s: %s" % (name, detail))
    if failures:
        print("FEATURE_OK family=annotation op=add_multileader verdict=FAIL")
        return 2
    print("FEATURE_OK family=annotation op=add_multileader verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
