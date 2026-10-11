#!/usr/bin/env python3
"""tests.test_features_smoke —— 功能片（``cadcore/ops/features/*_w*.py``）的**冒烟总测**。

尺子口径（**接口契约**，不是"用合成垃圾参数必须成功"）
--------------------------------------------------------
对**每一个操作**（不是"每片能有一个跑通就行"）：

1. 存在于 ``OPERATIONS`` 且 ``callable``（复用注册表 :func:`cadcore.ops.features.check_interface`）；
2. 签名第一个位置参数存在，且**没有"只能靠位置传"的第二个必填参**（其余应为关键字；同样复用
   :func:`check_signature` 的疑点口径，逐条上报并与注册表 ``warnings`` 对账）；
3. **真调用一次**（合成参数 × 探针文档）：**要么返回 ``list[str]``，要么抛 ``CadError``** ——
   后者是 ops 的 **fail-closed 合法行为**（``bad_uuid`` / ``bad_position`` / ``missing_text_key`` /
   ``mode_required`` / ``no_sketch`` / ``domain_reject`` …），记为 ``domain_reject``，**不算失败**；
4. **若抛的是 TypeError / KeyError / AttributeError / ValueError / SliceContractError / 其它异常类型
   ⇒ 那是真契约违规**，该操作报红、该片报红（尺子必须保持"看得见红"的能力）。

片发现：**与实际片文件数对齐，不写死条数** —— 分母 = ``features/`` 下**符合片命名契约**的 ``*.py``
（口径 = 注册表 ``SLICE_GLOB``，即 ``load_all()`` 的扫描规则），**非片文件不计入分母**：
``__init__.py`` 与显式声明的 ``_NON_SLICE_FILES``（如 ``i18n_aliases.py``）**不算漏片、不参与分母**；
目录里出现**未声明**的非片 ``*.py`` 则**点名报红**（不许静默忽略）。逐文件断言
``iter_slice_paths()`` 与 ``load_all()`` 都覆盖到，**缺哪个就点名哪个**。

不静默跳过
----------
* import 失败 / 接口坏 / 签名坏 / 操作违约 / 该操作一次都没调成 ⇒ ``subTest`` **失败**并带机器可读原因；
* 注册表 ``load_all()`` 自己记的 ``warnings``（签名疑点）与 ``failed`` 都与本文件的尺子**逐片对账**。

尺子自证（阴性对照，必须看得见红）
----------------------------------
``test_rulers_see_red`` 用合成件证明：违规返回值/违规异常类型必须报红，``CadError`` 必须判绿，
"漏片"必须被点名，空目录必须发现 0 片。
命令行 ``python3 tests/test_features_smoke.py --selftest-red`` 另做一次**真片实证**：把真片里某个 op
换成 ``return 42`` / 抛 ``ValueError`` / 抛 ``KeyError``（只在内存里换），跑到该片报红，再撤掉。

跑法::

    cd oss-alternatives/packages/cadcore
    python3 -m pytest tests/test_features_smoke.py -q
    python3 tests/test_features_smoke.py                 # 自报告模式，打印逐片判定行
    python3 tests/test_features_smoke.py --selftest-red  # 尺子看得见红的两跑实证
"""

from __future__ import annotations

import copy
import inspect
import os
import shutil
import sys
import tempfile
import types
import typing
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_PKG_ROOT = _HERE.parent.parent  # packages/cadcore
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import features as FEATURES  # noqa: E402

#: 功能片目录（分母 = 这里的**片文件**；不写死条数）。
_FEATURES_DIR = Path(getattr(FEATURES, "FEATURES_DIR", Path(FEATURES.__file__).resolve().parent))
#: 片命名口径的**唯一真源** = 注册表 ``SLICE_GLOB``（``iter_slice_paths()`` / ``load_all()`` 同源）。
_SLICE_GLOB = getattr(FEATURES, "SLICE_GLOB", "*_w*.py")
#: 该目录里**显式声明**的非片 ``*.py``：不是功能片 ⇒ 不进分母、不算漏片；但也不许静默忽略（见下）。
_NON_SLICE_FILES = ("i18n_aliases.py",)
#: 单个操作的最大尝试次数（够区分 list[str] / CadError / 真违约）。
_MAX_ATTEMPTS_PER_OP = 8
_EMPTY = inspect.Parameter.empty
_POSITIONAL_KINDS = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
_KEYWORD_KINDS = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)

_PROBE_UUIDS = {
    "line": "11111111-1111-4111-8111-111111111111",
    "circle": "22222222-2222-4222-8222-222222222222",
    "sketch": "33333333-3333-4333-8333-333333333333",
}

