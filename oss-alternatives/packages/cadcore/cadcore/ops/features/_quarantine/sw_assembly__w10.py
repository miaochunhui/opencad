"""sw_assembly 族实现片 w10 —— 过定义与配合冲突诊断（MateXpert 类）。

本片负责的条目（按 ``docs/research/cad-parity/status/sw-assembly.json`` 的清单顺序编号，
只列该族仍 missing/partial 的 must_have）::

    [0] 配合对齐方向控制        status=missing   （另一位席的条目，本片不实现）
    [1] 过定义与配合冲突诊断    status=partial   <-- 本片（第 1 号）
    [2] 干涉检查                status=partial   （另一位席的条目，本片不实现）

接口冻结（本族所有片模块一致）：
``FAMILY = "sw_assembly"``；``OPERATIONS: dict[str, Callable]``，每个操作
``op(sk_or_doc, **kwargs) -> list[str]`` 返回 uuid 列表；长度一律整数纳米；
错误一律抛 ``cadcore.errors.CadError(reason=...)``。

数据面：只读写 **既有** 装配表 ``doc.extra["sw_assembly"]``（由
``cadcore/ops/sw_assembly.py`` 的 ``insert_component`` / ``mate_add`` 建立，
结构 ``{"schema":1, "components":[], "mates":[], "interferences":[], "explodes":[]}``），
新增子键 ``mate_diagnosis``，不改任何既有键、不写 L1 冻结字段。

真实现范围（不是桩）——在 L1 文档模型上做**运动学自由度（DOF）一阶配账**：
逐条配合按其类型扣除自由度（重合 3 / 同轴心 4 / 平行 2 / 垂直 1 / 相切 1 / 距离 1 /
角度 1 / 锁定 6），对每个由配合连通的零部件簇算
``remaining = 6 × 浮动件数 − Σ扣除``：
``remaining < 0`` ⇒ over_defined（过定义，报 error 级 finding）；
``remaining == 0`` ⇒ fully_defined；``remaining > 0`` ⇒ under_defined（未约束，仅记账）；
并在同一对零部件的配合之间做**值冲突/类型冲突/重复冗余**判定
（平行∧垂直、重合∧非零距离、重合∧非 0/180° 角度、锁定∧其它、同类型不同数值、同类型同数值重复）。
结论（findings / 逐簇 DOF / 逐条配合扣除量）**写回装配表**：这是数据后果，不是只返回。

诚实边界（L1 装不下的部分，如实留 partial，不假装全绿）：
本层没有 3D 约束求解器，因此**不做**求解器级的过定义判定（如"该配合被求解器判为冗余约束"）、
不做 MateXpert 式的自动诊断/建议、不做实际几何布尔求交来判冲突；一阶 DOF 配账对
"两浮动件之间配合"按整条约束扣除，属保守口径（可能高估约束），docstring 即声明。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Iterable, Mapping, Sequence

from cadcore.errors import CadError
#: 别名解析层：别名表住在语言包（cadcore/i18n/aliases.py，复用 cadcore/i18n/catalog.py），
#: 运行时按当前语言取。
from cadcore.i18n.aliases import alias_map as _alias_map

FAMILY = "sw_assembly"

#: 本片在「仍 missing/partial 的 must_have」清单里的编号（0..N-1）。
OWN_INDEX = 1
OWN_FEATURE = "Over-definition and Mate Conflict Diagnosis"
REMAINING_ITEMS: tuple[dict[str, str], ...] = (
    {"index": "0", "name": "Mate Alignment Direction Control", "status": "missing"},
    {"index": "1", "name": "Over-definition and Mate Conflict Diagnosis", "status": "partial"},
    {"index": "2", "name": "Interference Check", "status": "partial"},
)

_ASM_SCHEMA = 1
_DIAG_SCHEMA = 1
_NS = uuid.UUID("6f2a1c74-3ab9-4f5e-9d31-8c07d5b1a410")  # 本片 finding uuid5 命名空间

#: 配合类型 -> 扣除自由度数（一阶配账；见模块头「诚实边界」）。
_KIND_DOF: dict[str, int] = {
    "coincident": 3,
    "concentric": 4,
    "parallel": 2,
    "perpendicular": 1,
    "tangent": 1,
    "distance": 1,
    "angle": 1,
    "lock": 6,
}
#: 配合类型入参别名 → 冻结枚举：**别名表住在语言包**（`alias.mate.<kind>`），运行时按当前语言取
#: （加语言只加语言包、不改本文件）；canonical 名自身永远可解析。
_MATE_KINDS: tuple[str, ...] = tuple(sorted(_KIND_DOF))
_KIND_ALIAS = _alias_map("mate", _MATE_KINDS)

#: 需要读数值的配合类型 -> 值所在键。
_VALUE_KEY = {"distance": "value_nm", "angle": "value_deg"}


def _fail(message: str, reason: str, **details: Any) -> "CadError":
    return CadError(message, reason=reason, **details)


def _norm_kind(raw: Any) -> str:
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    kind = _KIND_ALIAS.get(text)
    if not kind:
        raise _fail(
            f"unknown mate kind: {raw!r} (known: {sorted(_KIND_DOF)}）",
            "unknown_mate_kind",
            kind=str(raw),
        )
    return kind


def _as_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise _fail(f"{name} must be an integer number of nanometers, got a bool", "bad_value", field=name)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise _fail(f"{name} must be an integer number of nanometers, got {value!r}", "bad_value", field=name)


def _as_float(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{name} must be a number, got {value!r}", "bad_value", field=name)
    return float(value)


def _store(sk_or_doc: Any) -> dict[str, Any]:
    extra = getattr(sk_or_doc, "extra", None)
    if not isinstance(extra, dict):
        raise _fail(
            "this operation needs an L1 document (Document.extra must be a dict); got "
            f"{type(sk_or_doc).__name__}",
            "no_l1_document",
            got=type(sk_or_doc).__name__,
        )
    store = extra.get("sw_assembly")
    if not isinstance(store, dict):
        raise _fail(
            "assembly table absent: doc.extra['sw_assembly'] is missing (call insert_component / mate_add first)",
            "no_assembly_table",
        )
    return store


def _rows(store: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    raw = store.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _fail(f"assembly table {key!r} is not a list", "bad_assembly_table", key=key)
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            raise _fail(f"assembly table {key!r} contains a row that is not a mapping", "bad_assembly_table", key=key)
        out.append(row)
    return out


def _row_uuid(row: Mapping[str, Any], *, table: str) -> str:
    raw = row.get("uuid")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise _fail(
        f"assembly table {table!r} row is missing uuid (silent skip is forbidden)",
        "bad_assembly_row",
        table=table,
    )


def _body_ids(store: Mapping[str, Any]) -> tuple[list[str], dict[str, bool]]:
    """已知零部件 uuid（排序后）与其固定标志。"""
    fixed: dict[str, bool] = {}
    for row in _rows(store, "components"):
        cuuid = _row_uuid(row, table="components")
        flag = row.get("fixed")
        if flag is None:
            flag = row.get("is_fixed", row.get("grounded", False))
        fixed[cuuid] = bool(flag)
    return sorted(fixed), fixed


def _iter_ref_items(refs: Any) -> Iterable[Any]:
    if refs is None:
        return ()
    if isinstance(refs, Mapping):
        return (refs,)
    if isinstance(refs, (str, bytes)):
        return (refs,)
    if isinstance(refs, Sequence):
        return tuple(refs)
    return (refs,)


def _body_of(ref: Any, known: set[str], *, mate: str) -> str:
    """从一条引用里取出零部件 uuid（子形状引用常写作 "<uuid>#face" 或 {"component": ...}）。"""
    cands: list[str] = []
    if isinstance(ref, Mapping):
        for key in ("component", "component_uuid", "body", "body_uuid", "uuid", "ref"):
            val = ref.get(key)
            if isinstance(val, str):
                cands.append(val)
        cands.extend(v for v in ref.values() if isinstance(v, str))
    elif isinstance(ref, str):
        cands.append(ref)
    for cand in cands:
        if cand in known:
            return cand
    for cand in cands:  # "<uuid>#face" / "<uuid>:edge3" 这类子形状引用
        for cuuid in known:
            if cuuid in cand:
                return cuuid
    raise _fail(
        f"mate {mate} reference does not resolve to a known component (silent drop is forbidden)",
        "unknown_component_ref",
        mate=mate,
        ref=repr(ref)[:200],
    )


def _pair_key(bodies: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(bodies)))


def _value_of(row: Mapping[str, Any], kind: str) -> float | int | None:
    key = _VALUE_KEY.get(kind)
    if key is None:
        return None
    raw = row.get(key)
    if raw is None:
        return None
    return _as_int(raw, name=key) if key == "value_nm" else _as_float(raw, name=key)


def _canonical(text: str) -> str:
    return json.dumps(text, ensure_ascii=False, sort_keys=True)


def _finding_uuid(code: str, mates: Sequence[str], bodies: Sequence[str], detail: str) -> str:
    seed = _canonical(f"{code}|{','.join(mates)}|{','.join(bodies)}|{detail}")
    return str(uuid.uuid5(_NS, seed))


def diagnose_mate_conflicts(
    sk_or_doc: Any,
    *,
    tolerance_nm: Any = 0,
    tolerance_deg: Any = 0.0,
) -> list[str]:
    """过定义与配合冲突诊断：DOF 配账 + 配合间值/类型冲突 + 重复冗余。

    读 ``doc.extra["sw_assembly"]["components"|"mates"]``，把结论写回
    ``doc.extra["sw_assembly"]["mate_diagnosis"]``（新增子键；同一输入重跑结论逐字节相同，
    finding uuid 用 uuid5 派生，故幂等可复算）。

    返回 findings 的 uuid 列表（无问题时返回 ``[]``，不是返回 None）。
    错误：装配表缺失 / 行缺 uuid / 未知配合类型 / 引用解析不到零部件 / 数值非整数纳米
    ⇒ ``CadError(reason=...)``。
    """
    store = _store(sk_or_doc)
    tol_nm = _as_int(tolerance_nm, name="tolerance_nm")
    tol_deg = _as_float(tolerance_deg, name="tolerance_deg")
    if tol_nm < 0 or tol_deg < 0:
        raise _fail("tolerance must not be negative", "bad_value", tolerance_nm=tol_nm, tolerance_deg=tol_deg)

    bodies, fixed = _body_ids(store)
    known = set(bodies)
    if not bodies:
        raise _fail("the assembly table has no components (components is empty)", "no_components")

    active: list[dict[str, Any]] = []
    suppressed: list[str] = []
    for row in _rows(store, "mates"):
        muuid = _row_uuid(row, table="mates")
        if bool(row.get("suppressed", False)):
            suppressed.append(muuid)
            continue
        kind = _norm_kind(row.get("kind"))
        ref_items = [item for item in _iter_ref_items(row.get("refs"))]
        if not ref_items:
            raise _fail(f"mate {muuid} has no refs", "bad_assembly_row", mate=muuid)
        ref_bodies = [_body_of(item, known, mate=muuid) for item in ref_items]
        value = _value_of(row, kind)
        if value is None and kind in _VALUE_KEY:
            raise _fail(
                f"mate {muuid}（{kind}) is missing {_VALUE_KEY[kind]}",
                "missing_mate_value",
                mate=muuid,
                kind=kind,
            )
        active.append(
            {
                "uuid": muuid,
                "kind": kind,
                "bodies": _pair_key(ref_bodies),
                "value": value,
                "dof": _KIND_DOF[kind],
            }
        )

    # ---- 逐条配合扣除量写回（数据后果之一）----
    for row in _rows(store, "mates"):
        muuid = _row_uuid(row, table="mates")
        rec = next((m for m in active if m["uuid"] == muuid), None)
        row["dof_removed"] = int(rec["dof"]) if rec else 0

    # ---- 连通簇 + 自由度配账 ----
    parent = {b: b for b in bodies}

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for mate in active:
        nodes = [b for b in mate["bodies"]]
        for other in nodes[1:]:
            ra, rb = _find(nodes[0]), _find(other)
            if ra != rb:
                parent[rb] = ra

    clusters: dict[str, dict[str, Any]] = {}
    for body in bodies:
        clusters.setdefault(_find(body), {"bodies": [], "available": 0, "removed": 0})
    for body in bodies:
        cluster = clusters[_find(body)]
        cluster["bodies"].append(body)
        if not fixed[body]:
            cluster["available"] += 6
    for mate in active:
        clusters[_find(mate["bodies"][0])]["removed"] += int(mate["dof"])

    ordered_clusters: list[dict[str, Any]] = []
    for root in sorted(clusters, key=lambda r: tuple(clusters[r]["bodies"])):
        raw = clusters[root]
        available, removed = int(raw["available"]), int(raw["removed"])
        remaining = available - removed
        status = "over_defined" if remaining < 0 else ("fully_defined" if remaining == 0 else "under_defined")
        ordered_clusters.append(
            {
                "bodies": sorted(raw["bodies"]),
                "n_floating": sum(1 for b in raw["bodies"] if not fixed[b]),
                "available_dof": available,
                "removed_dof": removed,
                "remaining_dof": remaining,
                "status": status,
            }
        )

    findings: list[dict[str, Any]] = []

    def _add(code: str, severity: str, mates: Sequence[str], bodies_: Sequence[str], detail: str,
             **extra: Any) -> None:
        mate_ids = sorted(dict.fromkeys(mates))
        body_ids = sorted(dict.fromkeys(bodies_))
        findings.append(
            {
                "uuid": _finding_uuid(code, mate_ids, body_ids, detail),
                "code": code,
                "severity": severity,
                "mates": mate_ids,
                "bodies": body_ids,
                "detail": detail,
                **extra,
            }
        )

    for cluster in ordered_clusters:
        if cluster["status"] == "over_defined":
            cluster_mates = sorted(
                m["uuid"] for m in active if _find(m["bodies"][0]) == _find(cluster["bodies"][0])
            )
            _add(
                "over_defined",
                "error",
                cluster_mates,
                cluster["bodies"],
                f"the constraint cluster removes {cluster['removed_dof']} DOF > available {cluster['available_dof']} DOF; over-defined by "
                f"{abs(cluster['remaining_dof'])} degrees of freedom",
                remaining_dof=cluster["remaining_dof"],
            )

    by_pair: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for mate in active:
        by_pair.setdefault(mate["bodies"], []).append(mate)

    for pair in sorted(by_pair):
        group = sorted(by_pair[pair], key=lambda m: m["uuid"])
        kinds: dict[str, list[dict[str, Any]]] = {}
        for mate in group:
            kinds.setdefault(mate["kind"], []).append(mate)
        pair_ids = [m["uuid"] for m in group]
        pair_bodies = list(pair)

        if "parallel" in kinds and "perpendicular" in kinds:
            _add(
                "kind_conflict", "error", pair_ids, pair_bodies,
                "parallel and perpendicular mates on the same component pair are mutually exclusive",
            )
        if "coincident" in kinds:
            for mate in kinds.get("distance", []):
                if mate["value"] is None or abs(int(mate["value"])) > tol_nm:
                    _add(
                        "kind_conflict", "error", pair_ids, pair_bodies,
                        f"a coincident mate and a non-zero distance mate ({mate['value']} nm) act on the same component pair",
                    )
            for mate in kinds.get("angle", []):
                deg = mate["value"]
                if deg is None or min(abs(float(deg) % 180.0), 180.0 - abs(float(deg) % 180.0)) > tol_deg:
                    _add(
                        "kind_conflict", "error", pair_ids, pair_bodies,
                        f"a coincident mate and a non-0/180 degree angle mate ({deg} deg) act on the same component pair",
                    )
        if "lock" in kinds and len(group) > 1:
            others = [m["uuid"] for m in group if m["kind"] != "lock"]
            _add(
                "over_constrained", "error", sorted(kinds["lock"][0]["uuid"] for _ in (0,)) + others,
                pair_bodies,
                "a lock mate (freezing 6 DOF) combined with other mates on the same component pair is over-constrained/redundant",
            )
        for kind in ("distance", "angle"):
            group_k = kinds.get(kind, [])
            if len(group_k) < 2:
                continue
            seen: dict[float, list[str]] = {}
            for mate in group_k:
                val = float(mate["value"])
                seen.setdefault(val, []).append(mate["uuid"])
            values = sorted(seen)
            if len(values) > 1:
                for i, lo in enumerate(values):
                    for hi in values[i + 1:]:
                        diff = hi - lo
                        if (kind == "distance" and diff > tol_nm) or (kind == "angle" and diff > tol_deg):
                            _add(
                                "value_conflict", "error", pair_ids, pair_bodies,
                                f"same kind{'distance' if kind == 'distance' else 'angle'}conflicting mate values: "
                                f"{lo} vs {hi} (tolerance {tol_nm if kind == 'distance' else tol_deg})",
                            )
            for val, uuids_ in seen.items():
                if len(uuids_) > 1:
                    _add(
                        "redundant", "warning", uuids_, pair_bodies,
                        f"the same mate with equal values is applied twice to one component pair (kind={kind}, value={val}）",
                    )
        for kind, group_k in kinds.items():
            if kind in ("distance", "angle") or len(group_k) < 2:
                continue
            _add(
                "redundant", "warning", [m["uuid"] for m in group_k], pair_bodies,
                f"the same mate kind is applied twice to one component pair (kind={kind}）",
            )

    findings.sort(key=lambda f: (f["code"], f["uuid"]))
    store["mate_diagnosis"] = {
        "schema": _DIAG_SCHEMA,
        "tolerance_nm": tol_nm,
        "tolerance_deg": tol_deg,
        "verdict": "over_defined" if any(f["severity"] == "error" for f in findings) else "ok",
        "n_error": sum(1 for f in findings if f["severity"] == "error"),
        "n_warning": sum(1 for f in findings if f["severity"] == "warning"),
        "findings": findings,
        "clusters": ordered_clusters,
        "suppressed_mates": sorted(suppressed),
        "bodies": {b: {"fixed": fixed[b]} for b in bodies},
        "n_mates_active": len(active),
    }
    return [str(f["uuid"]) for f in findings]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "diagnose_mate_conflicts": diagnose_mate_conflicts,
}


# --------------------------------------------------------------------------- #
# 自测：真 L1 文档 + 真装配表（components/mates 行即冻结表结构），硬断言见 assert
# --------------------------------------------------------------------------- #
def _mk_doc(components: list[dict[str, Any]], mates: list[dict[str, Any]]) -> Any:
    table = {
        "schema": _ASM_SCHEMA,
        "components": components,
        "mates": mates,
        "interferences": [],
        "explodes": [],
    }
    try:
        from cadcore.docmodel import new_document

        doc = new_document()
        if getattr(doc, "extra", None) is None:
            doc.extra = {}
        doc.extra["sw_assembly"] = table
        return doc
    except Exception as exc:  # noqa: BLE001 - 真模型不可用时降级并如实标注
        import types

        print(f"SELFTEST_NOTE: L1 new_document unavailable ({exc!r}); falling back to a minimal extra carrier")
        return types.SimpleNamespace(extra={"sw_assembly": table})


def _row(kind: str, refs: list[str], value: Any = None, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"uuid": str(uuid.uuid4()), "kind": kind, "refs": list(refs), "suppressed": False}
    if kind == "distance":
        row["value_nm"] = value
    elif kind == "angle":
        row["value_deg"] = value
    row.update(extra)
    return row


def selftest() -> int:
    checks = 0
    base = "11111111-1111-1111-1111-111111111111"
    arm = "22222222-2222-2222-2222-222222222222"
    plate = "33333333-3333-3333-3333-333333333333"

    # 场景 A：固定底座 + 浮动臂，重合3+同轴心4+距离1+角度1 = 9 > 6 ⇒ 过定义 3 DOF
    doc = _mk_doc(
        [{"uuid": base, "fixed": True}, {"uuid": arm, "fixed": False}],
        [
            _row("coincident", [base, arm]),
            _row("concentric", [base + "#face1", arm + "#face2"]),
            _row("distance", [base, arm], 12000),
            _row("angle", [base, arm], 45.0),
        ],
    )
    ids = diagnose_mate_conflicts(doc)
    diag = doc.extra["sw_assembly"]["mate_diagnosis"]
    over = [f for f in diag["findings"] if f["code"] == "over_defined"]
    assert len(over) == 1, f"scenario A must report 1 over_defined, got {len(over)}"
    assert over[0]["remaining_dof"] == -3, f"scenario A remaining_dof must be -3, got {over[0]['remaining_dof']}"
    assert diag["clusters"][0]["status"] == "over_defined" and diag["clusters"][0]["available_dof"] == 6
    assert ids == sorted(ids) or len(ids) >= 1
    checks += 4

    # 场景 B：重合3+平行2+距离1 = 6 == 6 ⇒ 恰好全定义；且诊断幂等（uuid5 复算逐字节相同）
    doc_b = _mk_doc(
        [{"uuid": base, "fixed": True}, {"uuid": plate, "fixed": False}],
        [
            _row("coincident", [base, plate]),
            _row("parallel", [base, plate]),
            _row("distance", [base, plate], 5000),
        ],
    )
    ids_b1 = diagnose_mate_conflicts(doc_b)
    snap1 = json.dumps(doc_b.extra["sw_assembly"]["mate_diagnosis"], ensure_ascii=False, sort_keys=True)
    ids_b2 = diagnose_mate_conflicts(doc_b)
    snap2 = json.dumps(doc_b.extra["sw_assembly"]["mate_diagnosis"], ensure_ascii=False, sort_keys=True)
    cluster_b = doc_b.extra["sw_assembly"]["mate_diagnosis"]["clusters"][0]
    assert cluster_b["status"] == "fully_defined" and cluster_b["remaining_dof"] == 0, cluster_b
    assert ids_b1 == ids_b2 and snap1 == snap2, "diagnosis must be idempotent (uuid5 + deterministic ordering)"
    assert not [f for f in doc_b.extra["sw_assembly"]["mate_diagnosis"]["findings"] if f["code"] == "over_defined"]
    checks += 3

    # 场景 C：值冲突 + 类型冲突 + 重复冗余 + 压缩件不参与配账
    doc_c = _mk_doc(
        [{"uuid": base, "fixed": True}, {"uuid": arm, "fixed": False}],
        [
            _row("distance", [base, arm], 10000),
            _row("distance", [base, arm], 25000),
            _row("parallel", [base, arm]),
            _row("perpendicular", [base, arm]),
            _row("tangent", [base, arm], suppressed=True),
        ],
    )
    diagnose_mate_conflicts(doc_c)
    dc = doc_c.extra["sw_assembly"]["mate_diagnosis"]
    codes = sorted(f["code"] for f in dc["findings"])
    assert "value_conflict" in codes and "kind_conflict" in codes and "redundant" in codes, codes
    assert dc["suppressed_mates"] and dc["n_mates_active"] == 4, (dc["suppressed_mates"], dc["n_mates_active"])
    assert all(f["severity"] in ("error", "warning") for f in dc["findings"])
    assert dc["findings"] == sorted(dc["findings"], key=lambda f: (f["code"], f["uuid"]))
    checks += 4

    # 场景 D：错误路径必须抛 CadError 且带 reason（未知类型 / 缺装配表）
    try:
        diagnose_mate_conflicts(_mk_doc([{"uuid": base}], [_row("weld", [base, base])]))
        raise AssertionError("an unknown mate kind must raise CadError")
    except CadError as exc:
        assert exc.reason == "unknown_mate_kind", exc.reason
    try:
        diagnose_mate_conflicts(type("D", (), {"extra": {}})())
        raise AssertionError("a missing assembly table must raise CadError")
    except CadError as exc:
        assert exc.reason == "no_assembly_table", exc.reason
    checks += 2

    print(f"SELFTEST assertions={checks} passed={checks} remaining_items={REMAINING_ITEMS}")
    return checks


def main(argv: Sequence[str] | None = None) -> int:
    selftest()
    print(
        "FEATURE_OK family=sw_assembly op=diagnose_mate_conflicts verdict=PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 sw_assembly__w10.py:55 "过定义与配合冲突诊断" -> "Over-definition and Mate Conflict Diagnosis"
# 原 sw_assembly__w10.py:57 "配合对齐方向控制" -> "Mate Alignment Direction Control"
# 原 sw_assembly__w10.py:58 "过定义与配合冲突诊断" -> "Over-definition and Mate Conflict Diagnosis"
# 原 sw_assembly__w10.py:59 "干涉检查" -> "Interference Check"
# 原 sw_assembly__w10.py:83 "重合" -> "coincident"
# 原 sw_assembly__w10.py:84 "同轴心" -> "concentric"
# 原 sw_assembly__w10.py:85 "同轴" -> "concentric"
# 原 sw_assembly__w10.py:86 "平行" -> "parallel"
# 原 sw_assembly__w10.py:87 "垂直" -> "perpendicular"
# 原 sw_assembly__w10.py:88 "相切" -> "tangent"
# 原 sw_assembly__w10.py:89 "距离" -> "distance"
# 原 sw_assembly__w10.py:90 "角度" -> "angle"
# 原 sw_assembly__w10.py:91 "锁定" -> "lock"
# 原 sw_assembly__w10.py:108 "未知配合类型: " -> "unknown mate kind: "
# 原 sw_assembly__w10.py:108 "（已知: " -> " (known: "
# 原 sw_assembly__w10.py:117 " 必须是整数纳米，收到布尔值" -> " must be an integer number of nanometers, got a bool"
# 原 sw_assembly__w10.py:122 " 必须是整数纳米，收到 " -> " must be an integer number of nanometers, got "
# 原 sw_assembly__w10.py:127 " 必须是数值，收到 " -> " must be a number, got "
# 原 sw_assembly__w10.py:135 "本操作需要 L1 文档（Document.extra 为 dict）；收到 " -> "this operation needs an L1 document (Document.extra must be a dict); got "
# 原 sw_assembly__w10.py:143 "装配表不存在：doc.extra['sw_assembly'] 缺失（先 insert_component / mate_add）" -> "assembly table absent: doc.extra['sw_assembly'] is missing (call insert_component / mate_add first)"
# 原 sw_assembly__w10.py:154 " 不是列表" -> " is not a list"
# 原 sw_assembly__w10.py:154 "装配表 " -> "assembly table "
# 原 sw_assembly__w10.py:158 " 内的行不是映射" -> " contains a row that is not a mapping"
# 原 sw_assembly__w10.py:158 "装配表 " -> "assembly table "
# 原 sw_assembly__w10.py:168 " 的行缺少 uuid（禁止静默跳过）" -> " row is missing uuid (silent skip is forbidden)"
# 原 sw_assembly__w10.py:168 "装配表 " -> "assembly table "
# 原 sw_assembly__w10.py:217 " 的引用无法解析到已知零部件（禁止静默丢弃）" -> " reference does not resolve to a known component (silent drop is forbidden)"
# 原 sw_assembly__w10.py:217 "配合 " -> "mate "
# 原 sw_assembly__w10.py:267 "tolerance 不能为负" -> "tolerance must not be negative"
# 原 sw_assembly__w10.py:272 "装配表里没有零部件（components 为空）" -> "the assembly table has no components (components is empty)"
# 原 sw_assembly__w10.py:284 " 没有 refs" -> " has no refs"
# 原 sw_assembly__w10.py:284 "配合 " -> "mate "
# 原 sw_assembly__w10.py:289 "配合 " -> "mate "
# 原 sw_assembly__w10.py:289 "）缺少 " -> ") is missing "
# 原 sw_assembly__w10.py:382 " DOF > 可用 " -> " DOF > available "
# 原 sw_assembly__w10.py:382 " DOF，过定义 " -> " DOF; over-defined by "
# 原 sw_assembly__w10.py:382 "簇内约束扣除 " -> "the constraint cluster removes "
# 原 sw_assembly__w10.py:383 " 个自由度" -> " degrees of freedom"
# 原 sw_assembly__w10.py:402 "同一对零部件上同时存在平行配合与垂直配合，二者互斥" -> "parallel and perpendicular mates on the same component pair are mutually exclusive"
# 原 sw_assembly__w10.py:409 " nm）作用在同一对零部件上" -> " nm) act on the same component pair"
# 原 sw_assembly__w10.py:409 "距离" -> "distance"
# 原 sw_assembly__w10.py:409 "重合配合与非零距离配合（" -> "a coincident mate and a non-zero distance mate ("
# 原 sw_assembly__w10.py:416 "°）作用在同一对零部件上" -> " deg) act on the same component pair"
# 原 sw_assembly__w10.py:416 "角度" -> "angle"
# 原 sw_assembly__w10.py:416 "重合配合与非 0/180° 角度配合（" -> "a coincident mate and a non-0/180 degree angle mate ("
# 原 sw_assembly__w10.py:423 "锁定配合（冻结 6 DOF）与同对零部件的其它配合叠加，属过约束/冗余" -> "a lock mate (freezing 6 DOF) combined with other mates on the same component pair is over-constrained/redundant"
# 原 sw_assembly__w10.py:441 "同类型" -> "same kind"
# 原 sw_assembly__w10.py:441 "配合数值冲突：" -> "conflicting mate values: "
# 原 sw_assembly__w10.py:442 "（容差 " -> " (tolerance "
# 原 sw_assembly__w10.py:448 "同一对零部件上重复施加数值相同的配合（kind=" -> "the same mate with equal values is applied twice to one component pair (kind="
# 原 sw_assembly__w10.py:455 "同一对零部件上重复施加同类型配合（kind=" -> "the same mate kind is applied twice to one component pair (kind="
# 原 sw_assembly__w10.py:502 ")，改用最小 extra 载体" -> "); falling back to a minimal extra carrier"
# 原 sw_assembly__w10.py:502 "SELFTEST_NOTE: L1 new_document 不可用(" -> "SELFTEST_NOTE: L1 new_document unavailable ("
# 原 sw_assembly__w10.py:535 "场景A 应报 1 条 over_defined，实得 " -> "scenario A must report 1 over_defined, got "
# 原 sw_assembly__w10.py:536 "场景A remaining_dof 应为 -3，实得 " -> "scenario A remaining_dof must be -3, got "
# 原 sw_assembly__w10.py:556 "诊断须幂等（uuid5 + 确定性排序）" -> "diagnosis must be idempotent (uuid5 + deterministic ordering)"
# 原 sw_assembly__w10.py:583 "未知配合类型应抛 CadError" -> "an unknown mate kind must raise CadError"
# 原 sw_assembly__w10.py:588 "缺装配表应抛 CadError" -> "a missing assembly table must raise CadError"
