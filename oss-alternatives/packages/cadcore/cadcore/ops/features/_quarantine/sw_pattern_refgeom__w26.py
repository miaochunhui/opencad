"""sw_pattern_refgeom 族切片 w26 —— must_have「阵列对象类型（特征/面/实体）」的 L1 真实现。

选号依据（本席只读两份清单文件，未改它们）：
- `docs/research/cad-parity/features/sw-pattern-refgeom.json` 的 `must_have` 共 20 条（0..19）；
- `docs/research/cad-parity/status/sw-pattern-refgeom.json` 的**逐项** `status` 里，
  仍 missing/partial 的 must_have 共 8 条（7 missing + 1 partial），按清单顺序编号 0..7：
  0 线性阵列第二方向 / 1 阵列对象类型（特征/面/实体） / 2 跳过与恢复实例 /
  3 圆周阵列角度范围 / 4 镜像实体 / 5 装配体镜像零部件 / 6 阵列/镜像的参数化重建 /
  7 质量属性(partial)；
- 本席第 17 号 ≥ 8 ⇒ 顺延取模 `17 % 8 = 1` ⇒ **阵列对象类型（特征/面/实体）**。
  （该 status 文件尾部的汇总计数 `implemented 0 / partial 1 / missing 19` 与逐项 `status`
  互相矛盾，且与同文件 evidence 指到的实际代码不符；本片按**逐项 status 字段**取号，
  并在 docstring 里明写，便于复核者换口径时重定位。）

L1 能力边界（诚实声明，不做桩、不静默降级）：
- `features`（真实现）：被引用特征 + 其驱动草图整体克隆为新文档节点，草图几何按实例偏移
  **真平移**（线/圆/弧/折线的坐标与点列都动），新特征 `inputs` 指向新草图，未压缩；
  docmodel 重建后每个实例各自成体 ⇒ 体的数量、总轮廓面积、包围盒都真的变了。
- `bodies`（真实现）：L1 每文档只有一个体 = **全部未压缩特征栈**；按实例克隆**整栈**
  （含各自草图，逐实例独立 uuid）⇒ 文档出现多个体。嵌套调用会把已阵列出的实例一起复制，
  这是"体 = 特征栈"定义的直接后果，不是缺陷（docstring 明写）。
- `faces`（**L1 装不下**）：docmodel 的 `entity` 是草图曲线，kernel 子形状
  （`edge.role='face-bottom'` 等）只是导出时的临时读数，没有可持久引用的面 uuid，
  也没有面级拓扑，故 `object_type="faces"` 一律
  `CadError(reason="face_object_type_unsupported_l1")` —— 报错而不是返回空表。
- `boolean` 特征不可阵列：它的 inputs 是形状而非草图（docmodel 重建时报
  `boolean_needs_shape_inputs`），照抄会造出重建不了的死特征 ⇒ 当场
  `CadError(reason="unsupported_feature_kind")`。

写面：本文件是文档级算子（不画三维、不改 kernel）。阵列记录写进文档根的
`x_sw_pattern_refgeom` 表（`x_` 前缀是 docmodel 认可的厂商扩展命名空间，不会平白多出
`unknown_field_preserved` 诊断）；返回值里同时给出记录 uuid 与新建特征 uuid，下游不必读表。
"""

from __future__ import annotations

import copy
import uuid as _uuid
from collections.abc import Mapping, Sequence
from typing import Any, Callable

if __package__:
    from cadcore.errors import CadError  # 绝对导入：_quarantine 深一层，`...errors` 会解析成 cadcore.ops.errors
else:  # 直接以脚本方式运行（python3 cadcore/ops/features/sw_pattern_refgeom__w26.py）时补包搜索路径
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
    from cadcore.errors import CadError

__all__ = ["FAMILY", "OPERATIONS", "pattern_object_type", "selftest", "main"]

FAMILY = "sw_pattern_refgeom"

#: 本片承载的 must_have（族内「仍 missing/partial」清单口径下的序号与状态）。
MUST_HAVE = "pattern object type (feature/face/body)"
MUST_HAVE_INDEX = 17           # 本席号
MUST_HAVE_LISTED_INDEX = 3     # must_have 清单里的原始下标（0 基）
MUST_HAVE_LOCAL_INDEX = 1      # 「仍 missing/partial」清单 0..7 里的下标（17 % 8）
MUST_HAVE_STATUS = "missing"