#: 参数名 -> 字符串候选（冻结口径里的枚举/名字类参数）。
_STR_VALUES = {
    "layer": "0",
    "name": "probe",
    "mode": "auto",
    "fmt": "pdf",
    "format": "pdf",
    "kind": "line",
    "type": "auto",
    "text": "probe",
    "label": "probe",
    "lang": "en",
    "locale": "en-US",
    "unit": "mm",
    "units": "mm",
    "style": "solid",
    "axis": "x",
    "direction": "x",
    "op": "probe",
    "reason": "probe",
    "token": "probe",
    "value": "probe",
    "family": "draw_ops",
}
_INT_TOKENS = (
    "_nm", "count", "index", "steps", "position", "rows", "cols", "segments", "radius",
    "diameter", "width", "height", "spacing", "tolerance", "limit", "size", "thickness",
)
_BOOL_TOKENS = (
    "enabled", "visible", "close", "closed", "force", "allow", "strict", "construction",
    "suppress", "suppressed", "overwrite", "recursive", "reverse", "filled", "dashed",
    "on", "off", "flag",
)
_PATH_NAMES = {
    "out", "path", "file", "dir", "dest", "filename", "out_dir", "base_dir", "root",
    "directory", "dest_path", "out_path", "target_path",
}
_POINT_PREFIX = (
    "p1", "p2", "at", "base", "point", "target", "start", "end", "center", "origin",
    "ref_a", "ref_b", "corner", "from", "to",
)
_UUID_NAMES = {"uuids", "refs", "entities", "items", "feature_uuids", "entity_uuids", "selected"}
_UUID_SCALAR_NAMES = {"uuid", "ref", "xref", "entity", "sketch"}
_SCALAR_TYPES = (int, float, str, bool, list, dict, tuple, set)


# --------------------------------------------------------------------------- 探针输入


def _probe_docs():
    """冒烟用探针文档（每次调用前 ``deepcopy``，操作改的是副本，不串片）。"""
    line = {
        "uuid": _PROBE_UUIDS["line"], "kind": "line", "layer": "0",
        "construction": False, "geom": {"x1": 0, "y1": 0, "x2": 10000, "y2": 0},
    }
    circle = {
        "uuid": _PROBE_UUIDS["circle"], "kind": "circle", "layer": "0",
        "construction": False, "geom": {"cx": 5000, "cy": 0, "r": 1000},
    }

    def sketch(with_entities):
        node = {"uuid": _PROBE_UUIDS["sketch"], "name": "Sketch1", "entities": []}
        if with_entities:
            node["entities"] = [copy.deepcopy(line), copy.deepcopy(circle)]
        return node

    return [
        ("empty_mapping", {}),
        ("bare_sketch", sketch(False)),
        ("bare_sketch_entities", sketch(True)),
        ("l1_doc_empty", {"sketches": [sketch(False)]}),
        ("l1_doc_entities", {"sketches": [sketch(True)]}),
        ("payload_doc", {"payload": {"sketches": [sketch(True)], "features": []}}),
        ("sheet_doc", {"sheets": [], "sketches": [sketch(True)]}),
        ("payload_body_doc", {
            "payload": {
                "sketches": [sketch(True)],
                "features": [{"uuid": _PROBE_UUIDS["sketch"], "kind": "extrude",
                              "name": "Extrude1", "params": {}}],
                "body": {"uuid": _PROBE_UUIDS["sketch"], "features": []},
                "bom": [{"part": "P1", "qty": 1}],
                "templates": {},
            },
        }),
        ("features_body_doc", {
            "sketches": [sketch(True)],
            "features": [{"uuid": _PROBE_UUIDS["sketch"], "kind": "extrude",
                          "name": "Extrude1", "params": {}}],
            "body": {"uuid": _PROBE_UUIDS["sketch"], "features": []},
        }),
    ]


def _is_pathish(low):
    return low in _PATH_NAMES or low.endswith(("_dir", "_path", "_file", "_root"))


def _is_pointish(low):
    for token in _POINT_PREFIX:
        if low == token or low.startswith(token + "_"):
            return True
    return False


def _annotation_kind(annotation):
    """把注解归一到受支持的标量/容器类型（认不出返回 ``None``，交回按名字合成）。"""
    if annotation is _EMPTY or annotation is None:
        return None
    if isinstance(annotation, str):
        low = annotation.lower()
        for name, kind in (("bool", bool), ("int", int), ("float", float), ("str", str),
                           ("dict", dict), ("list", list), ("tuple", tuple), ("set", set)):
            if name in low:
                return kind
        return None
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is getattr(types, "UnionType", None):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _annotation_kind(args[0]) if len(args) == 1 else None
    if origin in (list, tuple, dict, set):
        return origin
    return annotation if annotation in _SCALAR_TYPES else None


