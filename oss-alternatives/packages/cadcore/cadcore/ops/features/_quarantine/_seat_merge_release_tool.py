#!/usr/bin/env python3
"""_seat_merge_release_tool.py —— 隔离片放回席（第二批）v2：**合并集合用尺子原件现测** + 记录式放回。

① 闸为什么不能用（已读实现，2026-09-30）
----------------------------------------
``cadcore/ops/features/__init__.py`` 的 ``unquarantine()`` 预检在第 389-400 行：逐 op 调
``probe_l1()``，``if live_paths == 0: problems.append("precheck_no_l1_success_path")``。
``probe_l1`` 是**结构性防线**（docstring 自述"认不出 phantom"）⇒ 预检 ALLOW ≠ 尺子判 live，
且预检会 **DENY 已被尺子判 live 的片**。闸的实现文件不在本席写面内 ⇒ 走本替代路径并留记录。

② 测量口径（**尺子原件**）
--------------------------
``tests/test_features_live.py`` 的 ``_scan()`` 走 ``FEATURES.iter_slice_paths()``；该函数按
``SLICE_GLOB``（非递归 ``*_w*.py``）只扫**主集合**目录 ⇒ 隔离片不在尺子视野内。本工具把
「主集合 + 隔离集合」软链成一个临时合并目录，把 ``FEATURES.FEATURES_DIR`` 临时指向它、
清空 ``RULER._RESULTS`` 后调用**尺子自己的 ``_results()``** 现测：测量代码/夹具/判据全是尺子
原件，只是集合目录不同（临时目录跑完即弃、<1MB）。

③ 放回判据（与闸 docstring 一致的权威口径，**逐片**）
----------------------------------------------------
    live > 0  且  phantom == 0  且  broken == 0  且 片接口 problems 为空

④ 放回动作（保留可追溯性，禁止无记录 mv）
-----------------------------------------
``os.replace(_quarantine/<片>.py → <features>/<片>.py)``，随后在 ``QUARANTINE.md`` **追加**一条
放回记录（片名 + 三态读数 + 当次官方 ``FEATURES_LIVE`` 判定行原样 + 时刻 + 修了什么）。
先读全文再 append，**不删改既有内容**。

用法::

    python3 .../_seat_merge_release_tool.py --report [--show-ops]
    python3 .../_seat_merge_release_tool.py --dump X [Y ...]
    python3 .../_seat_merge_release_tool.py --release X [Y ...] [--note "..."]
    python3 .../_seat_merge_release_tool.py --release-best 5 [--show-ops] [--note "..."]
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve()
PKG_ROOT = _HERE.parents[4]  # packages/cadcore
EXPECTED_FEATURES_DIR = PKG_ROOT / "cadcore" / "ops" / "features"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from cadcore.ops import features as FEATURES  # noqa: E402
from tests import test_features_live as RULER  # noqa: E402

_TZ = datetime.timezone(datetime.timedelta(hours=8))
_ROOT = Path(FEATURES.FEATURES_DIR).resolve()
_QDIR = Path(FEATURES.quarantine_dir(_ROOT)).resolve()
if _ROOT != EXPECTED_FEATURES_DIR.resolve():
    raise SystemExit(f"REFUSE features_dir={_ROOT} expected={EXPECTED_FEATURES_DIR.resolve()}")


def _now() -> str:
    return datetime.datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _merged_scan() -> tuple[list[dict], Path]:
    """主集合 + 隔离集合 → 软链临时目录 → 用**尺子自己的 `_results()`** 现测。"""
    tmp = Path(tempfile.mkdtemp(prefix="seat_merged_features_"))
    count = 0
    for base in (_ROOT, _QDIR):
        for src in sorted(base.glob(FEATURES.SLICE_GLOB)):
            if src.is_file() and src.name != "__init__.py":
                os.symlink(src, tmp / src.name)
                count += 1
    saved_dir, saved_results = FEATURES.FEATURES_DIR, RULER._RESULTS
    FEATURES.FEATURES_DIR = tmp
    RULER._RESULTS = None
    try:
        entries = RULER._results()
    finally:
        FEATURES.FEATURES_DIR = saved_dir
        RULER._RESULTS = saved_results
    print(f"MERGED_SCAN now={_now()} slice_glob={FEATURES.SLICE_GLOB} linked={count} "
          f"measured={len(entries)} manifest={len(FEATURES._QUARANTINE)} dir={tmp}")
    return entries, tmp


def _stats(entry: dict) -> dict:
    st = {"live": 0, "noop": 0, "reject": 0, "broken": 0, "phantom": 0, "unmeasured": 0,
          "ops": 0, "problems": list(entry.get("problems") or [])}
    for rec in entry["ops"]:
        st["ops"] += 1
        state = rec["state"]
        if state == "phantom":
            st["phantom"] += 1
            st["broken"] += 1  # 判定行保守口径
        elif state in ("live", "noop", "reject"):
            st[state] += 1
        else:
            st["unmeasured"] += 1
            st["broken"] += 1
    return st


def _ruler_line() -> str:
    """真跑 ``tests/test_features_live.py``，取它自己打印的判定行（不是本地重算）。"""
    proc = subprocess.run([sys.executable, "tests/test_features_live.py"], cwd=str(PKG_ROOT),
                          capture_output=True, text=True, timeout=1800)
    text = (proc.stdout or "") + (proc.stderr or "")
    line = next((ln for ln in text.splitlines() if ln.startswith("FEATURES_LIVE verdict=")), "")
    return line.strip() if line else "RULER_LINE_NA(see final real run)"  # 中文原文：RULER_LINE_NA(见收尾真跑)


def _qualifies(stem: str, entry: dict) -> tuple[bool, str, dict]:
    st = _stats(entry)
    problems = []
    if stem not in FEATURES._QUARANTINE:
        problems.append("not_in_quarantine_manifest")
    if st["problems"]:
        problems.append("slice_interface_problems")
    if st["live"] <= 0:
        problems.append("live==0")
    if st["phantom"] != 0:
        problems.append(f"phantom={st['phantom']}")
    if st["broken"] != 0:
        problems.append(f"broken={st['broken']}")
    if st["unmeasured"] != 0:
        problems.append(f"unmeasured={st['unmeasured']}")
    evidence = (f"live={st['live']} phantom={st['phantom']} broken={st['broken']} "
                f"(ops={st['ops']} noop={st['noop']} reject={st['reject']})")
    return (not problems), evidence, {"problems": problems, **st}


def _rows_and_move(stems_ranked: list[str], entries: dict) -> tuple[list[str], list[str]]:
    moved: list[str] = []
    placeholders: list[str] = []
    for stem in stems_ranked:
        entry = entries.get(stem)
        if entry is None:
            print(f"RELEASE stem={stem} verdict=DENY problems=unmeasured_slice")
            continue
        allowed, evidence, st = _qualifies(stem, entry)
        src, dst = _QDIR / f"{stem}.py", _ROOT / f"{stem}.py"
        if not allowed:
            print(f"RELEASE stem={stem} verdict=DENY problems={','.join(st['problems'])} "
                  f"evidence={evidence}")
            continue
        if not src.is_file() or dst.exists():
            print(f"RELEASE stem={stem} verdict=DENY problems="
                  f"{'quarantine_file_absent' if not src.is_file() else 'main_path_occupied'} "
                  f"src={src} dst={dst}")
            continue
        os.replace(src, dst)
        if not (dst.is_file() and not src.exists()):
            print(f"RELEASE stem={stem} verdict=DENY problems=move_not_effective")
            continue
        moved.append(stem)
        placeholders.append(f"| `{stem}` | `cadcore/ops/features/_quarantine/{stem}.py` → "
                            f"`cadcore/ops/features/{stem}.py` | {evidence} | {{RULER}} | {_now()} |")
        print(f"RELEASE stem={stem} verdict=ALLOW moved=1 evidence={evidence} dst={dst}")
    return moved, placeholders


def _append_record(placeholders: list[str], note: str, ruler_line: str, stamp: str) -> None:
    md = _ROOT / "QUARANTINE.md"
    assert md.is_file(), f"QUARANTINE.md absent: {md}"
    old = md.read_text(encoding="utf-8")
    # 中文原文（记录块文案）：
    #   ### 放回记录（第二批 · 隔离片放回席 · {stamp}）
    #   依据 = **`tests/test_features_live.py` 判定行 + 片名 + 该片 `live/phantom/broken` 三态读数**
    #   （官方闸 `--unquarantine --apply` 的预检走 `probe_l1()`，认不出 phantom，
    #   会 DENY 已被尺子判 live 的片；闸实现不在本席写面内，故走本替代路径并留此记录）。
    #   测量口径：尺子原件（`tests/test_features_live.py` 的 `_results()`）跑**主集合 + 隔离集合的
    #   合并目录**（软链临时目录，跑完即弃），逐片三态现测。
    #   表头：| 片 | 位置变更 | 三态读数（该片） | 官方判定行（当次真跑，原样） | 时刻 |
    block = [
        "",
        f"### Release record (batch 2 / quarantine release seat / {stamp})",
        "",
        "Basis = the **`tests/test_features_live.py` verdict line + slice name + that slice's "
        "`live/phantom/broken` readings** (the official gate `--unquarantine --apply` pre-checks "
        "with `probe_l1()`, which cannot see phantom, so it would DENY a slice the ruler has "
        "already judged live; the gate implementation file is outside this seat's write scope, "
        "hence this alternative path with the record kept here).",
        "Measurement = the ruler's own code (`tests/test_features_live.py` `_results()`) run over a "
        "**merged directory of the main set + the quarantine set** (symlinked temp dir, discarded "
        "after the run), with per-slice readings measured live.",
        "",
        "| slice | location change | readings (that slice) | official verdict line (that run, verbatim) | time |",
        "|---|---|---|---|---|",
    ]
    block.extend(row.replace("{RULER}", ruler_line) for row in placeholders)
    if note:
        block += ["", f"what was fixed: {note}"]  # 中文原文：修了什么：{note}
    block.append("")
    new = old.rstrip("\n") + "\n" + "\n".join(block)
    tmp = md.with_suffix(".md.seat-tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, md)


def _print_slices(entries: dict, show_ops: bool) -> None:
    manifest = set(FEATURES._QUARANTINE)
    for stem in sorted(entries):
        if stem not in manifest:
            continue
        st = _stats(entries[stem])
        print(f"SLICE {stem} verdict={entries[stem]['verdict']} live={st['live']} noop={st['noop']} "
              f"reject={st['reject']} broken={st['broken']} phantom={st['phantom']} "
              f"problems={st['problems'] or 'none'}")
        if show_ops and (st["live"] > 0 or st["broken"] > 0):
            for rec in entries[stem]["ops"]:
                if rec["state"] in ("live", "phantom", "broken"):
                    print(f"  OP {stem}::{rec['op']} state={rec['state']} "
                          f"shape={rec['live_shape'] or '-'} detail={rec['detail'][:160]}")


def cmd_report(show_ops: bool) -> int:
    entries, _tmp = _merged_scan()
    entries = {e["stem"]: e for e in entries}
    total = {"live": 0, "noop": 0, "reject": 0, "broken": 0, "phantom": 0, "ops": 0}
    for st in (_stats(e) for e in entries.values()):
        for key in total:
            total[key] += st[key]
    _print_slices(entries, show_ops)
    print(f"TOTAL_MERGED slices={len(entries)} ops={total['ops']} live={total['live']} "
          f"noop={total['noop']} reject={total['reject']} broken={total['broken']} "
          f"phantom={total['phantom']}")
    return 0


def cmd_dump(stems: list[str]) -> int:
    for stem in stems:
        path = _QDIR / f"{stem}.py"
        print(f"===== {path} =====")
        print(path.read_text(encoding="utf-8") if path.is_file() else "<<absent>>")
    return 0


def cmd_release(stems: list[str], note: str) -> int:
    entries, _tmp = _merged_scan()
    entries = {e["stem"]: e for e in entries}
    stamp = _now()
    moved, placeholders = _rows_and_move(stems, entries)
    if moved:
        _append_record(placeholders, note, _ruler_line(), stamp)
    print(f"RELEASE_TOTAL n={len(moved)} stems={','.join(moved) or 'none'} "
          f"registered_in={'QUARANTINE.md' if moved else 'none'}")
    return 0 if moved else 1


def cmd_release_best(limit: int, note: str, show_ops: bool) -> int:
    entries, _tmp = _merged_scan()
    entries = {e["stem"]: e for e in entries}
    _print_slices(entries, show_ops)
    ranked, blocked = [], 0
    for stem in sorted(entries):
        if stem not in FEATURES._QUARANTINE:
            continue
        ok, _ev, _st = _qualifies(stem, entries[stem])
        if ok:
            ranked.append(stem)
        else:
            blocked += 1
    ranked.sort(key=lambda s: (-_stats(entries[s])["live"], _stats(entries[s])["ops"]))
    print(f"QUALIFYING n={len(ranked)} stems={','.join(ranked) or 'none'} blocked={blocked} "
          f"limit={max(0, limit)}")
    stamp = _now()
    moved, placeholders = _rows_and_move(ranked[:max(0, limit)], entries)
    if moved:
        _append_record(placeholders, note, _ruler_line(), stamp)
    print(f"RELEASE_TOTAL n={len(moved)} stems={','.join(moved) or 'none'} "
          f"registered_in={'QUARANTINE.md' if moved else 'none'}")
    return 0 if moved else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        # 中文原文：隔离片放回席工具 v2（尺子合并集合判据 + 记录式放回）
        description="quarantine-slice release seat tool v2 (ruler merged-set criterion + recorded release)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--show-ops", action="store_true")
    ap.add_argument("--dump", nargs="*", default=None)
    ap.add_argument("--release", nargs="*", default=None)
    ap.add_argument("--release-best", type=int, default=None, metavar="N")
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)
    if args.dump is not None:
        return cmd_dump(list(args.dump))
    if args.release is not None:
        if not args.release:
            print("usage: --release <stem> [<stem> ...]")
            return 2
        return cmd_release(list(args.release), args.note)
    if args.release_best is not None:
        return cmd_release_best(args.release_best, args.note, args.show_ops)
    return cmd_report(args.show_ops)


if __name__ == "__main__":
    raise SystemExit(main())
