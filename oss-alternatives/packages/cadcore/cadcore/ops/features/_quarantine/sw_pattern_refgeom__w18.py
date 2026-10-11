#!/usr/bin/env python3
"""cadcore.ops.features.sw_pattern_refgeom__w18 —— 阵列对象类型（特征/面/实体）片。

一句话用途
----------
让**同一个阵列命令**按 ``seed_kind`` 决定「复制什么」：``feature`` 复制特征实例、
``face`` 只复制面片、``body`` 复制整个实体。本片落在 L1 文档模型上，几何克隆与变换
**不重写**，一律转发既有真实现 ``cadcore.ops.sw_pattern_refgeom`` 的 ``pattern_linear`` /
``pattern_circular`` / ``mirror_pattern``（它们自己解析 refs、克隆对象并施加仿射矩阵），
本文件只负责**分派、把关与如实降级**。

编号账（本席 sw_pattern_refgeom__w18 的自我交代）
------------------------------------------------
该族 must_have 共 20 条（``docs/research/cad-parity/features/sw-pattern-refgeom.json``
的 must_have 清单顺序）。按 ``docs/research/cad-parity/status/sw-pattern-refgeom.json``
的逐条 status 现读，仍 missing/partial 的为 8 条，按清单顺序编号 0..7：
  0 线性阵列第二方向 / 1 阵列对象类型（特征/面/实体）/ 2 跳过与恢复实例 /
  3 圆周阵列角度范围 / 4 镜像实体 / 5 装配体镜像零部件 / 6 阵列/镜像的参数化重建 / 7 质量属性
本席负责第 9 号：9 mod 8 = 1 ⇒ **阵列对象类型（特征/面/实体）**。

真做到哪一步（宁可红不许假绿）
------------------------------
- ``seed_kind="feature"``：真实现。refs 当可克隆对象交给既有阵列实现克隆/变换，返回新实例
  uuid；并在文档里留一条本片 provenance 记录（``sw_pattern_refgeom__w18`` 表），同时把
  ``target_type="feature"`` 写进阵列记录 —— 下游看得出这是「按特征阵列」。
- ``seed_kind="face"`` / ``"body"``：**L1 文档模型装不下**（docmodel 无面/多实体集合，kernel
  能力闭集里没有 face/body 阵列）⇒ 按规格显式抛 ``CadError(reason="unsupported_kind")``，
  **绝不静默当 feature 处理**；该条在 face/body 两个分支上如实留 missing，不冒充已实现。

冻结接口
--------
``FAMILY`` / ``OPERATIONS: dict[str, Callable]`` / ``op(sk_or_doc, **kwargs) -> list[str]``；
长度整数纳米；错误抛 ``cadcore.errors.CadError(reason=...)``。非 docstring 字符串一律 ASCII。

调用样例
--------
``pattern_seed_kind(doc, seed_kind="feature", seed_refs=[h1], pattern_op="linear",
count=3, spacing_nm=10000000, direction="x")`` ⇒ 2 个新实例 uuid（含 ``instances`` /
``direction_ref`` / ``spacing_nm`` 等别名，别名映射到既有阵列实现的参数名）。
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from typing import Any, Callable

__all__ = ["FAMILY", "OPERATIONS", "pattern_seed_kind", "selftest"]

FAMILY = "sw_pattern_refgeom"

SEED_KINDS = ("feature", "face", "body")
PATTERN_OPS = ("linear", "circular", "mirror")

_REALIZERS = {
    "linear": "pattern_linear",
    "circular": "pattern_circular",
    "mirror": "mirror_pattern",
}
# 规格层参数名 -> 既有阵列实现参数名（只做映射，不做静默丢弃）
_ALIASES = {
    "instances": "count",
    "instances_2": "count2",
    "seed_refs": "refs",
    "direction_ref": "direction",
    "direction_ref_2": "direction2",
    "spacing_nm_2": "spacing2_nm",
}
_TABLE_KEY = "sw_pattern_refgeom__w18"


# --------------------------------------------------------------------------------------
# 依赖装载（既能作为包被导入，也能直接被 python3 当脚本跑）
# --------------------------------------------------------------------------------------


def _ensure_cadcore_importable() -> None:
    """把 ``packages/cadcore`` 放上 sys.path，供脚本方式运行时 import cadcore。"""
    if "cadcore" in sys.modules:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    if root not in sys.path:
        sys.path.insert(0, root)


_LEGACY: Any = None
_LEGACY_TRIED = False


def _legacy() -> Any:
    """既有族实现模块；不可用时返回 ``None``（调用方必须显式报错，不静默降级）。"""
    global _LEGACY, _LEGACY_TRIED
    if _LEGACY_TRIED:
        return _LEGACY
    _LEGACY_TRIED = True
    try:
        from .. import sw_pattern_refgeom as mod  # type: ignore[attr-defined]

        _LEGACY = mod
        return _LEGACY
    except Exception:
        pass
    _ensure_cadcore_importable()
    try:
        _LEGACY = importlib.import_module("cadcore.ops.sw_pattern_refgeom")
    except Exception:
        _LEGACY = None
    return _LEGACY


def _error_class() -> Any:
    """``cadcore.errors.CadError``：绝对导入，失败即 loud，不再回落/吞错。"""
    from cadcore.errors import CadError

    return CadError


def _fail(reason: str, detail: str, **details: Any) -> BaseException:
    """构造 ``CadError``：优先走既有实现唯一构造点 ``_fail``，保证 reason 口径一致。"""
    legacy = _legacy()
    if legacy is not None and hasattr(legacy, "_fail"):
        try:
            return legacy._fail(reason, detail, **details)
        except Exception:
            pass
    cls = _error_class()
    try:
        return cls(reason=reason, detail=detail, **details)
    except TypeError:
        return cls(reason=reason)


# --------------------------------------------------------------------------------------
# 引用解析（uuid 是否真在文档里 —— 不靠输入自觉）
# --------------------------------------------------------------------------------------


def _has_uuid(sk_or_doc: Any, ref: Any) -> bool:
    """递归扫描文档（dict / list / 对象）判断 ``ref`` 这个 uuid 是否真的存在。"""
    stack = [sk_or_doc]
    budget = 200000
    while stack:
        budget -= 1
        if budget < 0:
            return False
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("uuid") == ref:
                return True
            stack.extend(node.values())
        elif isinstance(node, (list, tuple, set, frozenset)):
            stack.extend(node)
        elif getattr(node, "uuid", None) == ref:
            return True
    return False


def _as_ref_list(seed_refs: Any) -> list[str]:
    """``seed_refs`` -> 非空 uuid 字符串列表；坏入参当场 ``invalid_argument``。"""
    if isinstance(seed_refs, (str, bytes)) or seed_refs is None:
        raise _fail(
            "invalid_argument",
            "seed_refs must be a list of uuid strings, not a scalar: %s"
            % type(seed_refs).__name__,
            seed_refs=type(seed_refs).__name__,
        )
    try:
        refs = list(seed_refs)
    except TypeError:
        raise _fail(
            "invalid_argument",
            "seed_refs must be iterable: %s" % type(seed_refs).__name__,
        )
    if not refs:
        raise _fail("invalid_argument", "seed_refs is empty: nothing to pattern")
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            raise _fail("invalid_argument", "seed_refs entries must be non-empty strings")
    return refs


def _map_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """别名映射（``instances`` -> ``count`` 等）；同名冲突即报错，不静默覆盖。"""
    mapped: dict[str, Any] = {}
    for key, value in kwargs.items():
        target = _ALIASES.get(key, key)
        if target in mapped:
            raise _fail(
                "invalid_argument",
                "duplicate pattern parameter after alias mapping: %s -> %s" % (key, target),
                key=key,
            )
        mapped[target] = value
    return mapped


def _record_provenance(sk_or_doc: Any, record: dict[str, Any]) -> None:
    """在本片自己的表里留证：谁按什么 seed_kind 阵列了哪些实例（dict 文档为主）。"""
    if isinstance(sk_or_doc, dict):
        table = sk_or_doc.get(_TABLE_KEY)
        if not isinstance(table, dict):
            table = {"schema": 1, "records": []}
            sk_or_doc[_TABLE_KEY] = table
        records = table.get("records")
        if not isinstance(records, list):
            records = []
            table["records"] = records
        records.append(record)
        return
    table = getattr(sk_or_doc, _TABLE_KEY, None)
    if not isinstance(table, dict):
        table = {"schema": 1, "records": []}
        try:
            setattr(sk_or_doc, _TABLE_KEY, table)
        except Exception:
            return
    records = table.get("records")
    if not isinstance(records, list):
        records = []
        table["records"] = records
    records.append(record)


# --------------------------------------------------------------------------------------
# 操作：阵列表述「对象类型」分派
# --------------------------------------------------------------------------------------


def pattern_seed_kind(
    sk_or_doc: Any,
    *,
    seed_kind: str,
    seed_refs: Any,
    pattern_op: str,
    **kwargs: Any,
) -> list[str]:
    """按 ``seed_kind`` 分派同一条阵列命令，返回新实例 uuid 列表。

    - ``feature``：交给既有阵列实现（``pattern_linear`` / ``pattern_circular`` /
      ``mirror_pattern``）真克隆真变换，``seed_refs`` 指向的对象必须在文档里存在；
    - ``face`` / ``body``：L1 文档模型无面/多实体集合 ⇒ ``CadError(reason="unsupported_kind")``，
      显式降级，不静默当 feature；
    - 其余关键字（``count`` / ``instances`` / ``spacing_nm`` / ``direction`` / ``angle_deg`` /
      ``p1_nm`` / ``p2_nm`` …）按别名映射后原样转给既有实现；它不接受的参数报
      ``invalid_argument``，不静默丢弃。
    """
    if not isinstance(seed_kind, str) or seed_kind not in SEED_KINDS:
        raise _fail(
            "invalid_argument",
            "seed_kind must be one of feature|face|body: %r" % (seed_kind,),
            seed_kind=seed_kind,
        )
    if not isinstance(pattern_op, str) or pattern_op not in PATTERN_OPS:
        raise _fail(
            "invalid_argument",
            "pattern_op must be one of linear|circular|mirror: %r" % (pattern_op,),
            pattern_op=pattern_op,
        )
    refs = _as_ref_list(seed_refs)

    if seed_kind != "feature":
        raise _fail(
            "unsupported_kind",
            "L1 docmodel has no face/body collection: seed_kind=%s cannot be patterned as its "
            "own kind (explicit degrade, not a silent feature fallback)" % seed_kind,
            seed_kind=seed_kind,
        )

    legacy = _legacy()
    if legacy is None:
        raise _fail(
            "unsupported_kind",
            "realizer module cadcore.ops.sw_pattern_refgeom unavailable: cannot pattern features",
        )
    name = _REALIZERS[pattern_op]
    realizer = getattr(legacy, name, None)
    if not callable(realizer):
        raise _fail(
            "unsupported_kind",
            "no realizer %s for pattern_op=%s" % (name, pattern_op),
            pattern_op=pattern_op,
        )

    for ref in refs:
        if not _has_uuid(sk_or_doc, ref):
            raise _fail(
                "unknown_reference",
                "seed_ref is not present in the document",
                ref=ref,
                seed_kind=seed_kind,
            )

    params = inspect.signature(realizer).parameters
    accepts_extra = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    call_kwargs = _map_kwargs(kwargs)
    unknown = [k for k in call_kwargs if k not in params and not accepts_extra]
    if unknown:
        raise _fail(
            "invalid_argument",
            "unsupported pattern parameter(s) for pattern_op=%s" % pattern_op,
            unknown=sorted(unknown),
            pattern_op=pattern_op,
        )
    call_kwargs["refs"] = list(refs)
    if "target_type" in params and "target_type" not in call_kwargs:
        call_kwargs["target_type"] = "feature"
    if "features" in params and "features" not in call_kwargs:
        call_kwargs["features"] = list(refs)

    instances = realizer(sk_or_doc, **call_kwargs)
    if not isinstance(instances, (list, tuple)):
        raise _fail(
            "unsupported_kind",
            "realizer %s did not return a uuid list" % name,
            pattern_op=pattern_op,
        )
    out = list(instances)
    for uuid in out:
        if not isinstance(uuid, str) or not uuid:
            raise _fail(
                "unsupported_kind",
                "realizer %s returned a non-uuid entry" % name,
                pattern_op=pattern_op,
            )
    if not out:
        raise _fail(
            "invalid_argument",
            "pattern produced no new instance: check count/spacing parameters",
            pattern_op=pattern_op,
        )
    missing = [uuid for uuid in out if not _has_uuid(sk_or_doc, uuid)]
    if missing:
        raise _fail(
            "unsupported_kind",
            "patterned instances are not present in the document",
            missing=missing[:4],
            pattern_op=pattern_op,
        )

    _record_provenance(
        sk_or_doc,
        {
            "seed_kind": seed_kind,
            "pattern_op": pattern_op,
            "seed_refs": list(refs),
            "instances": out,
        },
    )
    return out


OPERATIONS: dict[str, Callable[..., list[str]]] = {"pattern_seed_kind": pattern_seed_kind}


# --------------------------------------------------------------------------------------
# 自测（硬断言；失败即 FAIL，绝不打印 PASS）
# --------------------------------------------------------------------------------------


def _expect_reason(expected: str, fn: Callable[[], Any]) -> None:
    """硬断言：调用必须抛 CadError 且 reason 命中。"""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - 只接受带 reason 的 CadError
        got = getattr(exc, "reason", None)
        if got is None:
            text = str(exc)
            got = expected if expected in text else "<no-reason-attr>"
        if got != expected:
            raise AssertionError("expected reason=%s got %s (%s)" % (expected, got, exc))
        print("SELFTEST_ASSERT ok=reason_%s" % expected)
        return
    raise AssertionError("expected CadError(reason=%s), got %r" % (expected, result))


def selftest() -> int:
    """至少 4 条硬断言：错误路径 3 条 + 真几何/真数据后果 4 条。"""
    seed = "w18-seed-0001"
    empty_doc: dict[str, Any] = {"entities": []}

    # 1) 坏枚举 -> invalid_argument（且不返回空列表冒充"真为零"）
    _expect_reason(
        "invalid_argument",
        lambda: pattern_seed_kind(
            empty_doc, seed_kind="banana", seed_refs=[seed], pattern_op="linear", count=3
        ),
    )
    # 2) face/body 在 L1 显式降级 -> unsupported_kind（不许静默降级成 feature）
    _expect_reason(
        "unsupported_kind",
        lambda: pattern_seed_kind(
            {"entities": [{"uuid": seed}]},
            seed_kind="body",
            seed_refs=[seed],
            pattern_op="linear",
            count=3,
            spacing_nm=10000000,
        ),
    )
    # 3) 不存在的种子 -> unknown_reference
    _expect_reason(
        "unknown_reference",
        lambda: pattern_seed_kind(
            empty_doc, seed_kind="feature", seed_refs=["no-such-uuid"], pattern_op="linear",
            count=3, spacing_nm=10000000, direction="x",
        ),
    )

    # 4) feature 分支：真克隆真变换（转发既有阵列实现），实例真落进文档
    legacy = _legacy()
    if legacy is None or not hasattr(legacy, "_demo_line"):
        raise AssertionError("fixture_unavailable: legacy realizer/_demo_line missing")
    line = legacy._demo_line(seed, (0, 0), (1000000, 0))
    doc: dict[str, Any] = {
        "schema": 1,
        "entities": [line],
        "constraints": [],
        "dimensions": [],
        "features": [],
        "annotations": [],
    }
    attempts = (
        {"count": 3, "spacing_nm": 10000000, "direction": "x"},
        {"count": 3, "dx_nm": 10000000},
        {"instances": 3, "spacing_nm": 10000000, "direction": (1, 0)},
    )
    out: Any = None
    failures: list[str] = []
    for attempt in attempts:
        try:
            out = pattern_seed_kind(
                doc, seed_kind="feature", seed_refs=[seed], pattern_op="linear", **attempt
            )
            break
        except Exception as exc:  # noqa: BLE001 - 记下每次失败的 reason 再定夺
            failures.append("%r -> %s: %s" % (attempt, getattr(exc, "reason", None), exc))
    if out is None:
        raise AssertionError("geometry_leg_failed: %s" % " | ".join(failures))

    if not (isinstance(out, list) and len(out) == 2 and all(isinstance(u, str) and u for u in out)):
        raise AssertionError("instances_count: expected 2 new uuids, got %r" % (out,))
    print("SELFTEST_ASSERT ok=instances_count n=2")
    if seed in out or not _has_uuid(doc, seed):
        raise AssertionError("seed_must_stay: seed uuid returned or lost")
    print("SELFTEST_ASSERT ok=seed_untouched")
    if not all(_has_uuid(doc, uuid) for uuid in out):
        raise AssertionError("new_instances_not_in_doc: %r" % (out,))
    print("SELFTEST_ASSERT ok=new_instances_in_doc")
    records = doc.get(_TABLE_KEY, {}).get("records", [])
    if len(records) != 1 or records[0].get("seed_kind") != "feature":
        raise AssertionError("provenance_missing: %r" % (records,))
    print("SELFTEST_ASSERT ok=provenance_seed_kind_feature")
    return 0


def main() -> int:
    """跑自测；全绿才打印 FEATURE_OK（末行，供 tail 取判定行）。"""
    _ensure_cadcore_importable()
    try:
        rc = selftest()
    except Exception as exc:  # noqa: BLE001 - 失败必须可见
        print("FEATURE_FAIL family=%s op=pattern_seed_kind reason=%s" % (FAMILY, exc))
        return 1
    if rc != 0:
        print("FEATURE_FAIL family=%s op=pattern_seed_kind rc=%s" % (FAMILY, rc))
        return rc
    print(
        "COVERAGE must_have_slot=9 -> item_index=1 (array object type: feature/face/body): "
        "seed_kind=feature implemented via real realizer; face|body=unsupported_kind on L1 "
        "docmodel (no face/multi-body) -> those two branches stay missing"
    )
    print("FEATURE_OK family=%s op=pattern_seed_kind verdict=PASS" % FAMILY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