def _synth_value(name, annotation, idx, tmpdir):
    """按参数名/注解合成一个"看起来合法"的候选值（业务上解不出 ⇒ 期望是 CadError）。"""
    low = name.lower()
    if "points" in low or low in {"pts", "polyline", "vertices"}:
        pool = [[(0, 0), (10000, 0)], [(0, 0), (10000, 0), (10000, 10000)]]
        return copy.deepcopy(pool[idx % len(pool)])
    if low in _UUID_NAMES:
        return [[], [_PROBE_UUIDS["line"]]][idx % 2]
    if _is_pathish(low):
        return [tmpdir, os.path.join(tmpdir, "probe.out")][idx % 2]
    if _is_pointish(low):
        return [(0, 0), (10000, 0), (5000, 5000)][idx % 3]
    if low in _UUID_SCALAR_NAMES:
        return None
    kind = _annotation_kind(annotation)
    if kind is int:
        return [1000, 1][idx % 2]
    if kind is float:
        return [45.0, 0.0][idx % 2]
    if kind is str:
        return _STR_VALUES.get(low, ["probe", "0"][idx % 2])
    if kind is bool:
        return [True, False][idx % 2]
    if kind in (list, set):
        return [[], [_PROBE_UUIDS["line"]]][idx % 2]
    if kind is tuple:
        return [(0, 0), (10000, 0)][idx % 2]
    if kind is dict:
        return [{}, {"uuid": _PROBE_UUIDS["line"]}][idx % 2]
    if low.endswith("_deg") or "angle" in low or low in {"scale", "rotation"}:
        return [45.0, 0.0, 90.0][idx % 3]
    if any(tok in low for tok in _BOOL_TOKENS) or low.startswith(("is_", "has_")):
        return [True, False, True][idx % 3]
    if low.endswith("_nm") or any(tok in low for tok in _INT_TOKENS):
        return [1000, 1, 0][idx % 3]
    if low in _STR_VALUES:
        return _STR_VALUES[low]
    return ["probe", "0", 1000][idx % 3]


# --------------------------------------------------------------------------- 签名尺子


def _signature_report(fn):
    """检查一个操作的调用性/签名，返回 ``{callable, first_positional, problems, advisory}``。

    * ``problems``  = 结构性红票（无第一个位置参 / 必填 POSITIONAL_ONLY 参 / 签名不可读）；
    * ``advisory``  = 注册表 :func:`check_signature` 的疑点（含"第一个位置参名字不在冻结名单"这类），
      本文件按任务口径只把"第一个位置参存在"当硬判据，疑点逐条上报并与注册表 ``warnings`` 对账。
    """
    report = {
        "callable": callable(fn),
        "first_positional": None,
        "problems": [],
        "advisory": [],
    }
    if not callable(fn):
        report["problems"].append("op_not_callable")
        return report
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        report["problems"].append(f"signature_uninspectable:{type(exc).__name__}")
        return report
    positional = [p for p in signature.parameters.values() if p.kind in _POSITIONAL_KINDS]
    if not positional:
        report["problems"].append("signature:missing_first_positional")
    else:
        report["first_positional"] = positional[0].name
        posonly_required = [
            p.name for p in positional[1:]
            if p.kind is inspect.Parameter.POSITIONAL_ONLY and p.default is _EMPTY
        ]
        if posonly_required:
            report["problems"].append(
                "signature:required_positional_only:" + ",".join(posonly_required)
            )
    report["advisory"] = list(FEATURES.check_signature(fn))
    return report


def _bump(counter, key):
    counter[key] = counter.get(key, 0) + 1