_TABLE_KEY = "x_sw_pattern_refgeom"
_BUCKETS = ("patterns", "datum_points", "datum_axes", "datum_planes", "measures")
_SUPPORTED_FEATURE_KINDS = ("extrude", "revolve")
_OBJECT_TYPE_ALIASES = {"feature": "features", "face": "faces", "body": "bodies",
                        "solid": "bodies", "bodies": "bodies", "features": "features",
                        "faces": "faces"}
_BODY_TOKENS = ("", "*", "body", "solid", "all")


# --------------------------------------------------------------------------------------
# 小工具（本片自包含：不依赖别的切片，也不依赖历史单体模块的私有函数）
# --------------------------------------------------------------------------------------


def _new_uuid() -> str:
    return _uuid.uuid4().hex


def _fail(reason: str, detail: str, **details: Any) -> "Any":
    """唯一失败构造点：文案 ASCII，`reason` 是机读归因入口。"""
    return CadError(detail, reason=reason, **details)


def _as_nm(value: Any, *, what: str) -> int:
    """长度一律整数纳米；bool/非数/非整数浮点/NaN ⇒ CadError（不静默取整）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_argument", "%s must be an integer length in nanometres" % what,
                    what=what, value=repr(value))
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")) or float(value) != int(value):
            raise _fail("invalid_argument", "%s must be an integer length in nanometres" % what,
                        what=what, value=repr(value))
        value = int(value)
    return int(value)


def _as_count(value: Any, *, what: str = "count") -> int:
    """实例数 → >= 2 的整数（`count == 1` 只是源自己，不是阵列 ⇒ 报错，不静默返 []）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", "%s must be an integer >= 2" % what, what=what,
                    value=repr(value))
    if value < 2:
        raise _fail("invalid_argument",
                    "%s must be >= 2: a pattern of one instance is the source itself" % what,
                    what=what, count=value)
    return value


def _step(*, dx_nm: Any, dy_nm: Any, spacing_nm: Any, axis: Any) -> tuple[int, int]:
    """实例步距（纳米）。三种写法：`dx_nm/dy_nm`、`spacing_nm=(dx, dy)`、`spacing_nm=<标量> + axis`。"""
    dx = _as_nm(dx_nm, what="dx_nm") if dx_nm is not None else 0
    dy = _as_nm(dy_nm, what="dy_nm") if dy_nm is not None else 0
    if spacing_nm is not None:
        if isinstance(spacing_nm, Sequence) and not isinstance(spacing_nm, (str, bytes)):
            pair = list(spacing_nm)
            if len(pair) != 2:
                raise _fail("invalid_argument", "spacing_nm must be a (dx_nm, dy_nm) pair",
                            spacing_nm=repr(spacing_nm))
            dx = _as_nm(pair[0], what="spacing_nm[0]")
            dy = _as_nm(pair[1], what="spacing_nm[1]")
        else:
            scalar = _as_nm(spacing_nm, what="spacing_nm")
            direction = "x" if axis is None else str(axis).strip().lower()
            if direction == "x":
                dx = scalar
            elif direction == "y":
                dy = scalar
            else:
                raise _fail("invalid_argument", "axis must be 'x' or 'y' when spacing_nm is a scalar",
                            axis=repr(axis))
    if dx == 0 and dy == 0:
        raise _fail("invalid_argument",
                    "dx_nm and dy_nm are both 0: the pattern would stack copies on the originals")
    return dx, dy


def _to_l1_document(value: Any) -> dict[str, Any] | None:
    """真 ``Document``（非 mapping）经 ``document_to_l1`` 转 L1 根映射；转不动返回 ``None``。

    返回口径随实现可能是 JSON 文本或映射，两种都认；认不出返回 ``None`` 由 :func:`_document`
    统一抛 ``document_required``（loud，不静默降级、不猜）。与同族 w23 的文档根口径一致。
    """
    import json

    try:
        from cadcore.docmodel import document_to_l1
    except Exception:  # pragma: no cover - docmodel 缺席 ⇒ 退化为"这不是文档根"
        return None
    try:
        payload = document_to_l1(value)
    except Exception:  # noqa: BLE001 - 草图/记录等非文档对象转不动是正常路径，交给上层 loud 报错
        return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    return payload if isinstance(payload, dict) else None


