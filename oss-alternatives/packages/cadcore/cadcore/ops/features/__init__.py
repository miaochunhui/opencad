"""功能片（feature slice）注册表 —— cadcore 操作契约席的唯一真源。

功能片 = 独立模块 ``cadcore/ops/features/<family>__w<k>.py``，暴露**冻结接口**：

    FAMILY: str                     —— 族名，与族模块（``cadcore.ops.<family>``）一致
    OPERATIONS: dict[str, Callable] —— 操作名 -> 可探针操作

操作契约（与族模块一致）：第一个位置参 ``sk_or_doc``，其余**关键字**；返回 uuid 字符串列表；
非法输入抛 ``cadcore.errors.CadError``（带机器可读 ``reason``）。

公开入口
--------
``load_all()``  扫描同目录 ``*_w*.py``，返回 ``{family: {op_name: callable}}``。
``merge_into()``把**同族**功能片并入族模块 ``OPERATIONS`` 的只读视图（``SliceView``）。

隔离纪律
--------
某个功能片 import 失败 / 契约坏 / （严格模式下）签名坏，只记进模块级 ``failed`` 列表，
**不带崩整体**：其余功能片照常返回。``warnings`` 留非致命签名疑点。
本模块**只依赖标准库**。

碰撞裁决（同族内多片提供同名 op）
--------------------------------
裁决规则（**确定性**，口径见同目录 ``COLLISIONS.md``）：``L1 成功路径 > 参数面 > 文件名字典序``。

1. **L1 成功路径**：用**真 L1 夹具**（``packages/cadcore/tests/data/plate.json``，G2 的 L1 模型；
   缺席退回内置最小 L1 文档）驱动该实现，返回 ``list[str]`` 记 ``l1_ok=True``；抛 ``CadError``
   是 fail-closed 合法拒绝，记 ``l1_ok=False``。
2. **参数面**：可关键字传参的参数个数（``**kwargs`` 再记 1）；越大越全。
3. **文件名字典序**：仅当前两条都并列时兜底，且该组标记 ``fallback="filename"``。

被遮蔽的实现**不静默丢弃**：``collisions()`` 给逐 (族, op) 组的完整裁决明细（含各候选的
``l1_ok`` / ``param_surface`` / ``rank``），``shadowed()`` 给逐条被遮蔽实现（含 ``reason``）。
两者都是 ``list`` 子类（``len()`` / 迭代照旧）**且可调用**。
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "FEATURES_DIR",
    "L1_FIXTURE_RELPATH",
    "MEASUREMENT",
    "QUARANTINE_DIRNAME",
    "CollisionLog",
    "SLICE_GLOB",
    "SliceContractError",
    "SliceView",
    "canon_family",
    "check_interface",
    "check_signature",
    "collisions",
    "failed",
    "family_of_path",
    "iter_slice_paths",
    "l1_fixture",
    "load_all",
    "load_one",
    "load_report",
    "merge_into",
    "probe_l1",
    "quarantine_dir",
    "quarantine_report",
    "quarantine_selfcheck",
    "quarantine_status",
    "quarantined",
    "shadowed",
    "unprobed",
    "unquarantine",
    "validate_result",
    "warnings",
]

FEATURES_DIR = Path(__file__).resolve().parent
SLICE_GLOB = "*_w*.py"
_PKG = "cadcore.ops.features"
_FIRST_PARAM_NAMES = ("sk_or_doc", "sketch_or_doc", "sk", "doc", "document")
_SLICE_DOUBLE = re.compile(r"^(?P<family>.+?)__(?P<w>w\d+[A-Za-z0-9_]*)$")
_SLICE_SINGLE = re.compile(r"^(?P<family>.+?)_(?P<w>w\d+[A-Za-z0-9_]*)$")

#: 最近一次 ``load_all`` 里被隔离的功能片（import 失败 / 契约坏 / 严格模式签名坏）。
failed: list[dict[str, Any]] = []
#: 最近一次 ``load_all`` 里的非致命疑点（缺省模式下只记签名疑点，不隔离）。
warnings: list[dict[str, Any]] = []
class CollisionLog(list):
    """``list`` 兼容的**可调用**机读日志（门/测试既可当列表读，也可 ``()`` 取结构化明细）。

    ``CollisionLog()`` 无参 = 全部条目；``(family=...)`` / ``(op=...)`` 按族名（归一化后）与
    op 名过滤。条目一律**拷贝**返回，调用方改不动注册表内部状态。
    """

    #: 该日志记什么（``collisions`` = 逐 (族, op) 组；``shadowed`` = 逐条被遮蔽实现）。
    kind = "log"

    def __call__(self, family: Any = None, op: Any = None) -> list[dict[str, Any]]:
        want_family = canon_family(family) if family else ""
        want_op = str(op) if op else ""
        out: list[dict[str, Any]] = []
        for item in self:
            if want_family and item.get("family") != want_family:
                continue
            if want_op and item.get("op") != want_op:
                continue
            out.append(copy.deepcopy(dict(item)))
        return out

    def summary(self) -> dict[str, Any]:
        """条数摘要（判定行用；``0`` = 真为零）。"""
        return {
            "kind": self.kind,
            "entries": len(self),
            "families": len({item.get("family") for item in self}),
            "ops": len({(item.get("family"), item.get("op")) for item in self}),
        }


#: 最近一次 ``load_all`` 里的同名操作冲突：**逐 (族, op) 组**一条完整裁决明细（可调用取明细）。
collisions = CollisionLog()
collisions.kind = "collisions"
#: 最近一次 ``load_all`` 里**被遮蔽**的实现：一条一个被丢弃的候选（含 ``reason``），可调用取明细。
shadowed = CollisionLog()
shadowed.kind = "shadowed"

_last_slices: list[tuple[Path, str, str, dict[str, Callable[..., Any]]]] = []


# --------------------------------------------------------------------------- 隔离（quarantine）

#: 隔离目录名：同目录**子包** ``features/_quarantine/``。``iter_slice_paths`` 的 ``*_w*.py``
#: 通配**不递归**，故其中的片不再被扫到 —— 这是**期望行为**（"只搬运 + 显式登记"，不删实现），
#: 逐片原因见 ``QUARANTINE.md``，机读清单见 :func:`quarantined` / :func:`quarantine_report`。
QUARANTINE_DIRNAME = "_quarantine"

#: 判定行的**基线快照**：``tests/test_features_live.py`` 对 ``features/`` **全目录**（含隔离片）
#: 的一次真跑。**只作溯源与漂移参照，不再当等式判据** —— 期望账一律**现算**：隔离侧 =
#: :func:`measured_manifest_totals`（清单逐片求和），主集合侧 = :func:`scanned_main_totals`
#: （``load_all()`` 实际扫描）。``reject`` 只说明"该夹具上下文里没有成功路径"，**不**外推成
#: "该 op 是空壳"；没测到的片**一律不隔离**。
#: 运行时拿不到的量（主集合 live/noop/reject —— 只有跑门才有）一律记 ``-1`` = **未采**，
#: 绝不用 ``0`` 冒充"测到 0"（判定行里 ``-1`` / ``0`` / ``NA`` 可辨）。
MEASUREMENT_BASELINE: dict[str, Any] = {
    "role": "baseline_snapshot",
    "source": "tests/test_features_live.py",
    "command": "python3 tests/test_features_live.py",
    "measured_at": "2026-09-30T13:45+08:00",
    "fixture": "tests/l1_fixture.py:build_fixture",
    "scope": "features/ whole tree (incl. _quarantine/)",  # 中文原文（scope）：features/ 全目录（含 _quarantine/）
    "verdict": "FAIL",
    "reason": "broken_ops",
    "slices": 73, "ops": 184,
    "live": 32, "noop": 19, "reject": 105, "broken": 28, "phantom": 24, "broken_true": 4,
    "unmeasured": 0,
    #: 该次测量留在主集合的片（= 当时的 ``_MEASURED_MAIN``）：只用于**现算**"基线之后被放回的片"。
    "main_stems": (
        "annotation__w27", "draw_extra__w9", "edit_ops__w13", "edit_ops__w16",
        "hatch_measure__w13", "hatch_measure__w19", "organization__w23", "output__w10",
        "output__w11", "precision_ops__w24", "precision_ops__w26", "precision_ops__w28",
        "precision_ops__w9", "sw_assembly__w19", "sw_assembly__w27", "sw_drawings__w10",
        "sw_drawings__w16", "sw_drawings__w27", "sw_features__w15", "sw_features__w20",
    ),
}
#: 兼容别名：旧名 ``MEASUREMENT`` 仍可读，但语义已变成**基线快照**（不是期望账）。
MEASUREMENT = MEASUREMENT_BASELINE


def measured_manifest_totals() -> dict[str, int]:
    """隔离清单的合计**逐片现算**（不写死任何合计）：片数 / 操作数 / 四态 / 假活 / 真违约。"""
    totals = {"slices": len(_QUARANTINE), "ops": 0, "live": 0, "noop": 0, "reject": 0,
              "broken_true": 0, "phantom": 0}
    for rec in _QUARANTINE.values():
        for key, field in (("ops", "ops"), ("live", "live"), ("noop", "noop"),
                           ("reject", "reject"), ("broken_true", "broken"), ("phantom", "phantom")):
            totals[key] += int(rec.get(field, 0) or 0)
    return totals


def scanned_main_totals(directory: Any = None) -> dict[str, Any]:
    """主集合**实际扫描现算**：片数 + 逐片 op 数之和（真值 = ``load_all()`` 的逐片记录）。

    ``live/noop/reject/broken/phantom`` 只有跑门（``tests/test_features_live.py``）才有；
    本函数**拿不到就记 -1（未采）**，绝不返回 0 冒充"测到 0"。
    """
    load_all(directory=directory)
    slices = load_report()["slices"]
    # 中文原文（source）：load_all()/load_report() 逐片现算
    return {"source": "load_all()/load_report() computed per slice", "slices": len(slices),
            "ops": sum(len(item["ops"]) for item in slices),
            "live": -1, "noop": -1, "reject": -1, "broken": -1, "phantom": -1}

#: 这次测量里**判定 OK（留在主集合）**的片：用来把"新增/没测到的片"识别成 ``unprobed``。
#: 该行由 :func:`_render_measured_main` 确定性渲染（``--unquarantine --apply`` 会改写它）。
_MEASURED_MAIN: frozenset[str] = frozenset({"annotation__w16", "annotation__w22", "annotation__w25", "annotation__w27", "draw_extra__w22", "draw_extra__w9", "draw_ops__w19", "draw_ops__w20", "draw_ops__w26", "edit_ops__w13", "edit_ops__w16", "edit_ops__w9", "hatch_measure__w11", "hatch_measure__w12", "hatch_measure__w13", "hatch_measure__w14", "hatch_measure__w19", "hatch_measure__w20", "hatch_measure__w24", "organization__w10", "organization__w19", "organization__w23", "organization__w26", "organization__w9", "output__w10", "output__w11", "output__w14", "output__w18", "precision_ops__w20", "precision_ops__w24", "precision_ops__w26", "precision_ops__w28", "precision_ops__w9", "sw_assembly__w12", "sw_assembly__w15", "sw_assembly__w19", "sw_assembly__w22", "sw_assembly__w27", "sw_assembly__w9", "sw_drawings__w10", "sw_drawings__w15", "sw_drawings__w16", "sw_drawings__w20", "sw_drawings__w24", "sw_drawings__w25", "sw_drawings__w27", "sw_features__w15", "sw_features__w20"})

#: **人工备注**（本模块唯一手写部分）：``stem -> 实测四态 + reason``，一片一行便于文本手术。
#: ``broken`` = 真违约操作（非 ``CadError`` 异常）；``phantom`` = 假活（非空返回但 L1 载荷未变）。
#: ⚠️ **成员资格不在这里**：谁被隔离由 ``_quarantine/`` 目录实况派生（:func:`_derive_quarantine`）——
#: 于是"搬片忘改清单"在结构上不可能再让账对不上（本轮回归即此）；但**备注必须同步补/删**，见 QUARANTINE.md §1。
_QUARANTINE_NOTES: dict[str, dict[str, Any]] = {
    "annotation__w28": dict(family="annotation", ops=2, live=0, noop=0, reject=2, broken=0, phantom=0, reason="no_live_op"),
    "draw_extra__w16": dict(family="draw-extra", ops=2, live=0, noop=1, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "draw_ops__w11": dict(family="draw-ops", ops=6, live=0, noop=0, reject=6, broken=0, phantom=0, reason="no_live_op"),
    "draw_ops__w16": dict(family="draw-ops", ops=2, live=0, noop=0, reject=2, broken=0, phantom=0, reason="no_live_op"),
    "draw_ops__w17": dict(family="draw-ops", ops=2, live=0, noop=0, reject=2, broken=0, phantom=0, reason="no_live_op"),
    "draw_ops__w27": dict(family="draw-ops", ops=2, live=0, noop=0, reject=2, broken=0, phantom=0, reason="no_live_op"),
    "edit_ops__w10": dict(family="edit-ops", ops=2, live=0, noop=0, reject=2, broken=0, phantom=0, reason="no_live_op"),
    "edit_ops__w24": dict(family="edit-ops", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "output__w21": dict(family="output", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "precision_ops__w12": dict(family="precision-ops", ops=2, live=0, noop=2, reject=0, broken=0, phantom=0, reason="no_live_op"),
    "precision_ops__w16": dict(family="precision-ops", ops=2, live=0, noop=0, reject=2, broken=0, phantom=0, reason="no_live_op"),
    "sw_assembly__w10": dict(family="sw-assembly", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "sw_assembly__w28": dict(family="sw-assembly", ops=2, live=0, noop=2, reject=0, broken=0, phantom=0, reason="no_live_op"),
    "sw_drawings__w11": dict(family="sw-drawings", ops=2, live=0, noop=0, reject=2, broken=0, phantom=0, reason="no_live_op"),
    "sw_drawings__w12": dict(family="sw-drawings", ops=3, live=0, noop=0, reject=3, broken=0, phantom=0, reason="no_live_op"),
    "sw_drawings__w19": dict(family="sw-drawings", ops=2, live=0, noop=0, reject=2, broken=0, phantom=0, reason="no_live_op"),
    "sw_features__w14": dict(family="sw-features", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "sw_features__w17": dict(family="sw-features", ops=2, live=0, noop=0, reject=2, broken=0, phantom=0, reason="no_live_op"),
    "sw_features__w18": dict(family="sw-features", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "sw_features__w25": dict(family="sw-features", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "sw_pattern_refgeom__w13": dict(family="sw-pattern-refgeom", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "sw_pattern_refgeom__w18": dict(family="sw-pattern-refgeom", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "sw_pattern_refgeom__w21": dict(family="sw-pattern-refgeom", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
    "sw_pattern_refgeom__w23": dict(family="sw-pattern-refgeom", ops=4, live=0, noop=1, reject=3, broken=0, phantom=0, reason="no_live_op"),
    "sw_pattern_refgeom__w26": dict(family="sw-pattern-refgeom", ops=1, live=0, noop=0, reject=1, broken=0, phantom=0, reason="no_live_op"),
}

#: 目录里有片、备注却缺席时的**占位备注**：不猜数字、不静默通过 —— ``reason`` 不是合法 slug，
#: 机检必然报 ``reason_rule:<片>``（把片放进 ``_quarantine/`` 后必须补备注行，见 QUARANTINE.md §1）。
_UNREGISTERED_NOTE: dict[str, Any] = dict(
    family="", ops=0, live=0, noop=0, reject=0, broken=0, phantom=0,
    reason="unregistered_measurement",
)


def _derive_quarantine(directory: Any = None) -> dict[str, dict[str, Any]]:
    """**隔离清单真源 = 目录实况 × 人工备注**（治本：清单不再是"手写第二真源"）。

    * **成员资格**：``_quarantine/*_w*.py``（不递归）里有的片**一律**进清单 —— 于是
      ``load_all()`` 只跳过真在隔离目录里的片，``quarantine_skipped`` / ``moved`` / ``leaked``
      三者按构造一致；目录里没有的片**一律**不进清单（搬回主集合 = 自动恢复被扫描，
      **不需要**再手改任何清单）。本轮回归（``not_moved``+``leaked`` 同指一片）由此结构性消除。
    * **备注**：:data:`_QUARANTINE_NOTES` 只提供实测四态与 ``reason``；缺备注 ⇒ 占位 + 机检报红
      （fail-closed，绝不静默把"没测到"当"测到 0"）。
    * ``directory=`` 供自检注入合成目录；空 ``_quarantine/`` = 空清单（不是错误）。
    """
    root = Path(directory) if directory is not None else FEATURES_DIR
    qdir = root / QUARANTINE_DIRNAME
    derived: dict[str, dict[str, Any]] = {}
    if not qdir.is_dir():
        # **隔离目录缺席的降级路径**（交付载荷按封装边界排除了 `_quarantine/`；或缺目录的合成根）：
        # 成员资格退回人工备注清单 —— 清单**仍然可见**（29 片一条不少），**绝不**静默返回空清单把欠账
        # 抹掉；每条显式标 ``source=notes_fallback_dir_absent``，且 :func:`quarantine_selfcheck` 会报
        # ``quarantine_dir_absent``（另有逐片 ``not_moved``）⇒ 判定行 FAIL。**不放宽任何断言**。
        for stem, note in sorted(_QUARANTINE_NOTES.items()):
            derived[stem] = dict(note, source="notes_fallback_dir_absent")
        return derived
    for path in sorted(qdir.glob(SLICE_GLOB)):
        if not path.is_file() or path.name == "__init__.py":
            continue
        stem = path.stem
        note = _QUARANTINE_NOTES.get(stem)
        if note is None:
            hit = _SLICE_DOUBLE.match(stem) or _SLICE_SINGLE.match(stem)
            hint = (hit.group("family") if hit else stem).lower()
            derived[stem] = dict(_UNREGISTERED_NOTE, source="quarantine_dir",
                                 family=re.sub(r"[^0-9a-z]+", "-", hint).strip("-"))
        else:
            derived[stem] = dict(note, source="quarantine_dir")
    return derived


#: 被隔离的片（**派生量**，导入时按 :func:`_derive_quarantine` 渲染一次）：``stem -> 实测四态 + reason``。
_QUARANTINE: dict[str, dict[str, Any]] = _derive_quarantine(FEATURES_DIR)

#: 最近一次 ``load_all`` 里**因隔离清单被跳过**的片（搬运到位时恒为空；非空 = 清单片又出现在主目录）。
quarantine_skipped: list[dict[str, Any]] = []


def _quarantine_reason(live: int, broken: int, phantom: int) -> str:
    """隔离原因 slug（确定性）：``no_live_op`` / ``phantom`` / ``broken``，多个用 ``+`` 连。"""
    slugs: list[str] = []
    if not live:
        slugs.append("no_live_op")
    if phantom:
        slugs.append("phantom")
    if broken:
        slugs.append("broken")
    return "+".join(slugs) or "measured_fail"


def _render_measured_main(stems: Any) -> str:
    """确定性渲染 ``_MEASURED_MAIN`` 那一行（``--unquarantine --apply`` 的文本手术用同一渲染器）。"""
    return ('_MEASURED_MAIN: frozenset[str] = frozenset({'
            + ", ".join(f'"{name}"' for name in sorted(stems)) + "})")


def quarantine_dir(directory: Any = None) -> Path:
    """隔离目录 ``features/_quarantine/``（``directory`` 供自检注入合成目录）。"""
    root = Path(directory) if directory is not None else FEATURES_DIR
    return root / QUARANTINE_DIRNAME


def quarantined() -> list[dict[str, Any]]:
    """隔离清单（真源 = 逐片实测），**六字段机读视图**：``{slice, reason, live, noop, reject, broken}``。

    ``reason`` 是 slug；``broken`` 取判定行保守口径 = **真违约操作 + 假活（phantom）操作**。
    清单 = **目录实况**（``_quarantine/*_w*.py``）× **人工备注**（逐片计数与 ``reason``）：成员资格由磁盘
    决定、计数由备注给（详见 :func:`_derive_quarantine`）；文件在位核查见 :func:`quarantine_report`。
    """
    return [
        {"slice": stem, "reason": rec["reason"], "live": rec["live"], "noop": rec["noop"],
         "reject": rec["reject"], "broken": rec["broken"] + rec["phantom"]}
        for stem, rec in sorted(_QUARANTINE.items())
    ]


def unprobed(directory: Any = None) -> list[str]:
    """主集合里**这次测量没覆盖到**的片：留在主集合（绝不凭猜隔离），显式登记为 ``unprobed``。"""
    root = Path(directory) if directory is not None else FEATURES_DIR
    return sorted({p.stem for p in iter_slice_paths(root)} - set(_QUARANTINE) - set(_MEASURED_MAIN))


def quarantine_report(directory: Any = None) -> dict[str, Any]:
    """机读报告：逐片完整记录（含 ops / phantom / 在位核查）+ 泄漏清单 + unprobed + 放回条件。"""
    root = Path(directory) if directory is not None else FEATURES_DIR
    qdir = quarantine_dir(root)
    entries: list[dict[str, Any]] = []
    for stem, rec in sorted(_QUARANTINE.items()):
        item = dict(rec)
        item["slice"] = stem
        item["quarantine_path"] = str(qdir / f"{stem}.py")
        item["moved"] = (qdir / f"{stem}.py").is_file()
        item["leaked_in_main"] = (root / f"{stem}.py").is_file()
        item["reason_matches_rule"] = rec["reason"] == _quarantine_reason(
            rec["live"], rec["broken"], rec["phantom"])
        entries.append(item)
    if not _last_slices and directory is None:
        # 首次调用先做一次**实扫**（不递归：``load_report()`` 回来时 ``_last_slices`` 已就位）。
        scanned_main_totals()
    manifest = measured_manifest_totals()
    baseline = MEASUREMENT_BASELINE
    counts = {
        "quarantined": len(entries),
        "moved": sum(1 for e in entries if e["moved"]),
        "leaked": sum(1 for e in entries if e["leaked_in_main"]),
        "quarantined_ops": sum(e["ops"] for e in entries),
        "quarantined_live": sum(e["live"] for e in entries),
        "quarantined_noop": sum(e["noop"] for e in entries),
        "quarantined_reject": sum(e["reject"] for e in entries),
        "quarantined_phantom": sum(e["phantom"] for e in entries),
        "quarantined_broken_true": sum(e["broken"] for e in entries),
        "unprobed": len(unprobed(root)),
        #: 隔离目录当刻是否在位：`0` = 缺席（交付载荷按封装边界排除 `_quarantine/`）⇒ 清单走
        #: :func:`_derive_quarantine` 的人工备注降级路径，判定行报 FAIL，但**清单不静默消失**。
        "qdir_present": int(qdir.is_dir()),
        #: 清单来源口径（`quarantine_dir` = 目录实况 × 备注 / `notes_fallback_dir_absent` = 备注降级）。
        "manifest_source": "quarantine_dir" if qdir.is_dir() else "notes_fallback_dir_absent",
    }
    scanned = bool(_last_slices)
    counts["main_slices_expected"] = baseline["slices"] - manifest["slices"]
    counts["main_ops_expected"] = baseline["ops"] - manifest["ops"]
    counts["main_slices_scanned"] = len(_last_slices) if scanned else -1
    counts["main_ops_scanned"] = (sum(len(ops) for _path, _stem, _family, ops in _last_slices)
                                  if scanned else -1)
    #: 主集合 live/noop/reject **只有跑门才有** ⇒ 记 -1（未采）；基线投影只作参考、**不作等式判据**。
    counts["main_states_scanned"] = {"live": -1, "noop": -1, "reject": -1, "broken": -1,
                                     "phantom": -1,
                                     # 中文原文（note）：-1=未采（以门判定行为准）
                                     "note": "-1=unmeasured (the gate verdict line is authoritative)"}
    counts["main_live_projection"] = baseline["live"] - manifest["live"]
    put_back = sorted(set(_MEASURED_MAIN) - set(baseline["main_stems"]))
    drift = {
        "baseline": {key: baseline[key] for key in ("slices", "ops", "live", "phantom",
                                                    "broken_true")},
        "manifest": dict(manifest),
        "ops_residual": (baseline["ops"] - manifest["ops"] - counts["main_ops_scanned"])
                        if scanned else -1,
        "phantom_drift": baseline["phantom"] - manifest["phantom"],
        "broken_true_drift": baseline["broken_true"] - manifest["broken_true"],
        "put_back": put_back,
        "put_back_ops": (sum(len(ops) for _path, stem, _family, ops in _last_slices
                             if stem in set(put_back)) if scanned else -1),
        # 中文原文（basis）：漂移 = 基线快照之后被 ``--unquarantine`` 放回的片（现算 = ``_MEASURED_MAIN`` −
        # 基线 main_stems）把逐片计数从清单侧搬到主集合侧；``ops_residual==0`` = 总量不重不漏，
        # ``phantom``/``broken_true`` 漂移须落在 ``[0, put_back_ops]`` 内才算有据可查。
        "basis": ("drift = slices put back by ``--unquarantine`` after the baseline snapshot "
                  "(computed = ``_MEASURED_MAIN`` minus baseline main_stems) move per-slice counts "
                  "from the manifest side to the main-set side; ``ops_residual==0`` means totals are "
                  "neither lost nor double-counted, and ``phantom``/``broken_true`` drift must fall "
                  "within ``[0, put_back_ops]`` to be traceable."),
    }
    return {
        "measurement": dict(baseline),
        "measurement_role": baseline["role"],
        "manifest_totals": manifest,
        "drift": drift,
        "quarantine_dir": str(qdir),
        "entries": entries,
        "counts": counts,
        "leaked": [e["slice"] for e in entries if e["leaked_in_main"]],
        "unprobed": unprobed(root),
        "unquarantine_condition": ("fix until tests/test_features_live.py shows (live>0 or query>0 or write>0) "
                                   "and phantom==0 and broken==0 "
                                   "then: python3 cadcore/ops/features/__init__.py --unquarantine <slice> --apply"),
    }


def quarantine_selfcheck(directory: Any = None) -> dict[str, Any]:
    """机检（E1②）：清单 × **现算**期望账 + 逐片恒等式 + 文件在位 + 无泄漏。任一不成立 ⇒ FAIL。

    **期望账不写死**：主集合侧 = ``load_all()`` 实扫现算（:func:`scanned_main_totals`），
    期望 = 基线快照 − 清单现算（:func:`measured_manifest_totals`）—— 两条**独立读数**相等才 PASS；
    隔离侧 = 清单逐片求和。**只有跑门才能得到的量**（主集合 live/noop/reject）记 ``-1``（未采），
    只报告、不判等。

    判据：①逐片 ``ops == live+noop+reject+broken+phantom``（逐条转录自证）；
    ②``main_ops_scanned == baseline.ops − manifest.ops``（片数同理）；
    ③漂移**有据可查**：``ops_residual == 0`` 且 ``0 <= phantom_drift <= put_back_ops``、
    ``0 <= broken_true_drift <= put_back_ops``（漂移允许，但必须能被"基线之后放回的片"解释）；
    ④清单片都在 ``_quarantine/``（``moved``）且主目录无同名片（``leaked``）。
    """
    report = quarantine_report(directory)
    counts, drift = report["counts"], report["drift"]
    problems: list[str] = []
    for entry in report["entries"]:
        if entry["ops"] != entry["live"] + entry["noop"] + entry["reject"] + entry["broken"] + entry["phantom"]:
            problems.append(f"ops_identity:{entry['slice']}")
        if not entry["reason_matches_rule"]:
            problems.append(f"reason_rule:{entry['slice']}")
        if not entry["moved"]:
            problems.append(f"not_moved:{entry['slice']}")
        if entry["leaked_in_main"]:
            problems.append(f"leaked:{entry['slice']}")
    # 隔离目录**缺席**（交付载荷按封装边界排除 `_quarantine/`）= 清单走备注降级：如实报红 ——
    # 不当绿、也**不放宽** moved/leaked 断言（逐片 `not_moved` 照旧逐条报）。
    if not Path(report["quarantine_dir"]).is_dir():
        problems.append("quarantine_dir_absent")
    # 注入目录（合成夹具）= 基线/实扫口径不适用：如实标 scan_checked=0，不许假装算过（也不许当绿）。
    scan_checked = directory is None
    if scan_checked:
        if counts["main_ops_scanned"] != counts["main_ops_expected"]:
            problems.append("main_ops_identity")
        if counts["main_slices_scanned"] != counts["main_slices_expected"]:
            problems.append("main_slices_identity")
        if drift["ops_residual"] != 0:
            problems.append("ops_unreconciled")
        if not 0 <= drift["phantom_drift"] <= drift["put_back_ops"]:
            problems.append("phantom_drift_unreconciled")
        if not 0 <= drift["broken_true_drift"] <= drift["put_back_ops"]:
            problems.append("broken_true_drift_unreconciled")
    return {"verdict": "PASS" if not problems else "FAIL", "rc": 0 if not problems else 1,
            "problems": problems, "counts": counts, "drift": drift,
            "scan_checked": scan_checked, "report": report}


def quarantine_status(directory: Any = None) -> str:
    """判定行：一条命令给 PASS/FAIL；**期望账现算**、漂移如实报（数字一律以当次输出为准）。"""
    check = quarantine_selfcheck(directory)
    counts, drift = check["counts"], check["drift"]
    states = counts["main_states_scanned"]
    line = (f"FEATURES_QUARANTINE verdict={check['verdict']} quarantined={counts['quarantined']} "
            f"moved={counts['moved']} leaked={counts['leaked']} unprobed={counts['unprobed']} "
            f"quarantined_ops={counts['quarantined_ops']} "
            f"main_ops_expected={counts['main_ops_expected']} "
            f"main_ops_scanned={counts['main_ops_scanned']} "
            f"main_slices_expected={counts['main_slices_expected']} "
            f"main_slices_scanned={counts['main_slices_scanned']} "
            f"phantom_manifest={drift['manifest']['phantom']} "
            f"phantom_baseline={drift['baseline']['phantom']} "
            f"phantom_drift={drift['phantom_drift']} "
            f"broken_true_drift={drift['broken_true_drift']} "
            f"drift_basis=put_back:{len(drift['put_back'])} put_back_ops:{drift['put_back_ops']} "
            f"ops_residual={drift['ops_residual']} "
            f"main_live_projection={counts['main_live_projection']} "
            f"main_states_scanned=live:{states['live']},noop:{states['noop']},reject:{states['reject']},"
            f"broken:{states['broken']},phantom:{states['phantom']} "
            f"scan_checked={int(check['scan_checked'])} "
            f"qdir_present={counts['qdir_present']} "
            f"manifest_source={counts['manifest_source']} "
            f"baseline={MEASUREMENT_BASELINE['source']}@{MEASUREMENT_BASELINE['measured_at']} "
            f"problems={len(check['problems'])} rc={check['rc']}")
    if check["problems"]:
        line += " detail=" + ",".join(check["problems"][:5])
    return line


def _ruler_precheck(stem: str, root: Path) -> dict[str, Any]:
    """用**尺子本体**（``tests/test_features_live.py``）单测一片，返回 ``live/query/write/broken/phantom``。

    与 ``--include-quarantine`` **同源**：同一隔离区 loader（``cadcore.ops.features._quarantine.<stem>``）、
    同一 ladder、同一 ``query`` 声明规则 ⇒ 闸与尺子不会各说各话。
    尺子不可用（非仓内运行、导入失败…）⇒ 返回 ``{"ok": False, "error": ...}``，调用方回落 legacy 口径**并显式打印**。
    """
    pkg_root = root.parents[2]  # <repo root>/cadcore/ops/features → <repo root>
    try:
        if str(pkg_root) not in sys.path:
            sys.path.insert(0, str(pkg_root))
        ruler = importlib.import_module("tests.test_features_live")
        module = ruler._load_quarantine_one(quarantine_dir(root) / f"{stem}.py")
        ops, _interface_problems = check_interface(module)
        shapes = ruler._shapes()
        counts: dict[str, Any] = {"ok": True, "error": "", "ops": len(ops),
                                  "live": 0, "query": 0, "write": 0,
                                  "broken": 0, "phantom": 0}
        for name in sorted(ops):
            rec = ruler._measure_op(name, ops[name], shapes, module)
            state = rec.get("state")
            if state in ("live", "query", "write", "broken", "phantom"):
                counts[state] += 1
        return counts
    except BaseException as exc:  # noqa: BLE001 - 尺子不可用只记账，绝不外抛
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "ops": -1,
                "live": -1, "query": -1, "write": -1, "broken": -1, "phantom": -1}


def unquarantine(stem: str, *, apply: bool = False, directory: Any = None) -> dict[str, Any]:
    """把隔离片**放回**主集合（缺省 dry-run，``apply=True`` 才动文件与清单）。

    **权威**放回判据（五档口径，= CLI 判定行里 ``condition=`` 那一行）：``tests/test_features_live.py``
    逐 op 现测，该片必须 ``(live>0 ∨ query>0 ∨ write>0) ∧ phantom==0 ∧ broken==0``。
    - **算成功的三档**：``live``（返回非空 uuid 列表且 L1 载荷真变）、``query``（声明式只读、三面全静、
      返回非空数据）、``write``（有副作用但 L1 未变）；三档任一 ``>0`` 即"有成功路径"，**实际靠哪一档通过
      由判定行现算打印**（``gate_success_tiers``），不靠事后读源码推。
    - **判红的两档**：``phantom``（既无副作用也无 L1 变更，含声明只读却改动的 MISLABEL）与 ``broken``
      （非 ``CadError`` 异常 / 返回非 uuid 列表 / 接口 problems）；**任一 ``>0`` ⇒
      ``precheck_ruler_phantom`` / ``precheck_ruler_broken`` ⇒ DENY**，成功档再多也不豁免、门槛不降。
    预检口径**已对齐尺子**：同一隔离区 loader、同一 ladder、同一 ``query`` 声明规则；但 :func:`probe_l1`
    只认 uuid 列表、认不出 ``query``/``write`` ⇒ 两个尺子会不一致，此时以尺子为准并打印 ``basis_divergent``。
    **例外（显式打印、不许当绿）**：尺子不可用（``ruler["ok"] is False``，判定行 ``ruler_error=`` 非空）时
    回落 legacy ``probe_l1`` 口径、``gate_basis=l1_probe_legacy_ruler_unavailable``，此时上面那条
    ``phantom==0 ∧ broken==0`` **未被验证**。预检 ALLOW 只说明放回当刻过闸，放回后必须复跑门。
    """
    global _MEASURED_MAIN
    root = Path(directory) if directory is not None else FEATURES_DIR
    qpath = quarantine_dir(root) / f"{stem}.py"
    dest = root / f"{stem}.py"
    problems: list[str] = []
    if stem not in _QUARANTINE:
        problems.append("not_in_quarantine_manifest")
    if not qpath.is_file():
        problems.append("quarantine_file_absent")
    if dest.exists():
        problems.append("main_path_occupied")
    ops: dict[str, Callable[..., Any]] = {}
    live_paths = 0
    fatal: list[str] = []
    ruler: dict[str, Any] = {"ok": False, "error": "not_run", "ops": -1,
                             "live": -1, "query": -1, "write": -1,
                             "broken": -1, "phantom": -1}
    basis = "not_run"
    tiers: list[str] = []
    divergent = False
    if not problems:
        try:
            module = load_one(qpath)
        except BaseException as exc:  # noqa: BLE001 - 放回预检只记账
            problems.append(f"import_failed:{type(exc).__name__}")
        else:
            ops, interface_problems = check_interface(module)
            problems.extend(interface_problems)
    if ops and not problems:
        for name in sorted(ops):
            probe = probe_l1(ops[name], op=name)
            if probe["ok"]:
                live_paths += 1
            for key in probe["outcomes"]:
                if key.startswith("unexpected:"):
                    fatal.append(f"{name}:{key}")
        ruler = _ruler_precheck(stem, root)
        if ruler["ok"]:
            # 成功集合 = 尺子测到的 live / query / **write** 三档；phantom / broken **不计**（门槛不降）。
            tiers = [tier for tier in ("live", "query", "write") if int(ruler[tier]) > 0]
            successes = int(ruler["live"]) + int(ruler["query"]) + int(ruler["write"])
            basis = "ruler_live_query_or_write"
            # 判红两档：与判定行 ``condition=(...)>0_and_phantom==0_and_broken==0`` **逐字对应** ——
            # 成功档 >0 **不豁免** broken/phantom，否则闸比尺子松（会把已知违约/假活的片放回主集合）。
            if int(ruler["broken"]) > 0:
                problems.append(f"precheck_ruler_broken:{int(ruler['broken'])}")
            if int(ruler["phantom"]) > 0:
                problems.append(f"precheck_ruler_phantom:{int(ruler['phantom'])}")
        else:
            successes = live_paths
            tiers = ["l1_probe"] if live_paths > 0 else []
            basis = "l1_probe_legacy_ruler_unavailable"
        divergent = bool(ruler["ok"]) and ((live_paths > 0) != (successes > 0))
        if successes == 0:
            problems.append("precheck_no_live_query_or_write_success_path")
        if fatal:
            problems.append("precheck_fatal:" + ",".join(sorted(set(fatal))[:2]))
    result: dict[str, Any] = {
        "slice": stem, "apply": bool(apply), "ops": len(ops), "l1_ok_ops": live_paths,
        "gate_basis": basis, "gate_success_tiers": ",".join(tiers) or "none",
        "ruler_live": ruler.get("live", -1), "ruler_query": ruler.get("query", -1),
        "ruler_write": ruler.get("write", -1),
        "ruler_broken": ruler.get("broken", -1), "ruler_phantom": ruler.get("phantom", -1),
        "ruler_error": ruler.get("error", ""), "basis_divergent": divergent,
        "quarantine_path": str(qpath), "main_path": str(dest),
        "verdict": "ALLOW" if not problems else "DENY", "problems": list(problems),
        "moved": False, "manifest_updated": False,
    }
    if problems or not apply:
        return result
    os.replace(qpath, dest)  # 先搬运
    result["moved"] = True
    try:
        _rewrite_manifest_after_unquarantine(stem)  # 再改清单（失败则回滚搬运，保持全或无）
    except BaseException as exc:  # noqa: BLE001
        os.replace(dest, qpath)
        result.update({"moved": False, "manifest_updated": False, "verdict": "DENY",
                       "problems": list(problems) + [f"manifest_rewrite_failed:{type(exc).__name__}:{exc}"]})
        return result
    _QUARANTINE.pop(stem, None)
    _QUARANTINE_NOTES.pop(stem, None)
    _MEASURED_MAIN = frozenset(set(_MEASURED_MAIN) | {stem})
    result["manifest_updated"] = True
    return result


def _rewrite_manifest_after_unquarantine(stem: str) -> None:
    """文本手术：删掉该片的**人工备注**行 + 把它加进 ``_MEASURED_MAIN``（fail-closed，原子替换）。

    成员资格由 :func:`_derive_quarantine` 按目录实况派生，所以这里只动备注；备注缺席（目录里有片
    却没登记）会 fail-closed 报 ``manifest_entry_not_found``。
    """
    source = Path(__file__).resolve()
    text = source.read_text(encoding="utf-8")
    entry_prefix = f'    "{stem}": dict('
    entry_found = False
    measured_found = False
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith(entry_prefix):
            entry_found = True
            continue
        if line.startswith("_MEASURED_MAIN: frozenset[str] = frozenset({"):
            measured_found = True
            lines.append(_render_measured_main(set(_MEASURED_MAIN) | {stem}) + "\n")
            continue
        lines.append(line)
    if not entry_found:
        raise RuntimeError(f"manifest_entry_not_found:{stem}")
    if not measured_found:
        raise RuntimeError("measured_main_line_not_found")
    updated = "".join(lines)
    if updated == text:
        raise RuntimeError("manifest_rewrite_noop")
    tmp = source.with_name(source.name + ".quarantine-tmp")
    tmp.write_text(updated, encoding="utf-8")
    os.replace(tmp, source)


class SliceContractError(Exception):
    """功能片违反冻结契约（注册表侧红票）。"""


def _find_pkg_root(start: Path) -> Path:
    """定位含 ``cadcore/ops`` 的包根（``packages/cadcore``），供切片按包名 import。"""
    for candidate in (start, *start.parents):
        if (candidate / "cadcore" / "ops").is_dir():
            return candidate
    return start


_PKG_ROOT = _find_pkg_root(FEATURES_DIR)


def canon_family(name: Any) -> str:
    """族名归一化：小写、非字母数字统一成 ``-``（``sw_drawings`` -> ``sw-drawings``）。"""
    return re.sub(r"[^0-9a-z]+", "-", str(name or "").strip().lower()).strip("-")


def family_of_path(path: Any) -> str:
    """从文件名推族名：``<family>__w<k>.py`` 优先，退回 ``<family>_w<k>.py`` / 首个 ``_w``。"""
    stem = Path(path).stem
    for pattern in (_SLICE_DOUBLE, _SLICE_SINGLE):
        hit = pattern.match(stem)
        if hit:
            return canon_family(hit.group("family"))
    index = stem.find("_w")
    return canon_family(stem[:index] if index > 0 else stem)


def iter_slice_paths(directory: Any = None) -> list[Path]:
    """按文件名排序返回目录下全部功能片路径（``*_w*.py``，排除 ``__init__.py``）。"""
    root = Path(directory) if directory is not None else FEATURES_DIR
    if not root.is_dir():
        return []
    return [p for p in sorted(root.glob(SLICE_GLOB)) if p.is_file() and p.name != "__init__.py"]


def _ensure_import_root() -> None:
    root = str(_PKG_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _ensure_pkg() -> None:
    """本模块若按文件路径加载，也登记成包名，保证切片内的相对 import 可用。"""
    if __name__ == _PKG:
        return
    me = sys.modules.get(__name__)
    if me is not None:
        sys.modules.setdefault(_PKG, me)


def _import_from_path(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"spec_unavailable:{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except KeyboardInterrupt:
        sys.modules.pop(module_name, None)
        raise
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def load_one(path: Any, *, reload: bool = False) -> Any:
    """导入单个功能片：优先 ``cadcore.ops.features.<stem>``，失败再退回按文件路径导入。"""
    path = Path(path)
    _ensure_import_root()
    module_name = f"{_PKG}.{path.stem}"
    existing = sys.modules.get(module_name)
    if existing is not None and not reload and getattr(existing, "__file__", None) == str(path):
        return existing
    _ensure_pkg()
    if reload and existing is not None:
        try:
            return importlib.reload(existing)
        except KeyboardInterrupt:
            raise
        except BaseException:
            pass
    try:
        return importlib.import_module(module_name)
    except KeyboardInterrupt:
        raise
    except BaseException:
        return _import_from_path(path, module_name)


def check_interface(module: Any) -> tuple[dict[str, Callable[..., Any]], list[str]]:
    """校验冻结接口（``FAMILY`` / ``OPERATIONS``）；返回 ``(可用操作, 红票文案列表)``。"""
    problems: list[str] = []
    family = getattr(module, "FAMILY", None)
    if not isinstance(family, str) or not family.strip():
        problems.append("bad_contract:FAMILY")
    ops = getattr(module, "OPERATIONS", None)
    if not isinstance(ops, Mapping) or not ops:
        problems.append("bad_contract:OPERATIONS")
        return {}, problems
    usable: dict[str, Callable[..., Any]] = {}
    for name, fn in dict(ops).items():
        if not isinstance(name, str) or not name:
            problems.append("bad_contract:op_name")
            continue
        if not callable(fn):
            problems.append(f"bad_contract:op_not_callable:{name}")
            continue
        usable[name] = fn
    if not usable:
        problems.append("bad_contract:OPERATIONS_no_callable")
    return usable, problems


def check_signature(fn: Callable[..., Any]) -> list[str]:
    """按冻结契约校验操作签名：第一个位置参 ``sk_or_doc``，其余关键字。"""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return ["signature_uninspectable"]
    params = list(signature.parameters.values())
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    variadic = [p for p in params if p.kind is p.VAR_POSITIONAL]
    problems: list[str] = []
    if positional:
        if positional[0].name not in _FIRST_PARAM_NAMES:
            problems.append(f"signature:first_param:{positional[0].name}")
        extra = [p.name for p in positional[1:] if p.default is p.empty]
        if extra:
            problems.append("signature:extra_positional:" + ",".join(extra))
    elif variadic:
        problems.append("signature:no_named_first_positional")
    else:
        problems.append("signature:missing_first_positional")
    return problems


def validate_result(result: Any, *, op: str = "?") -> list[str]:
    """校验操作返回值 = uuid 字符串列表；不合规抛 :class:`SliceContractError`。"""
    if isinstance(result, (str, bytes)) or not isinstance(result, (list, tuple)):
        raise SliceContractError(f"op_result_not_uuid_list:{op}:{type(result).__name__}")
    for item in result:
        if not isinstance(item, str) or not item:
            raise SliceContractError(f"op_result_not_uuid_list:{op}:{type(item).__name__}")
    return list(result)


# --------------------------------------------------------------------------- L1 夹具探针

#: 真 L1 夹具（A20 夹具自证的 G2 L1 模型）在包内的相对位置；缺席退回内置最小 L1 文档。
L1_FIXTURE_RELPATH = ("tests", "data", "plate.json")
#: 每个候选的最大尝试次数（与 ``tests/test_features_smoke.py`` 的探针口径同源）。
_L1_MAX_ATTEMPTS = 3
_L1_CACHE: dict[str, Any] = {}

_L1_DICT_NAMES = {"params", "args", "update", "geometry", "geom", "fields", "title_fields",
                  "view_defaults", "templates", "matrix", "matrices", "bom", "payload"}
_L1_REF_NAMES = {"uuids", "refs", "entities", "items", "feature_uuids", "entity_uuids",
                 "selected", "targets", "mates", "sketches", "annotations", "sheets", "features",
                 "leaders", "views", "target", "mate", "ref", "entity", "feature", "sketch",
                 "annotation", "sheet", "uuid", "xref"}
_L1_PATH_NAMES = {"out", "path", "file", "dir", "dest", "filename", "out_dir", "base_dir",
                  "root", "directory", "dest_path", "out_path", "target_path"}
_L1_BOOL_TOKENS = ("enabled", "visible", "close", "closed", "force", "allow", "strict",
                   "construction", "suppress", "suppressed", "overwrite", "recursive", "reverse",
                   "filled", "dashed", "through", "all_through", "printable", "literal",
                   "dry_run", "apply", "check", "on", "off", "flag")
_L1_NUM_TOKENS = ("_nm", "count", "index", "steps", "position", "rows", "cols", "segments",
                  "radius", "diameter", "depth", "width", "height", "spacing", "tolerance",
                  "limit", "thickness")
_L1_POINT_PREFIX = ("p1", "p2", "at", "base", "point", "target", "start", "end", "center",
                    "origin", "ref_a", "ref_b", "corner", "from", "to")
_L1_STR_VALUES: dict[str, tuple[str, ...]] = {
    "mode": ("auto", "distance", "all"), "kind": ("aligned", "auto", "line"),
    "type": ("auto", "solid"), "alignment": ("aligned", "anti_aligned", "flip"),
    "axis": ("x", "z"), "op": ("probe", "cut"), "name": ("probe", "0"), "layer": ("0",),
    "style": ("STANDARD", "standard"), "text": ("probe",), "label": ("probe",),
    "fmt": ("pdf",), "format": ("pdf",),
    "token": ("probe",), "reason": ("probe",), "value": ("probe",), "family": ("draw_ops",),
}
#: 内置最小 L1 文档（真夹具缺席时的兜底，**不是**合成垃圾：形状与 L1 契约一致）。
_BUILTIN_L1: dict[str, Any] = {
    "schema_version": 1, "unit": "mm", "standard_profile": "gbt",
    "sketches": [{
        "uuid": "a11ce000-0000-4000-8000-000000000010", "plane": "XY",
        "entities": [{"uuid": "a11ce000-0000-4000-8000-000000000020", "kind": "line",
                      "layer": "0", "geom": {"x1": 0, "y1": 0, "x2": 10000, "y2": 0}}],
    }],
    "features": [], "annotations": [], "sheets": [],
}


def _builtin_l1() -> dict[str, Any]:
    return copy.deepcopy(_BUILTIN_L1)


def l1_fixture_path() -> str:
    """真 L1 夹具路径（缺席返回 ``""``，表示退回内置文档）。"""
    l1_fixture()
    return str(_L1_CACHE.get("path", ""))


def l1_fixture() -> dict[str, Any]:
    """**真 L1 夹具**文档副本：``packages/cadcore/tests/data/plate.json``（A20 夹具自证件）。

    只读、每次返回 ``deepcopy``；解析失败或结构不像 L1 文档时退回 :data:`_BUILTIN_L1`。
    """
    if "doc" not in _L1_CACHE:
        path = _PKG_ROOT.joinpath(*L1_FIXTURE_RELPATH)
        doc: Any = None
        try:
            if path.is_file():
                doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict) or not isinstance(doc.get("sketches"), list) or not doc["sketches"]:
            doc = _builtin_l1()
            _L1_CACHE["path"] = ""
        else:
            _L1_CACHE["path"] = str(path)
        _L1_CACHE["doc"] = doc
    return copy.deepcopy(_L1_CACHE["doc"])


def _fixture_tokens(doc: Mapping[str, Any]) -> dict[str, Any]:
    """从 L1 夹具现取探针要用的**真值**（uuid 池 / 字符串 / 图层），走一遍即定序（确定性）。"""
    uuids: list[str] = []
    layers: list[str] = []
    text_keys: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            value = node.get("uuid")
            if isinstance(value, str) and value and value not in uuids:
                uuids.append(value)
            layer = node.get("layer")
            if isinstance(layer, str) and layer and layer not in layers:
                layers.append(layer)
            key = node.get("text_key")
            if isinstance(key, str) and key and key not in text_keys:
                text_keys.append(key)
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(doc)
    strings: dict[str, tuple[str, ...]] = {}
    for field, names in (("unit", ("unit", "units")), ("standard_profile", ("standard", "standard_profile")),
                         ("projection_angle", ("projection_angle", "projection")),
                         ("size", ("size",)), ("scale", ("scale",)), ("plane", ("plane",))):
        value = doc.get(field)
        if isinstance(value, str) and value:
            for name in names:
                strings[name] = (value,)
    if layers:
        strings["layer"] = tuple(layers)
    if text_keys:
        strings["text_key"] = tuple(text_keys)
    if not uuids:
        uuids = [item["uuid"] for item in _BUILTIN_L1["sketches"]]
    return {"uuids": uuids, "layers": layers or ["0"], "strings": strings}


def _ann_kind(annotation: Any) -> Any:
    """注解 -> 标量/容器类型（认不出返回 ``None``；只做文本与 ``__origin__`` 判断，不引入依赖）。"""
    if annotation is None or annotation is inspect.Parameter.empty:
        return None
    if annotation in (bool, int, float, str, dict, list, tuple, set):
        return annotation
    origin = getattr(annotation, "__origin__", None)
    if origin in (list, tuple, set, dict):
        return origin
    text = (annotation if isinstance(annotation, str) else str(annotation)).lower()
    for name, kind in (("bool", bool), ("int", int), ("float", float), ("str", str),
                       ("dict", dict), ("list", list), ("tuple", tuple), ("set", set)):
        if name in text:
            return kind
    return None


def _l1_value(name: str, annotation: Any, idx: int, tokens: Mapping[str, Any], tmpdir: str) -> Any:
    """按参数名/注解合成一个"看起来合法"的值；uuid/字符串/图层**优先取夹具真值**。"""
    low = (name or "").lower()
    uuids = list(tokens["uuids"])
    if "points" in low or low in {"pts", "polyline", "vertices"}:
        return copy.deepcopy([[(0, 0), (10000, 0)], [(0, 0), (10000, 0), (10000, 10000)]][idx % 2])
    if low in _L1_DICT_NAMES:
        return [{}, {"uuid": uuids[0]}][idx % 2]
    if low in _L1_REF_NAMES or low.endswith(("_ref", "_refs", "_uuid", "_uuids")):
        return [uuids[idx % len(uuids)]]
    if low in _L1_PATH_NAMES or low.endswith(("_dir", "_path", "_file", "_root")):
        return [tmpdir, os.path.join(tmpdir, "probe.out")][idx % 2]
    if any(low == token or low.startswith(token + "_") for token in _L1_POINT_PREFIX):
        return [(0, 0), (10000, 0), (5000, 5000)][idx % 3]
    kind = _ann_kind(annotation)
    if kind is int:
        return [1000, 1][idx % 2]
    if kind is float:
        return [45.0, 0.0][idx % 2]
    if kind is bool:
        return [True, False][idx % 2]
    if kind in (list, set):
        return [[], [uuids[0]]][idx % 2]
    if kind is tuple:
        return [(0, 0), (10000, 0)][idx % 2]
    if kind is dict:
        return [{}, {"uuid": uuids[0]}][idx % 2]
    if low.endswith("_deg") or "angle" in low or low in {"scale", "rotation"}:
        return [45.0, 0.0, 90.0][idx % 3]
    if kind is str:
        pool = tokens["strings"].get(low) or _L1_STR_VALUES.get(low) or ("probe", "0")
        return pool[idx % len(pool)]
    if any(token in low for token in _L1_BOOL_TOKENS) or low.startswith(("is_", "has_")):
        return [True, False, True][idx % 3]
    if low.endswith("_nm") or any(token in low for token in _L1_NUM_TOKENS):
        return [1000, 1, 0][idx % 3]
    if low in _L1_STR_VALUES:
        pool = _L1_STR_VALUES[low]
        return pool[idx % len(pool)]
    return ["probe", "0", 1000][idx % 3]


def _is_cad_error(exc: BaseException) -> bool:
    """鸭子判定 ``cadcore.errors.CadError``（本模块**只依赖标准库**，不做包内 import）。"""
    return any(cls.__name__ == "CadError" for cls in type(exc).__mro__)


def _param_surface(fn: Callable[..., Any]) -> tuple[int, list[str]]:
    """参数面（越大越全）= 可关键字传参的参数个数 + （有 ``**kwargs`` 再记 1）；附参数名清单。"""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return 0, []
    params = list(signature.parameters.values())[1:]  # 首个位置参 = sk_or_doc
    named = [p.name for p in params
             if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
    surface = len(named) + (1 if any(p.kind is p.VAR_KEYWORD for p in params) else 0)
    return surface, sorted(named)


def probe_l1(fn: Callable[..., Any], *, op: str = "?", fixture: Any = None,
             tmpdir: Any = None) -> dict[str, Any]:
    """用**真 L1 夹具**驱动一个实现，判定它能否跑出**成功路径**（返回 uuid 字符串列表）。

    只读、可重复：每次尝试都 ``deepcopy`` 夹具文档，路径类参数指向临时目录（退出即删），
    不写任何模块级状态。返回 ``{"ok","detail","attempts","outcomes","document"}``。
    ``ok=False`` 只说明"这一路 L1 驱动没跑出成功路径"（多半是夹具喂不出它要的域数据），
    **不**等于该实现坏 —— 它只被用作裁决的第 1 优先级，不参与 ``failed`` / ``warnings``。
    """
    doc = fixture if fixture is not None else l1_fixture()
    own_tmp = tmpdir is None
    work = tempfile.mkdtemp(prefix="cadcore-l1-probe-") if own_tmp else str(tmpdir)
    report: dict[str, Any] = {"ok": False, "detail": "", "attempts": 0, "outcomes": {},
                              "document": str(_L1_CACHE.get("path", ""))}
    rejects: list[str] = []
    try:
        try:
            signature = inspect.signature(fn)
        except (TypeError, ValueError):
            report["detail"] = "signature_uninspectable"
            return report
        params = list(signature.parameters.values())
        if not params:
            report["detail"] = "no_parameters"
            return report
        tokens = _fixture_tokens(doc)
        keyword = [p for p in params[1:]
                   if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
        required = [p for p in keyword if p.default is p.empty]
        optional = [p for p in keyword if p.default is None]
        combos = [required]
        if optional:
            combos.extend([required + optional, required + optional[:1]])
        variants: list[tuple[str, dict[str, Any]]] = []
        for combo_idx, combo in enumerate(combos):
            for idx in (0, 1):
                variants.append((f"v{combo_idx}i{idx}", {
                    p.name: _l1_value(p.name, p.annotation, idx, tokens, work) for p in combo
                }))
        for label, kwargs in variants[:_L1_MAX_ATTEMPTS]:
            report["attempts"] += 1
            try:
                result = fn(copy.deepcopy(doc), **kwargs)
            except KeyboardInterrupt:
                raise
            except BaseException as exc:  # noqa: BLE001 - 探针只记账，绝不外抛
                if _is_cad_error(exc):
                    reason = str(getattr(exc, "reason", "") or exc)
                    rejects.append(reason)
                    key = f"domain_reject:{reason}"
                else:
                    key = f"unexpected:{type(exc).__name__}"
                    report["detail"] = f"unexpected_exception:{type(exc).__name__}@{label}: {exc}"
                report["outcomes"][key] = report["outcomes"].get(key, 0) + 1
                continue
            try:
                validate_result(result, op=op)
            except SliceContractError as exc:
                report["detail"] = f"bad_result@{label}: {exc}"
                continue
            report["ok"] = True
            report["detail"] = f"uuid_list@{label} n={len(result)}"
            return report
        if not report["detail"]:
            report["detail"] = (f"no_l1_success_path rejected={rejects[0]}" if rejects
                                else "no_l1_success_path no_attempt")
        return report
    finally:
        if own_tmp:
            shutil.rmtree(work, ignore_errors=True)


def _adjudicate(family: str, op: str, cands: list[tuple[str, Callable[..., Any], Path]],
                *, fixture: Any, tmpdir: str) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """确定性裁决一组同 (族, op) 候选：``L1 成功路径 > 参数面 > 文件名字典序``。

    返回 ``(胜者 stem, 组明细, 被遮蔽条目列表)``；被遮蔽者一条不漏地进明细。
    """
    ranked: list[dict[str, Any]] = []
    for stem, fn, path in cands:
        probe = probe_l1(fn, op=op, fixture=fixture, tmpdir=tmpdir)
        surface, keywords = _param_surface(fn)
        ranked.append({
            "stem": stem, "path": str(path),
            "l1_ok": bool(probe["ok"]), "l1_detail": probe["detail"],
            "param_surface": surface, "params": keywords,
            "rank": [0 if probe["ok"] else 1, -surface, stem],
        })
    ranked.sort(key=lambda item: item["rank"])
    winner, losers = ranked[0], ranked[1:]
    if winner["l1_ok"] and any(not item["l1_ok"] for item in losers):
        decision = "l1"
    elif all(winner["param_surface"] > item["param_surface"] for item in losers):
        decision = "param_surface"
    else:
        decision = "filename"
    group = {
        "family": family, "op": op, "decision": decision,
        "fallback": "" if decision != "filename" else "filename",
        "kept": winner["stem"], "shadowed": [item["stem"] for item in losers],
        "records": len(losers), "candidates": ranked,
    }
    details = [{
        "family": family, "op": op, "kept": winner["stem"], "dropped": item["stem"],
        "stem": item["stem"], "path": item["path"], "reason": decision,
        "l1_ok": item["l1_ok"], "l1_detail": item["l1_detail"],
        "param_surface": item["param_surface"], "params": item["params"],
    } for item in losers]
    return winner["stem"], group, details


def _adjudicate_into(loaded: dict[str, dict[str, Callable[..., Any]]],
                     candidates: dict[str, dict[str, list[tuple[str, Callable[..., Any], Path]]]]) -> None:
    """把候选表裁决进 ``loaded``（唯一真源 = ``_adjudicate``），并填 ``collisions`` / ``shadowed``。"""
    fixture = l1_fixture()
    work = tempfile.mkdtemp(prefix="cadcore-l1-probe-")
    try:
        for family in sorted(candidates):
            bucket = loaded.setdefault(family, {})
            for op in sorted(candidates[family]):
                cands = sorted(candidates[family][op], key=lambda item: item[0])
                if len(cands) == 1:
                    bucket[op] = cands[0][1]
                    continue
                by_stem = {stem: fn for stem, fn, _path in cands}
                kept, group, details = _adjudicate(family, op, cands, fixture=fixture, tmpdir=work)
                bucket[op] = by_stem[kept]
                collisions.append(group)
                shadowed.extend(details)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load_all(
    *,
    directory: Any = None,
    strict_signature: bool = False,
    reload: bool = False,
) -> dict[str, dict[str, Callable[..., Any]]]:
    """扫描同目录 ``*_w*.py`` 并加载，返回 ``{family: {op_name: callable}}``。

    * import 失败 / 契约坏的功能片被**隔离**：记进 ``failed``（含 ``kind`` / ``stem`` / ``path`` /
      ``family`` / ``error``），其余功能片照常返回 —— 绝不整体崩。
    * 签名疑点缺省只记 ``warnings``；``strict_signature=True`` 时并进 ``failed`` 并隔离该片。
    * 同族内多片提供同名 op 时**不再"文件名字典序先到者胜"**，改按确定性规则裁决
      （``L1 成功路径 > 参数面 > 文件名字典序``，见 :func:`_adjudicate`）：胜者进 ``loaded``，
      被遮蔽者一条不漏地进 ``collisions``（逐组明细）与 ``shadowed``（逐条实现）。
    * ``directory`` 供自检注入合成片目录（缺省 = 本目录）；``reload=True`` 时重新执行源码。
    """
    global _last_slices
    root = Path(directory) if directory is not None else FEATURES_DIR
    del failed[:]
    del warnings[:]
    del collisions[:]
    del shadowed[:]
    loaded: dict[str, dict[str, Callable[..., Any]]] = {}
    candidates: dict[str, dict[str, list[tuple[str, Callable[..., Any], Path]]]] = {}
    records: list[tuple[Path, str, str, dict[str, Callable[..., Any]]]] = []
    quarantine_skipped.clear()
    for path in iter_slice_paths(root):
        stem = path.stem
        hint = family_of_path(path)
        if stem in _QUARANTINE:  # 显式隔离（真源 = 逐片实测，见 QUARANTINE.md）：只搬运 + 登记，不删实现
            quarantine_skipped.append({"stem": stem, "path": str(path),
                                       "reason": _QUARANTINE[stem]["reason"]})
            continue
        try:
            module = load_one(path, reload=reload)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001 - 功能片必须被隔离，不许带崩整体
            failed.append({
                "kind": "import_failed", "stem": stem, "path": str(path), "family": hint,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        ops, problems = check_interface(module)
        family = canon_family(getattr(module, "FAMILY", "")) or hint
        if problems:
            failed.append({
                "kind": "contract_broken", "stem": stem, "path": str(path), "family": family,
                "error": ";".join(problems),
            })
            continue
        signature_problems: list[str] = []
        for name in sorted(ops):
            signature_problems.extend(f"{name}:{p}" for p in check_signature(ops[name]))
        if signature_problems:
            record = {
                "kind": "signature_broken", "stem": stem, "path": str(path), "family": family,
                "error": ";".join(signature_problems),
            }
            if strict_signature:
                failed.append(record)
                continue
            warnings.append(dict(record, kind="signature_suspect"))
        for name in sorted(ops):
            fn = ops[name]
            pool = candidates.setdefault(family, {}).setdefault(name, [])
            if all(known[1] is not fn for known in pool):  # 同一函数对象只算一个候选（旧口径）
                pool.append((stem, fn, path))
        records.append((path, stem, family, dict(ops)))
    _last_slices = records
    _adjudicate_into(loaded, candidates)
    return loaded


def load_report() -> dict[str, Any]:
    """最近一次 ``load_all`` 的机读报告（``slices`` / ``failed`` / ``warnings`` / 碰撞裁决）。"""
    return {
        "features_dir": str(FEATURES_DIR),
        "slices": [
            {"stem": stem, "family": family, "path": str(path), "ops": sorted(ops)}
            for path, stem, family, ops in _last_slices
        ],
        "failed": [dict(item) for item in failed],
        "warnings": [dict(item) for item in warnings],
        "collisions": [dict(item) for item in collisions],
        "shadowed": [dict(item) for item in shadowed],
        "collision_groups": len(collisions),
        "shadowed_total": len(shadowed),
        "adjudication_rule": "l1_success_path > param_surface > filename",
        "l1_fixture": str(_L1_CACHE.get("path", "")),
        "loaded_families": sorted({family for _, _, family, _ in _last_slices}),
        "quarantined_total": len(_QUARANTINE),
        "quarantine_skipped": [dict(item) for item in quarantine_skipped],
        "unprobed": unprobed(),
        "quarantine_counts": quarantine_report()["counts"],
    }


def _family_matches(target: str, candidate: str) -> bool:
    """族名匹配：精确，或 ``-`` 边界前缀（``annotation`` 命中 ``annotation-extra``）。"""
    if not target or not candidate:
        return False
    if target == candidate:
        return True
    return candidate.startswith(target + "-") or target.startswith(candidate + "-")


class SliceView(Mapping):
    """族模块 ``OPERATIONS`` 的只读**视图**：族模块自身操作优先，其后才是同族功能片。

    视图绑定安装时那个 ``OPERATIONS`` 字典对象：族模块后续**原地**更新可见，整体替换属性不可见
    （替换后重跑 :func:`merge_into` 即可）。同名冲突不静默：见 :meth:`report`。
    """

    def __init__(self, base: Mapping[str, Any], slices: Any, *, family: str = "") -> None:
        self._base: Mapping[str, Any] = base if isinstance(base, dict) else dict(base)
        self._slices = tuple(slices)
        self.family = family

    def _merge(self) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
        merged: dict[str, Any] = {}
        origin: dict[str, str] = {}
        collisions: list[dict[str, Any]] = []
        for name in sorted(self._base):
            merged[name] = self._base[name]
            origin[name] = "family_module"
        for stem, _family, ops in self._slices:
            for name in sorted(ops):
                fn = ops[name]
                if name in merged:
                    if merged[name] is fn:
                        continue
                    collisions.append({"op": name, "kept": origin.get(name, "?"), "dropped": stem})
                    continue
                merged[name] = fn
                origin[name] = stem
        return merged, origin, collisions

    def __getitem__(self, key: str) -> Any:
        return self._merge()[0][key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._merge()[0])

    def __len__(self) -> int:
        return len(self._merge()[0])

    def snapshot(self) -> dict[str, Any]:
        """拍平成普通 dict（供需要真 dict 的下游用）。"""
        return dict(self._merge()[0])

    def report(self) -> dict[str, Any]:
        """机读报告：``ops_total`` / 来源计数 / 片清单 / 冲突明细 / 逐操作来源。"""
        merged, origin, collisions = self._merge()
        return {
            "family": self.family,
            "ops_total": len(merged),
            "from_family_module": sum(1 for v in origin.values() if v == "family_module"),
            "from_slices": sum(1 for v in origin.values() if v != "family_module"),
            "slices": [stem for stem, _family, _ops in self._slices],
            "collisions": collisions,
            "origin": origin,
        }


def merge_into(
    family_module: Any,
    *,
    directory: Any = None,
    families: Any = None,
    strict_signature: bool = False,
    reload: bool = False,
) -> SliceView:
    """把**同族**功能片的操作并入族模块的 ``OPERATIONS`` 视图（幂等；返回该视图）。

    目标族名取 ``family_module.FAMILY``，缺省退回模块名末段；可用 ``families=`` 显式点名。
    族模块自身操作**优先**；同名冲突不静默，进 ``view.report()["collisions"]``。
    """
    load_all(directory=directory, strict_signature=strict_signature, reload=reload)
    if families is None:
        target = canon_family(getattr(family_module, "FAMILY", "") or "")
        if not target:
            target = canon_family(str(getattr(family_module, "__name__", "")).rsplit(".", 1)[-1])
        wanted = {target} if target else set()
    else:
        wanted = {canon_family(f) for f in families if str(f).strip()}
    picked = [
        (stem, family, ops)
        for _path, stem, family, ops in _last_slices
        if any(_family_matches(want, family) for want in wanted)
    ]
    base = getattr(family_module, "OPERATIONS", None)
    if isinstance(base, SliceView):
        base = base._base
    if not isinstance(base, Mapping):
        base = {}
    label = canon_family(
        getattr(family_module, "FAMILY", "") or str(getattr(family_module, "__name__", "?"))
    )
    view = SliceView(base, picked, family=label)
    family_module.OPERATIONS = view
    return view


# --------------------------------------------------------------------------- CLI

def main(argv: Any = None) -> int:
    """CLI：``--status``（缺省）/ ``--list`` / ``--report`` / ``--unquarantine <片> [--apply]``。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("--status", "status"):
        print(quarantine_status())
        return quarantine_selfcheck()["rc"]
    if args[0] in ("--list", "list"):
        for item in quarantined():
            print(f"QUARANTINED {item['slice']} reason={item['reason']} live={item['live']} "
                  f"noop={item['noop']} reject={item['reject']} broken={item['broken']}")
        print(f"QUARANTINED_TOTAL n={len(_QUARANTINE)}")
        return 0
    if args[0] in ("--report", "report"):
        print(json.dumps(quarantine_report(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args[0] == "--unquarantine":
        if len(args) < 2 or args[1].startswith("-"):
            print("UNQUARANTINE verdict=DENY problems=missing_slice_name rc=2")
            return 2
        record = unquarantine(args[1], apply="--apply" in args[2:])
        print(f"UNQUARANTINE slice={record['slice']} verdict={record['verdict']} "
              f"gate_basis={record['gate_basis']} gate_success_tiers={record['gate_success_tiers']} "
              f"ops={record['ops']} "
              f"l1_ok_ops={record['l1_ok_ops']} ruler_live={record['ruler_live']} "
              f"ruler_query={record['ruler_query']} ruler_write={record['ruler_write']} "
              f"ruler_broken={record['ruler_broken']} "
              f"ruler_phantom={record['ruler_phantom']} "
              f"basis_divergent={int(record['basis_divergent'])} "
              f"ruler_error={record['ruler_error'] or 'none'} "
              f"apply={record['apply']} moved={record['moved']} "
              f"manifest_updated={record['manifest_updated']} "
              f"problems={','.join(record['problems']) or 'none'} "
              f"condition=(ruler_live_or_query_or_write)>0_and_phantom==0_and_broken==0 "
              f"rc={0 if record['verdict'] == 'ALLOW' else 1}")
        return 0 if record["verdict"] == "ALLOW" else 1
    print("usage: python3 cadcore/ops/features/__init__.py "
          "[--status|--list|--report|--unquarantine <slice> [--apply]]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