def _op_audit(name, fn, tmpdir):
    """**逐操作**契约审计（本文件的核心尺子）。

    判绿只有两条路：真返回 ``list[str]``，或抛 ``CadError``（fail-closed）。其余异常类型 =
    真契约违规 ⇒ 红。返回报告 dict（``ok`` / ``problems`` / ``attempts`` / ``outcomes`` / ``detail``）。
    """
    signature_report = _signature_report(fn)
    report = {
        "op": name,
        "ok": False,
        "problems": list(signature_report["problems"]),
        "advisory": signature_report["advisory"],
        "first_positional": signature_report["first_positional"],
        "attempts": 0,
        "outcomes": {},
        "detail": "",
    }
    if report["problems"]:
        _bump(report["outcomes"], "signature_problem")
        report["detail"] = "signature:" + ",".join(report["problems"])
        return report
    first = report["first_positional"]
    signature = inspect.signature(fn)
    params = [p for p in signature.parameters.values()
              if p.name != first and p.kind in _KEYWORD_KINDS]
    required = [p for p in params if p.default is _EMPTY]
    optional = [p for p in params if p.default is None]
    combos = [required]
    if optional:
        # 变体：只填必填 / 必填+全部"缺省 None"的可选（有些 op 必须显式给一个） / 必填+首个可选。
        combos.extend([required + optional, required + optional[:1]])
    variants = []
    for combo_idx, combo in enumerate(combos):
        for idx in (0, 1):
            variants.append((
                f"v{combo_idx}i{idx}",
                {p.name: _synth_value(p.name, p.annotation, idx, tmpdir) for p in combo},
            ))
    saw_contract_ok = False
    for doc_label, doc in _probe_docs():
        for variant_label, kwargs in variants:
            if report["attempts"] >= _MAX_ATTEMPTS_PER_OP:
                break
            report["attempts"] += 1
            label = f"{doc_label}/{variant_label}"
            try:
                result = fn(copy.deepcopy(doc), **kwargs)
            except CadError as exc:
                reason = getattr(exc, "reason", None) or str(exc)
                _bump(report["outcomes"], f"domain_reject:{reason}")
                saw_contract_ok = True
                continue
            except KeyboardInterrupt:
                raise
            except BaseException as exc:  # noqa: BLE001 - 冒烟要记账，不要带崩整轮
                _bump(report["outcomes"], f"unexpected:{type(exc).__name__}")
                report["detail"] = f"unexpected_exception:{type(exc).__name__}@{label}: {exc}"
                return report
            try:
                FEATURES.validate_result(result, op=name)
            except FEATURES.SliceContractError as exc:
                _bump(report["outcomes"], "bad_result")
                report["detail"] = f"bad_result@{label}: {exc}"
                return report
            _bump(report["outcomes"], "uuid_list")
            report["ok"] = True
            report["detail"] = f"uuid_list@{label} n={len(result)}"
            return report
    report["ok"] = bool(saw_contract_ok and report["attempts"])
    if not report["ok"]:
        report["detail"] = "no_attempt" if not report["attempts"] else "no_contract_outcome"
    else:
        rejected = sorted(k for k in report["outcomes"] if k.startswith("domain_reject"))
        report["detail"] = (
            f"list_or_caderror attempts={report['attempts']}"
            + (f" rejected={rejected[0]}" if rejected else "")
        )
    return report


# --------------------------------------------------------------------------- 逐片扫描


def _on_disk_files(directory=None):
    """目录下**全部** ``*.py``（去掉 ``__init__.py``）—— 未经片命名过滤的原始枚举。"""
    root = Path(directory) if directory is not None else _FEATURES_DIR
    if not root.is_dir():
        return []
    return [p for p in sorted(root.glob("*.py")) if p.is_file() and p.name != "__init__.py"]


def _is_slice_name(path):
    """文件名是否符合片命名契约（口径同 ``load_all()``：:data:`_SLICE_GLOB`）。"""
    return Path(path).match(_SLICE_GLOB)


def _on_disk_slice_files(directory=None):
    """分母：目录下**符合片命名契约**的 ``*.py``（去掉 ``__init__.py`` 与声明的非片文件）。

    与 ``load_all()`` 的扫描规则（``SLICE_GLOB``）**同口径**：非片工具模块（``i18n_aliases.py``
    这类）不参与分母 ⇒ 不会被误判成"漏片"。
    """
    return [p for p in _on_disk_files(directory)
            if p.name not in _NON_SLICE_FILES and _is_slice_name(p)]


def _unexpected_non_slice_files(directory=None):
    """**未声明**的非片 ``*.py``（点名返回）：往这个目录塞非片模块 ⇒ 在这里报红，不静默忽略。"""
    return [p.name for p in _on_disk_files(directory)
            if p.name not in _NON_SLICE_FILES and not _is_slice_name(p)]


def _declared_non_slice_files_present(directory=None):
    """目录里**确实存在**的已声明非片文件（点名返回，供逐条列账）。"""
    return [p.name for p in _on_disk_files(directory) if p.name in _NON_SLICE_FILES]


def _missing_slice_files(directory=None, discover=None):
    """``iter_slice_paths()`` 没扫到的**片文件**（**点名**返回；"磁盘 vs 发现"是真尺子）。

    ``discover`` 只在尺子自证里换成"故意漏扫"的变体（阴性对照），缺省就是注册表的真发现函数。
    """
    scan = FEATURES.iter_slice_paths if discover is None else discover
    found = {p.resolve() for p in scan(directory)}
    return [p.name for p in _on_disk_slice_files(directory) if p.resolve() not in found]