def _document(sk_or_doc: Any) -> dict[str, Any]:
    """L1 文档根：真 `Document` 先转 L1 映射；仍必须带 `features` 列表（传草图/对象一律 loud 报错，不猜）。"""
    if not isinstance(sk_or_doc, dict):
        converted = _to_l1_document(sk_or_doc)
        if converted is not None:
            sk_or_doc = converted
    if not isinstance(sk_or_doc, dict) or not isinstance(sk_or_doc.get("features"), list):
        raise _fail("document_required",
                    "a document root mapping with a 'features' list is required for feature/body patterns",
                    got=type(sk_or_doc).__name__)
    return sk_or_doc


def _feature_map(doc: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in doc.get("features") or []:
        if isinstance(item, dict) and item.get("uuid") is not None:
            out[str(item["uuid"])] = item
    return out


def _sketch_map(doc: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in doc.get("sketches") or []:
        if isinstance(item, dict) and item.get("uuid") is not None:
            out[str(item["uuid"])] = item
    return out


def _family_table(sk_or_doc: dict[str, Any]) -> dict[str, Any]:
    table = sk_or_doc.get(_TABLE_KEY)
    if not isinstance(table, dict):
        table = {}
        sk_or_doc[_TABLE_KEY] = table
    table.setdefault("schema", 1)
    for bucket in _BUCKETS:
        if not isinstance(table.get(bucket), list):
            table[bucket] = []
    return table


def _record(sk_or_doc: dict[str, Any], bucket: str, record: Mapping[str, Any]) -> str:
    uuid_str = _new_uuid()
    payload = dict(record)
    payload["uuid"] = uuid_str
    _family_table(sk_or_doc)[bucket].append(payload)
    return uuid_str


# --------------------------------------------------------------------------------------
# 几何克隆（真平移：坐标与点列都动，半径/角度/尺寸值不动）
# --------------------------------------------------------------------------------------


def _translate_geom(geom: Any, dx: int, dy: int) -> None:
    """把一条实体的几何按 (dx, dy) 平移（纳米）；认得的键才动，认不出的一律不动、不猜。"""
    if not isinstance(geom, dict):
        return
    for key, delta in (("x1", dx), ("x2", dx), ("cx", dx), ("y1", dy), ("y2", dy), ("cy", dy)):
        if key in geom:
            geom[key] = _as_nm(geom[key], what="geom.%s" % key) + delta
    points = geom.get("points_nm")
    if isinstance(points, Sequence) and not isinstance(points, (str, bytes)):
        moved: list[list[int]] = []
        for point in points:
            if not (isinstance(point, Sequence) and not isinstance(point, (str, bytes))
                    and len(point) == 2):
                raise _fail("invalid_argument", "points_nm entries must be [x_nm, y_nm] pairs",
                            point=repr(point))
            moved.append([_as_nm(point[0], what="points_nm.x") + dx,
                          _as_nm(point[1], what="points_nm.y") + dy])
        geom["points_nm"] = moved


def _clone_sketch(sketch: Mapping[str, Any], dx: int, dy: int) -> dict[str, Any]:
    """整草图深拷贝：新 uuid（草图/实体/约束/尺寸都换，防串号），实体几何真平移，求解缓存作废。"""
    clone: dict[str, Any] = copy.deepcopy(dict(sketch))
    clone["uuid"] = _new_uuid()
    for bucket in ("entities", "constraints", "dimensions"):
        items = clone.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item["uuid"] = _new_uuid()
            if bucket == "entities":
                _translate_geom(item.get("geom"), dx, dy)
    solved = clone.get("solved")
    if isinstance(solved, dict):
        solved.pop("cache", None)          # 几何已平移，旧求解结果不再有效
        solved["converged"] = False
    return clone


# --------------------------------------------------------------------------------------
# 引用解析与预校验（先全量校验再落地 ⇒ 失败调用不留半成品）
# --------------------------------------------------------------------------------------


def _resolve_feature_refs(doc: Mapping[str, Any], refs: Any,
                          features: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(refs, str):
        items: list[Any] = [refs]
    elif isinstance(refs, Sequence) and not isinstance(refs, (bytes, bytearray)):
        items = list(refs)
    else:
        raise _fail("invalid_argument", "refs must be a reference or a sequence of references",
                    refs=repr(refs))
    if not items:
        raise _fail("invalid_argument", "refs must not be empty")
    sketches = _sketch_map(doc)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in items:
        key = str(ref)
        if key in seen:
            raise _fail("duplicate_reference", "the same reference appears twice", ref=key)
        seen.add(key)
        feature = features.get(key)
        if feature is None:
            detail = ("reference names a sketch, not a feature" if key in sketches
                      else "feature reference not found in document")
            raise _fail("unknown_reference", detail, ref=key)
        out.append(feature)
    return out


def _validate_features(features: Sequence[Mapping[str, Any]],
                       sketches: Mapping[str, dict[str, Any]]) -> None:
    for feature in features:
        feature_uuid = str(feature.get("uuid"))
        kind = str(feature.get("kind") or "").strip().lower()
        if kind not in _SUPPORTED_FEATURE_KINDS:
            raise _fail("unsupported_feature_kind",
                        "only extrude/revolve features are sketch-driven and can be patterned on L1 "
                        "(boolean inputs are shapes, not sketches)",
                        kind=kind, feature_uuid=feature_uuid)
        if feature.get("suppressed"):
            raise _fail("feature_suppressed", "a suppressed feature cannot be patterned",
                        feature_uuid=feature_uuid)
        inputs = feature.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise _fail("feature_input_missing", "feature has no sketch input to clone",
                        feature_uuid=feature_uuid)
        for ref in inputs:
            if str(ref) not in sketches:
                raise _fail("unknown_reference",
                            "feature input does not resolve to a sketch in this document",
                            ref=str(ref), feature_uuid=feature_uuid)


def _body_stack(doc: Mapping[str, Any], refs: Any,
                features: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """体 = 文档里**全部未压缩特征**（L1 每文档一个体；点名的特征只用于确认选的是哪个体）。"""
    whole = [item for item in doc.get("features") or [] if isinstance(item, dict)]
    stack = [item for item in whole if not item.get("suppressed")]
    if not stack:
        raise _fail("no_solid_feature", "document has no unsuppressed feature to pattern as a body")
    if refs is None:
        return stack
    if isinstance(refs, str) and refs.strip().lower() in _BODY_TOKENS:
        return stack
    selected = _resolve_feature_refs(doc, refs, features)
    for feature in selected:
        if feature not in stack:
            raise _fail("feature_suppressed",
                        "referenced feature is suppressed and is not part of the document body",
                        feature_uuid=str(feature.get("uuid")))
    return stack


def _clone_instance(doc: dict[str, Any], stack: Sequence[Mapping[str, Any]],
                    sketches: Mapping[str, dict[str, Any]], dx: int, dy: int) -> list[str]:
    """一个实例 = 整栈特征各克隆一份（每个特征配一份它自己的新草图，几何平移 (dx, dy)）。"""
    created: list[str] = []
    for feature in stack:
        new_inputs: list[str] = []
        for ref in feature.get("inputs") or []:
            source = sketches.get(str(ref))
            if source is None:  # 预校验已挡住；这里再挡一次，宁可 loud 也不落地半个实例
                raise _fail("unknown_reference",
                            "feature input does not resolve to a sketch in this document",
                            ref=str(ref), feature_uuid=str(feature.get("uuid")))
            sketch_clone = _clone_sketch(source, dx, dy)
            doc.setdefault("sketches", []).append(sketch_clone)
            new_inputs.append(str(sketch_clone["uuid"]))
        clone: dict[str, Any] = copy.deepcopy(dict(feature))
        clone["uuid"] = _new_uuid()
        clone["inputs"] = new_inputs
        clone["suppressed"] = False
        doc["features"].append(clone)
        created.append(str(clone["uuid"]))
    return created


# --------------------------------------------------------------------------------------
# 操作：按对象类型阵列（特征 / 面 / 实体）
# --------------------------------------------------------------------------------------


def pattern_object_type(sk_or_doc: Any, *, refs: Any = None, object_type: Any = "features",
                        target_type: Any = None, count: Any = 2, dx_nm: Any = 0, dy_nm: Any = 0,
                        spacing_nm: Any = None, axis: Any = None) -> list[str]:
    """按「阵列对象类型」阵列：`features`（特征）/ `bodies`（实体）真实现，`faces` 在 L1 无落点。

    - `object_type`（别名 `target_type`）∈ {features, faces, bodies}，接受 feature/face/body/solid 简写；
    - `features`：`refs` = 特征 uuid（可多条，重复引用报错）；每个实例克隆被引用特征 + 其驱动草图，
      草图几何**真平移** `k * (dx, dy)`（`k = 1..count-1`，原位实例不动、也不返回）；
    - `bodies`：`refs` 省略（或 `"*"/"body"/"solid"/"all"`）= 整个文档体；给了 `refs` 就按特征 uuid
      确认选的是哪个体（L1 只有一个体），实例 = 整栈克隆；
    - `faces`：**报错** `CadError(reason="face_object_type_unsupported_l1")` —— L1 没有可持久引用的
      面子对象（docmodel 的 entity 是草图曲线，kernel 子形状没有面 uuid），不做桩；
    - `count < 2` / 非整数纳米 / 步距全 0 / 未知引用 / 草图冒充特征 / boolean 或已压缩特征
      ⇒ `CadError`；**校验先于落地**，失败调用不改文档（原子性）；
    - 返回 `[记录 uuid] + 新建特征 uuid...`（记录含 object_type/inputs/params/instances，落文档根
      `x_sw_pattern_refgeom.patterns`）。
    """
    doc = _document(sk_or_doc)
    raw_kind = target_type if target_type is not None else object_type
    kind = str(raw_kind if raw_kind is not None else "features").strip().lower()
    kind = _OBJECT_TYPE_ALIASES.get(kind, kind)
    if kind not in ("features", "faces", "bodies"):
        raise _fail("invalid_argument", "object_type must be one of features/faces/bodies",
                    object_type=kind)
    if kind == "faces":
        raise _fail("face_object_type_unsupported_l1",
                    "L1 has no addressable face objects: docmodel entities are sketch curves and "
                    "kernel sub-shapes carry no persistent face uuid, so a face pattern cannot be "
                    "built without a stub",
                    object_type=kind)

    total = _as_count(count)
    dx, dy = _step(dx_nm=dx_nm, dy_nm=dy_nm, spacing_nm=spacing_nm, axis=axis)
    features = _feature_map(doc)
    sketches = _sketch_map(doc)
    if kind == "features":
        stack = _resolve_feature_refs(doc, refs, features)
        inputs = [str(item.get("uuid")) for item in stack]
    else:
        stack = _body_stack(doc, refs, features)
        inputs = [str(item.get("uuid")) for item in stack]
    _validate_features(stack, sketches)

    created: list[str] = []
    for index in range(1, total):
        created.extend(_clone_instance(doc, stack, sketches, index * dx, index * dy))
    record = {"kind": "pattern_object_type", "object_type": kind, "inputs": inputs,
              "params": {"count": total, "object_type": kind, "spacing_nm": [dx, dy]},
              "instances": list(created), "skipped": []}
    return [_record(doc, "patterns", record)] + created


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "pattern_object_type": pattern_object_type,
}


# --------------------------------------------------------------------------------------
# 自检（真跑 + 硬断言；失败 loud 非零退出，不吞异常）
# --------------------------------------------------------------------------------------

_MM = 1_000_000


def _rect_sketch(sketch_uuid: str, x_nm: int, y_nm: int,
                 w_nm: int = 20 * _MM, h_nm: int = 10 * _MM) -> dict[str, Any]:
    points = [[x_nm, y_nm], [x_nm + w_nm, y_nm], [x_nm + w_nm, y_nm + h_nm],
              [x_nm, y_nm + h_nm], [x_nm, y_nm]]
    return {"uuid": sketch_uuid, "plane": "XY",
            "entities": [{"uuid": "e-" + sketch_uuid, "kind": "polyline", "layer": "0",
                          "construction": False, "geom": {"points_nm": points, "closed": True}}],
            "constraints": [], "dimensions": [], "solved": {"dof": 0, "converged": True,
                                                            "cache": {"legacy": True}}}


def _doc_with_rect() -> dict[str, Any]:
    return {"schema_version": 1, "unit": "mm", "meta": {"doc_uuid": "doc-1"},
            "sketches": [_rect_sketch("sk1", 0, 0)],
            "features": [{"uuid": "f1", "kind": "extrude", "inputs": ["sk1"],
                          "params": {"distance_nm": 10 * _MM, "symmetric": False},
                          "suppressed": False}]}


def _sketch_of(doc: Mapping[str, Any], sketch_uuid: Any) -> dict[str, Any]:
    for item in doc.get("sketches") or []:
        if str(item.get("uuid")) == str(sketch_uuid):
            return item
    raise AssertionError("sketch %r not found" % (sketch_uuid,))


def _profile_area_nm2(sketch: Mapping[str, Any]) -> int:
    """鞋带公式现算轮廓面积（整数纳米²；闭合环才计），与 kernel 无关但真读几何。"""
    total = 0
    for entity in sketch.get("entities") or []:
        points = (entity.get("geom") or {}).get("points_nm") or []
        if len(points) < 3:
            continue
        loop = [list(point) for point in points]
        if loop[0] != loop[-1]:
            loop.append(loop[0])
        twice = sum(loop[i][0] * loop[i + 1][1] - loop[i + 1][0] * loop[i][1]
                    for i in range(len(loop) - 1))
        total += abs(twice) // 2
    return total


def _bbox_nm(sketch: Mapping[str, Any]) -> tuple[int, int, int, int]:
    xs: list[int] = []
    ys: list[int] = []
    for entity in sketch.get("entities") or []:
        for point in (entity.get("geom") or {}).get("points_nm") or []:
            xs.append(int(point[0]))
            ys.append(int(point[1]))
    if not xs:
        raise AssertionError("sketch has no point geometry to bound")
    return min(xs), min(ys), max(xs), max(ys)


def _unsuppressed(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [item for item in doc.get("features") or []
            if isinstance(item, dict) and not item.get("suppressed")]


def selftest() -> int:
    """自检：features / bodies 真跑出几何后果，faces 与非法入参真报错且零副作用。"""
    checks: list[str] = []
    src_area = 200 * 10 ** 12          # 20 mm × 10 mm 轮廓 = 200 mm² = 2e14 nm²

    # ① features：克隆特征 + 驱动草图，几何真平移，原位不动
    doc = _doc_with_rect()
    assert _profile_area_nm2(doc["sketches"][0]) == src_area, _profile_area_nm2(doc["sketches"][0])
    out = pattern_object_type(doc, refs=["f1"], object_type="features", count=3,
                              spacing_nm=30 * _MM, axis="x")
    assert len(out) == 3, out                                    # 1 记录 + 2 新实例
    assert len(doc["features"]) == 3 and len(_unsuppressed(doc)) == 3, doc["features"]
    assert len(doc["sketches"]) == 3, len(doc["sketches"])
    new_features = [f for f in doc["features"] if f["uuid"] != "f1"]
    boxes = sorted(_bbox_nm(_sketch_of(doc, f["inputs"][0])) for f in new_features)
    assert boxes == [(30 * _MM, 0, 50 * _MM, 10 * _MM), (60 * _MM, 0, 80 * _MM, 10 * _MM)], boxes
    assert all(_profile_area_nm2(_sketch_of(doc, f["inputs"][0])) == src_area for f in new_features)
    assert len({f["inputs"][0] for f in new_features}) == 2, "instances must not share a sketch"
    assert all(str(f["inputs"][0]) != "sk1" for f in new_features)
    assert _bbox_nm(doc["sketches"][0]) == (0, 0, 20 * _MM, 10 * _MM), "source geometry must not move"
    assert len(doc["x_sw_pattern_refgeom"]["patterns"]) == 1
    assert doc["x_sw_pattern_refgeom"]["patterns"][0]["instances"] == out[1:]
    checks.append("features")

    # ② bodies：整栈克隆 ⇒ 体数、总轮廓面积、包围盒都真变
    doc2 = _doc_with_rect()
    out2 = pattern_object_type(doc2, object_type="bodies", count=2, dy_nm=100 * _MM)
    assert len(out2) == 2, out2                                  # 1 记录 + 1 个整栈克隆
    assert len(_unsuppressed(doc2)) == 2, doc2["features"]
    area_total = sum(_profile_area_nm2(_sketch_of(doc2, f["inputs"][0])) for f in _unsuppressed(doc2))
    assert area_total == 2 * src_area, area_total
    new_bbox = _bbox_nm(_sketch_of(doc2, [f for f in doc2["features"] if f["uuid"] != "f1"][0]["inputs"][0]))
    assert new_bbox == (0, 100 * _MM, 20 * _MM, 110 * _MM), new_bbox
    assert len({str(f["uuid"]) for f in doc2["features"]}) == 2, "body clones must get fresh uuids"
    checks.append("bodies")

    # ③ faces：L1 无面对象 ⇒ 必须 loud 报错，且文档一字不改（阴性对照）
    before = copy.deepcopy(doc2)
    try:
        pattern_object_type(doc2, object_type="faces", count=2, dx_nm=10 * _MM)
    except CadError as exc:
        assert exc.reason == "face_object_type_unsupported_l1", exc.as_dict()
    else:
        raise AssertionError("object_type='faces' was silently accepted")
    assert doc2 == before, "a failed call must not leave a half-built pattern"
    checks.append("faces_unsupported")

    # ④ 非法入参：全部报 CadError，且文档零副作用（原子性）
    doc3 = _doc_with_rect()
    snapshot = copy.deepcopy(doc3)
    cases = {
        "count_one": lambda: pattern_object_type(doc3, refs=["f1"], count=1, dx_nm=_MM),
        "zero_step": lambda: pattern_object_type(doc3, refs=["f1"], count=3, dx_nm=0, dy_nm=0),
        "ghost_ref": lambda: pattern_object_type(doc3, refs=["ghost"], count=2, dx_nm=_MM),
        "sketch_as_feature": lambda: pattern_object_type(doc3, refs=["sk1"], count=2, dx_nm=_MM),
        "float_nm": lambda: pattern_object_type(doc3, refs=["f1"], count=2, spacing_nm=1.5),
        "bad_object_type": lambda: pattern_object_type(doc3, object_type="loops", count=2, dx_nm=_MM),
    }
    reasons: dict[str, str] = {}
    for label, call in cases.items():
        try:
            call()
        except CadError as exc:
            reasons[label] = exc.reason
        else:
            raise AssertionError("bad input was accepted: %s" % label)
    assert reasons == {"count_one": "invalid_argument", "zero_step": "invalid_argument",
                       "ghost_ref": "unknown_reference", "sketch_as_feature": "unknown_reference",
                       "float_nm": "invalid_argument", "bad_object_type": "invalid_argument"}, reasons
    assert doc3 == snapshot, "failed calls must leave the document untouched"
    boolean_doc = _doc_with_rect()
    boolean_doc["features"].append({"uuid": "f2", "kind": "boolean", "inputs": ["sk1"],
                                    "params": {"op": "cut"}, "suppressed": False})
    try:
        pattern_object_type(boolean_doc, object_type="bodies", count=2, dx_nm=_MM)
    except CadError as exc:
        assert exc.reason == "unsupported_feature_kind", exc.as_dict()
    else:
        raise AssertionError("a boolean feature must not be silently patterned")
    checks.append("bad_inputs_atomicity")

    # ⑤ 接口自洽
    assert FAMILY == "sw_pattern_refgeom" and set(OPERATIONS) == {"pattern_object_type"}, OPERATIONS
    assert OPERATIONS["pattern_object_type"] is pattern_object_type
    checks.append("manifest")

    print("SELFTEST family=%s op=pattern_object_type checks=%d [%s] asserts>=2"
          % (FAMILY, len(checks), ",".join(checks)))
    print("FEATURE_OK family=sw_pattern_refgeom op=pattern_object_type verdict=PASS")
    return 0


def main() -> int:
    try:
        return selftest()
    except Exception as exc:  # noqa: BLE001 - 自检必须 loud 失败，不吞异常
        print("FEATURE_FAIL family=sw_pattern_refgeom op=pattern_object_type error=%s: %s"
              % (type(exc).__name__, exc))
        raise


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(main())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 sw_pattern_refgeom__w26.py:56 "阵列对象类型（特征/面/实体）" -> "pattern object type (feature/face/body)"
