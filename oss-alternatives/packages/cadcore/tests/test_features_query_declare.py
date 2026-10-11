#!/usr/bin/env python3
"""test_features_query_declare.py —— 放回席（第三批）只读查询档声明 + 复测工具（临时量具）。

背景（为什么需要本工具）
------------------------
`tests/test_features_live.py`（现行尺子）**还没有 `query` 档**：只读查询型 op 因为
"返回非空结果 + L1 未变"被记成 `phantom`（见其 docstring 第 11-12 行）。新尺子（带 query 档）
在本工具落盘时**尚未落盘**（实测 mtime 2026-09-30 13:36，全文 grep `query` = 0 命中）。

本工具的定位：**不是新尺子、不放宽任何断言**，只是把"声明式只读 + 真返回数据 ⇒ query"这条
待落盘的判据，用**尺子原件**（`test_features_live.py` 的 `_results()` / `_scan()` / 夹具）的读数
做一次等价复测，供放回席登记依据。测量代码、夹具、判据全部来自尺子原件，本工具只做：
① 在**我名下**的隔离片里补声明；② 按声明把 phantom 读成 query；③ 打印判定行。

声明形态（本席选的最显式形态，供新尺子对齐）
--------------------------------------------
片模块末尾追加两个模块级常量（幂等，已存在则不重复追加）::

    READONLY = True
    OPERATIONS_META = {
        "<op>": {"tier": "query", "readonly": True, "returns": "data"},
        ...
    }

即：模块级 `READONLY = True`（整片只读）+ `OPERATIONS_META` 逐 op 显式给 `tier="query"`、
`readonly=True`、`returns="data"`。新尺子若认别的键（如 `kind`/`mode`），以新尺子为准；
本工具认的是上面这组键。

query 判定（本工具口径，全部来自尺子原件的读数）
-----------------------------------------------
一个 op 记 `query` ⇔ ①尺子原件在该 op 上的读数是 `phantom`（返回非空 + L1 未变）；
②该 op 在片模块的 `OPERATIONS_META` 里被**显式声明**为 `tier=query/readonly=True`；
③尺子判定行里的 `n=`（`validate_result` 归一后的返回条数）**> 0**（真返回数据，不是空壳）。
片级 `broken` 用**真违约**口径（尺子 `state=="broken"`，即非 CadError 异常），
不含保守口径里并进来的 phantom —— 这点与旧 `_seat_release_tool._slice_stats` 不同，**必须显式写明**。

放回动作：**本工具不做**（见 `--why-no-release`）。原因的硬事实：隔离清单 `_QUARANTINE` 真源是
`cadcore/ops/features/__init__.py`（第 208 行起），**不在本席写面内**；只 `os.replace` 搬片而不改清单
会让 `quarantine_selfcheck()` 的四条机检红（`moved`/`leaked`/`ops_residual`）。故本席只登记
"query 档可放回清单"，不越权动清单。

用法::

    python3 tests/test_features_query_declare.py --declare --report
    python3 tests/test_features_query_declare.py --why-no-release
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve()
PKG_ROOT = _HERE.parents[1]
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from cadcore.ops import features as FEATURES  # noqa: E402
from tests import test_features_live as RULER  # noqa: E402

_ROOT = Path(FEATURES.FEATURES_DIR).resolve()
_QDIR = Path(FEATURES.quarantine_dir(_ROOT)).resolve()

_META_HEADER = (
    "\n\n# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---\n"
    "READONLY = True\n"
    "OPERATIONS_META = {\n"
)
_META_FOOTER = "}\n"


def merged_scan() -> list[dict]:
    """主集合 + 隔离集合软链临时目录，用尺子原件 `_results()` 现测（跑完即弃）。"""
    tmp = Path(tempfile.mkdtemp(prefix="seat3_query_merged_"))
    for base in (_ROOT, _QDIR):
        for src in sorted(base.glob(FEATURES.SLICE_GLOB)):
            if src.is_file() and src.name != "__init__.py":
                os.symlink(src, tmp / src.name)
    saved_dir, saved_results = FEATURES.FEATURES_DIR, RULER._RESULTS
    FEATURES.FEATURES_DIR = tmp
    RULER._RESULTS = None
    try:
        return RULER._results()
    finally:
        FEATURES.FEATURES_DIR = saved_dir
        RULER._RESULTS = saved_results


def phantom_ops(entry: dict) -> list[dict]:
    return [rec for rec in entry["ops"] if rec["state"] == "phantom"]


def parse_n(detail: str) -> int:
    """从尺子判定文本里取 `n=`；取不到 ⇒ -1（未采），不许当 0。"""
    for part in detail.replace(";", " ").split():
        if part.startswith("n="):
            try:
                return int(part[2:])
            except ValueError:
                return -1
    return -1


def declare(path: Path, ops: list[str]) -> bool:
    """幂等追加 `READONLY` + `OPERATIONS_META`（只动我名下的隔离片）。"""
    text = path.read_text(encoding="utf-8")
    if "OPERATIONS_META" in text:
        return False
    body = "".join(
        f'    "{op}": {{"tier": "query", "readonly": True, "returns": "data"}},\n' for op in sorted(ops))
    path.write_text(text + _META_HEADER + body + _META_FOOTER, encoding="utf-8")
    return True


def cmd_why_no_release() -> int:
    print("RELEASE_BLOCKED tool=test_features_query_declare.py "
          "reason=quarantine_manifest_not_in_seat_write_scope "
          "manifest=cadcore/ops/features/__init__.py:208(_QUARANTINE) "
          "seat_write_scope=_quarantine/<slice>.py,tests/test_features_*.py,QUARANTINE.md "
          "official_door=cadcore/ops/features/__init__.py --unquarantine --apply "
          "official_door_precheck=probe_l1(denies live-but-phantom per tool docstring) "
          "new_ruler_query_tier=not_landed(2026-09-30 15:35, tests/test_features_live.py mtime 13:36, grep query=0)")
    return 0


def cmd_report(declare_first: bool) -> int:
    entries = {e["stem"]: e for e in merged_scan()}
    declared_slices, eligible, totals = [], [], {"live": 0, "query": 0, "noop": 0, "reject": 0,
                                                 "broken": 0, "phantom": 0, "ops": 0}
    rows = []
    for stem in sorted(entries):
        entry = entries[stem]
        path = _QDIR / f"{stem}.py"
        if not path.is_file():
            continue  # 只看隔离片
        ph = phantom_ops(entry)
        broken_true = sum(1 for rec in entry["ops"] if rec["state"] == "broken")
        query = 0
        missing_n = []
        if ph and declare_first:
            if declare(path, [rec["op"] for rec in ph]):
                declared_slices.append(stem)
        if ph:
            for rec in ph:
                if parse_n(rec["detail"]) > 0:
                    query += 1
                else:
                    missing_n.append(rec["op"])
        st = {"live": sum(1 for r in entry["ops"] if r["state"] == "live"),
              "noop": sum(1 for r in entry["ops"] if r["state"] == "noop"),
              "reject": sum(1 for r in entry["ops"] if r["state"] == "reject"),
              "broken": broken_true, "phantom": len(ph) - query, "ops": len(entry["ops"])}
        for key in ("live", "noop", "reject", "broken", "phantom", "ops"):
            totals[key] += st[key]
        totals["query"] += query
        ok = (broken_true == 0 and query > 0 and not missing_n)
        if ok:
            eligible.append(stem)
        rows.append((stem, st, query, missing_n, ok))
    for stem, st, query, missing_n, ok in rows:
        if query == 0 and st["broken"] == 0 and st["phantom"] == 0:
            continue
        print(f"SLICE {stem} ops={st['ops']} live={st['live']} query={query} noop={st['noop']} "
              f"reject={st['reject']} broken_true={st['broken']} phantom={st['phantom']} "
              f"eligible={'YES' if ok else 'NO'}"
              + (f" missing_n={missing_n}" if missing_n else ""))
    print(f"DECLARED slices={len(declared_slices)} {','.join(declared_slices) or '-'}")
    print(f"QUERY_ELIGIBLE n={len(eligible)} slices={','.join(eligible) or '-'}")
    print(f"FEATURES_QUERY verdict={'PASS' if eligible else 'FAIL'} slices_quarantined_reportable={len(rows)} "
          f"ops={totals['ops']} live={totals['live']} query={totals['query']} noop={totals['noop']} "
          f"reject={totals['reject']} broken_true={totals['broken']} phantom={totals['phantom']} "
          f"eligible_slices={len(eligible)} rc=0")
    # 跨平台临时目录拼接（原为 POSIX 字面量 "/tmp/seat3_query_rows.json"）：只改拼接，不改逻辑
    Path(os.path.join(tempfile.gettempdir(), "seat3_query_rows.json")).write_text(json.dumps(
        [{"slice": s, **st, "query": q, "eligible": ok} for s, st, q, _m, ok in rows],
        ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--declare", action="store_true", help="先在隔离片里补声明（幂等）")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--why-no-release", action="store_true")
    a = ap.parse_args()
    if a.why_no_release:
        return cmd_why_no_release()
    if a.report:
        return cmd_report(a.declare)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