def _audit_module(module, tmpdir):
    """对一个片模块跑完整尺子（接口 + 逐操作契约），返回逐片报告。"""
    entry = {
        "stem": getattr(module, "__name__", "?").rsplit(".", 1)[-1],
        "path": str(getattr(module, "__file__", "") or ""),
        "family": "",
        "ops": 0,
        "problems": [],
        "op_reports": [],
        "advisories": [],
        "bad_ops": [],
        "outcomes": {},
        "verdict": "FAIL",
        "detail": "",
    }
    ops, iface_problems = FEATURES.check_interface(module)
    entry["problems"].extend(iface_problems)
    entry["family"] = FEATURES.canon_family(getattr(module, "FAMILY", ""))
    entry["ops"] = len(ops)
    for name in sorted(ops):
        report = _op_audit(name, ops[name], tmpdir)
        entry["op_reports"].append(report)
        entry["advisories"].extend(f"{name}:{p}" for p in report["advisory"])
        for key, value in report["outcomes"].items():
            entry["outcomes"][key] = entry["outcomes"].get(key, 0) + value
        if not report["ok"]:
            entry["bad_ops"].append(report["op"])
    entry["verdict"] = "PASS" if (not entry["problems"] and entry["ops"] > 0
                                  and not entry["bad_ops"]) else "FAIL"
    return entry


def _scan_all(tmpdir, directory=None):
    """逐片扫描（``directory`` 供自检注入合成目录）：接口 / 签名 / 逐操作契约。"""
    slice_paths = FEATURES.iter_slice_paths(directory)
    scan = []
    for path in slice_paths:
        try:
            module = FEATURES.load_one(path)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001
            scan.append({
                "stem": path.stem, "path": str(path), "family": "", "ops": 0,
                "problems": [f"import_failed:{type(exc).__name__}: {exc}"],
                "op_reports": [], "advisories": [], "bad_ops": [], "outcomes": {},
                "verdict": "FAIL", "detail": "import_failed",
            })
            continue
        entry = _audit_module(module, tmpdir)
        entry["stem"] = path.stem
        entry["path"] = str(path)
        scan.append(entry)
    return slice_paths, scan


def _summarize(slice_paths, scan):
    total_ops = sum(e["ops"] for e in scan)
    agg = {}
    for entry in scan:
        for key, value in entry["outcomes"].items():
            agg[key] = agg.get(key, 0) + value
    bad_slices = [e for e in scan if e["verdict"] != "PASS"]
    return {
        "slices": len(scan),
        "paths": len(slice_paths),
        "ops": total_ops,
        "ops_ok": sum(sum(1 for r in e["op_reports"] if r["ok"]) for e in scan),
        "bad_slices": bad_slices,
        "outcomes": agg,
    }


# --------------------------------------------------------------------------- 测试


class FeaturesSmokeTest(unittest.TestCase):
    """功能片冒烟总测（动态发现，不写死片清单与片数）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="cadcore-features-smoke-")
        cls.slice_paths, cls.scan = _scan_all(cls.tmpdir)
        cls.summary = _summarize(cls.slice_paths, cls.scan)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # -- ① 发现与注册表 ------------------------------------------------------

    def test_discovery_matches_files_on_disk(self):
        """片发现与磁盘片文件对齐：分母 = ``SLICE_GLOB`` 片文件，缺谁点名谁；非片文件显式列账。"""
        expected = _on_disk_slice_files()
        self.assertTrue(expected, f"{_FEATURES_DIR} 下没有任何功能片文件（不许空集假绿）")
        missing = _missing_slice_files()
        self.assertEqual(
            missing, [],
            f"iter_slice_paths() 漏了 {len(missing)}/{len(expected)} 个片文件: {missing}",
        )
        self.assertEqual(
            len(self.slice_paths), len(expected),
            f"发现片数 {len(self.slice_paths)} != 目录实际片文件数 {len(expected)}",
        )
        # 非片文件：已声明的**允许存在**（点名列账，不算漏片），未声明的**必须报红**。
        declared_present = _declared_non_slice_files_present()
        unexpected = _unexpected_non_slice_files()
        print(f"[features-smoke] non_slice_files declared={list(_NON_SLICE_FILES)} "
              f"present={declared_present} unexpected={unexpected}")
        self.assertEqual(
            unexpected, [],
            f"{_FEATURES_DIR} 下出现**未声明**的非片 *.py: {unexpected} —— 要么改名成片文件，"
            f"要么加进 _NON_SLICE_FILES 显式声明（不许静默忽略）",
        )
        for name in declared_present:
            with self.subTest(non_slice=name):
                self.assertNotIn(name, missing, f"{name} 是已声明的非片文件，不许被当成漏片")
                self.assertNotIn(name, {p.name for p in expected},
                                 f"{name} 是已声明的非片文件，不许计入分母")
        for path in self.slice_paths:
            with self.subTest(slice=path.name):
                self.assertTrue(path.name.endswith(".py"))
                self.assertIn("_w", path.stem, f"{path.name} 不符合 *_w*.py 命名契约")

    def test_load_all_covers_every_slice_file(self):
        """``load_all()`` 必须覆盖到**每一个**片文件（缺哪个点名哪个），且不隔离任何片。"""
        loaded = FEATURES.load_all()
        report = FEATURES.load_report()
        self.assertTrue(loaded, "load_all() 返回空注册表")
        self.assertFalse(list(FEATURES.failed), f"load_all() 隔离了功能片: {FEATURES.failed}")
        covered = {item["stem"] for item in report["slices"]}
        expected = {p.stem for p in _on_disk_slice_files()}
        missing = sorted(expected - covered)
        self.assertEqual(missing, [], f"load_all() 没覆盖到这些片文件: {missing}")
        self.assertEqual(len(report["slices"]), len(self.slice_paths))
        for family, ops in loaded.items():
            with self.subTest(family=family):
                self.assertTrue(ops, f"族 {family} 无操作")
                for name, fn in ops.items():
                    self.assertTrue(callable(fn), f"{family}.{name} 不可调用")

    def test_registry_warnings_match_our_ruler(self):
        """注册表 ``warnings``（签名疑点）与本文件尺子逐片对账：两边不一致即失败。"""
        FEATURES.load_all()
        mine = {e["stem"]: sorted(e["advisories"]) for e in self.scan if e["advisories"]}
        theirs = {}
        for warning in FEATURES.warnings:
            theirs.setdefault(warning["stem"], []).extend(warning["error"].split(";"))
        theirs = {stem: sorted(v) for stem, v in theirs.items() if v}
        self.assertEqual(mine, theirs, "本文件算出的签名疑点与注册表 warnings 不一致")

    # -- ② 逐片接口 / 签名 ---------------------------------------------------

    def test_every_slice_interface_and_signature(self):
        """每个片：可导入 + OPERATIONS 非空 + 每个操作可调用且第一个位置参存在。"""
        for entry in self.scan:
            with self.subTest(slice=entry["stem"]):
                if entry["problems"]:
                    self.fail(f"{entry['stem']}: {'; '.join(entry['problems'])}")
                self.assertTrue(entry["ops"] > 0, f"{entry['stem']}: OPERATIONS 为空")
                self.assertTrue(entry["family"], f"{entry['stem']}: FAMILY 非法")
        for entry in self.scan:
            for report in entry["op_reports"]:
                with self.subTest(slice=entry["stem"], op=report["op"]):
                    if report["problems"]:
                        self.fail(f"{entry['stem']}.{report['op']}: {'; '.join(report['problems'])}")
                    self.assertIsNotNone(
                        report["first_positional"],
                        f"{entry['stem']}.{report['op']}: 第一个位置参不存在",
                    )
        advisories = {e["stem"]: e["advisories"] for e in self.scan if e["advisories"]}
        if advisories:
            print(f"[features-smoke] signature advisories(suspect): {advisories}")

    # -- ③ 逐操作：list[str] 或 CadError；其余异常 = 真违约 -------------------

    def test_every_op_returns_list_or_caderror(self):
        """逐操作契约：真调用后**要么返回 list[str]、要么抛 CadError**；其它异常类型报红。"""
        outcomes = self.summary["outcomes"]
        print(f"[features-smoke] call outcomes={outcomes} ops_ok={self.summary['ops_ok']}"
              f"/{self.summary['ops']}")
        for entry in self.scan:
            with self.subTest(slice=entry["stem"]):
                if entry["problems"]:
                    self.fail(f"{entry['stem']}: {'; '.join(entry['problems'])}")
                self.assertTrue(entry["ops"] > 0, f"{entry['stem']}: OPERATIONS 为空")
        for entry in self.scan:
            for report in entry["op_reports"]:
                with self.subTest(slice=entry["stem"], op=report["op"]):
                    self.assertGreater(
                        report["attempts"], 0,
                        f"{entry['stem']}.{report['op']}: 一次都没调用（不许静默跳过）",
                    )
                    if report["problems"]:
                        self.fail(
                            f"{entry['stem']}.{report['op']}: {'; '.join(report['problems'])}"
                        )
                    if not report["ok"]:
                        self.fail(
                            f"{entry['stem']}.{report['op']}: 契约违规（既没返回 list[str] "
                            f"也没抛 CadError）: {report['detail']} outcomes={report['outcomes']}"
                        )

    # -- 尺子自证（阴性对照） ------------------------------------------------

    def test_rulers_see_red(self):
        """尺子必须看得见红：违规返回值/违规异常类型报红，CadError 判绿，漏片被点名。"""
        tmpdir = tempfile.mkdtemp(prefix="cadcore-features-redproof-")

        class _SyntheticModule:
            def __init__(self, family, ops):
                self.FAMILY = family
                self.OPERATIONS = ops

        def good(sk_or_doc, **kwargs):
            return ["uuid-1"]

        def bad_signature(sk_or_doc, x):
            return ["uuid-1"]

        def no_positional(**kwargs):
            return ["uuid-1"]

        def returns_int(sk_or_doc, **kwargs):
            return 42

        def raises_value_error(sk_or_doc, **kwargs):
            raise ValueError("boom")

        def raises_key_error(sk_or_doc, **kwargs):
            raise KeyError("boom")

        def raises_type_error(sk_or_doc, **kwargs):
            raise TypeError("boom")

        def rejects(sk_or_doc, **kwargs):
            raise CadError("bad_position", reason="bad_position")

        try:
            ok_entry = _audit_module(_SyntheticModule("synth-ops", {"good": good}), tmpdir)
            self.assertEqual(ok_entry["verdict"], "PASS", f"list[str] 必须判绿: {ok_entry}")
            reject_entry = _audit_module(_SyntheticModule("synth-ops", {"rejects": rejects}), tmpdir)
            self.assertEqual(reject_entry["verdict"], "PASS",
                             f"CadError 是 fail-closed 合法行为，必须判绿: {reject_entry}")
            for label, fn in (("return_42", returns_int), ("raise_value_error", raises_value_error),
                              ("raise_key_error", raises_key_error),
                              ("raise_type_error", raises_type_error)):
                with self.subTest(mutant=label):
                    entry = _audit_module(_SyntheticModule("synth-ops", {"mutant": fn}), tmpdir)
                    self.assertEqual(entry["verdict"], "FAIL", f"{label} 必须报红: {entry}")
                    self.assertEqual(entry["bad_ops"], ["mutant"], f"{label} 必须点名违约 op")
            self.assertEqual(FEATURES.check_signature(good), [])
            self.assertTrue(FEATURES.check_signature(bad_signature), "坏签名必须被 check_signature 抓住")
            self.assertTrue(_signature_report(no_positional)["problems"], "无位置参必须报红")
            self.assertEqual(FEATURES.validate_result(["uuid-1"]), ["uuid-1"])
            for bad in ("uuid-1", [1], [""], None, {"uuid": "x"}):
                with self.assertRaises(FEATURES.SliceContractError):
                    FEATURES.validate_result(bad)
            empty_dir = tempfile.mkdtemp(prefix="cadcore-features-empty-")
            try:
                self.assertEqual(FEATURES.iter_slice_paths(empty_dir), [],
                                 "空目录必须发现 0 片（否则片数是恒真断言）")
                self.assertEqual(_missing_slice_files(empty_dir), [])
            finally:
                shutil.rmtree(empty_dir, ignore_errors=True)
            gap_dir = tempfile.mkdtemp(prefix="cadcore-features-gap-")
            try:
                (Path(gap_dir) / "ok_ops__w92.py").write_text(
                    "FAMILY = 'ok-ops'\nOPERATIONS = {'ok': lambda sk_or_doc, **kw: ['x']}\n",
                    encoding="utf-8",
                )
                (Path(gap_dir) / "stray_module.py").write_text("FAMILY = 'stray'\n",
                                                               encoding="utf-8")
                (Path(gap_dir) / "i18n_aliases.py").write_text("ALIASES = {}\n", encoding="utf-8")
                # ① 漏片尺子必须看得见红：发现被切断（模拟发现/glob 坏掉）⇒ 真片文件被点名。
                self.assertEqual(
                    _missing_slice_files(gap_dir, discover=lambda directory=None: []),
                    ["ok_ops__w92.py"],
                    "漏片尺子看不见红（发现为空却没人被点名）",
                )
                # ② 同口径：分母只数片文件，非片模块（含已声明的 i18n_aliases.py）不算漏片。
                self.assertEqual([p.name for p in _on_disk_slice_files(gap_dir)],
                                 ["ok_ops__w92.py"], "分母必须只数片文件")
                self.assertEqual(_missing_slice_files(gap_dir), [],
                                 f"已声明的非片文件不许被误判成漏片: {_missing_slice_files(gap_dir)}")
                # ③ 未声明的非片 *.py 必须点名报红（不静默忽略），已声明的逐条列账。
                self.assertEqual(_unexpected_non_slice_files(gap_dir), ["stray_module.py"],
                                 "未声明的非片文件必须被点名（尺子看得见红）")
                self.assertEqual(_declared_non_slice_files_present(gap_dir), ["i18n_aliases.py"],
                                 "已声明的非片文件必须能被显式列出")
            finally:
                shutil.rmtree(gap_dir, ignore_errors=True)
            probe = _probe_docs()
            self.assertTrue(probe)
            self.assertTrue(all(isinstance(name, str) and doc is not None for name, doc in probe))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- 自报告 / 红证


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest-red" in argv:
        return _selftest_red()
    tmpdir = tempfile.mkdtemp(prefix="cadcore-features-smoke-")
    try:
        slice_paths, scan = _scan_all(tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    summary = _summarize(slice_paths, scan)
    for entry in scan:
        print(
            f"SLICE {entry['stem']:<32} family={entry['family']:<22} ops={entry['ops']:<3} "
            f"verdict={entry['verdict']:<4} bad_ops={entry['bad_ops'][:4]}"
        )
        for problem in entry["problems"]:
            print(f"  PROBLEM {problem}")
        for report in entry["op_reports"]:
            if not report["ok"]:
                print(f"  VIOLATION {report['op']}: {report['detail']} {report['outcomes']}")
    print(
        "FEATURES_SMOKE "
        f"slices={summary['slices']} paths={summary['paths']} ops={summary['ops']} "
        f"ops_ok={summary['ops_ok']} outcomes={summary['outcomes']} "
        f"verdict={'PASS' if not summary['bad_slices'] else 'FAIL'}"
    )
    return 0 if not summary["bad_slices"] else 1


def _selftest_red():
    """尺子看得见红的两跑实证：真片内存里换成违规 op ⇒ 报红；撤掉 ⇒ 回绿。"""
    tmpdir = tempfile.mkdtemp(prefix="cadcore-features-red-")
    rc = 0
    try:
        # A) 真片里的一个 op 换成 `return 42` / 抛 ValueError / 抛 KeyError，判红后再撤掉。
        path = FEATURES.iter_slice_paths()[0]
        module = FEATURES.load_one(path)
        op_name = sorted(module.OPERATIONS)[0]
        original = module.OPERATIONS[op_name]
        baseline = _audit_module(module, tmpdir)
        print(f"RED-PROOF baseline slice={path.stem} op={op_name} "
              f"verdict={baseline['verdict']} bad_ops={baseline['bad_ops'][:4]}")

        def _raiser(exc_type):
            def _fn(sk_or_doc, **kwargs):
                raise exc_type("selftest-red")
            return _fn

        mutants = {
            "return_42": lambda sk_or_doc, **kwargs: 42,
            "raise_valueerror": _raiser(ValueError),
            "raise_keyerror": _raiser(KeyError),
        }
        for label, mutant in mutants.items():
            module.OPERATIONS[op_name] = mutant
            entry = _audit_module(module, tmpdir)
            report = next(r for r in entry["op_reports"] if r["op"] == op_name)
            print(f"RED-PROOF mutant={label} slice={path.stem} op={op_name} "
                  f"verdict={entry['verdict']} bad_ops={entry['bad_ops'][:4]} "
                  f"detail={report['detail']}")
            if entry["verdict"] != "FAIL" or op_name not in entry["bad_ops"]:
                print(f"RED-PROOF-FAILED mutant={label}: 尺子没看见红")
                rc = 1
            module.OPERATIONS[op_name] = original
        restored = _audit_module(module, tmpdir)
        report = next(r for r in restored["op_reports"] if r["op"] == op_name)
        print(f"RED-PROOF restored slice={path.stem} op={op_name} verdict={restored['verdict']} "
              f"bad_ops={restored['bad_ops'][:4]} detail={report['detail']}")
        if op_name in restored["bad_ops"]:
            print("RED-PROOF-FAILED: 撤掉违规 op 后仍红")
            rc = 1

        # B) 临时目录里落一个违规片文件 ⇒ 报红；撤掉换成合规片 ⇒ 回绿。
        red_dir = Path(tmpdir) / "slices"
        red_dir.mkdir()
        red_file = red_dir / "mutant_ops__w90.py"
        red_file.write_text(
            "FAMILY = 'mutant-ops'\nOPERATIONS = {'mutant_op': lambda sk_or_doc, **kw: 42}\n",
            encoding="utf-8",
        )
        _, scan = _scan_all(tmpdir, red_dir)
        red_entry = scan[0] if scan else {"verdict": "MISSING", "bad_ops": [], "detail": ""}
        print(f"RED-PROOF file={red_file.name} slices={len(scan)} verdict={red_entry['verdict']} "
              f"bad_ops={red_entry['bad_ops'][:4]}")
        if not scan or red_entry["verdict"] != "FAIL":
            print("RED-PROOF-FAILED: 违规片文件没报红")
            rc = 1
        red_file.unlink()  # 撤掉违规片
        green_file = red_dir / "good_ops__w91.py"
        green_file.write_text(
            "FAMILY = 'good-ops'\n"
            "OPERATIONS = {'good_op': lambda sk_or_doc, **kw: "
            "['11111111-1111-4111-8111-111111111111']}\n",
            encoding="utf-8",
        )
        _, scan = _scan_all(tmpdir, red_dir)
        green_entry = scan[0] if scan else {"verdict": "MISSING", "bad_ops": [], "detail": ""}
        print(f"RED-PROOF reverted file={green_file.name} slices={len(scan)} "
              f"verdict={green_entry['verdict']} bad_ops={green_entry['bad_ops'][:4]}")
        if not scan or green_entry["verdict"] != "PASS":
            print("RED-PROOF-FAILED: 撤掉违规片后没回绿")
            rc = 1
        print(f"RED-PROOF verdict={'PASS' if rc == 0 else 'FAIL'}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(_main())
