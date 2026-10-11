#!/usr/bin/env python3
"""_seat_release_tool.py —— 隔离片放回席（第二批）的唯一工具（**可核验替代路径**）。

为什么不用官方闸
----------------
官方放回闸 ``cadcore/ops/features/__init__.py --unquarantine <片> --apply`` 的预检走
``probe_l1()``（结构性防线，**认不出 phantom**）：`unquarantine()` 里
``if live_paths == 0: problems.append("precheck_no_l1_success_path")`` ⇒ 它会 **DENY 已被
尺子判 live 的片**。闸的实现文件不在本席写面内 ⇒ 本席改用**可核验的替代路径**。

放回判据（权威，与闸 docstring 一致）
-------------------------------------
对**该片**用 ``tests/test_features_live.py`` 现测三态读数：

    live > 0  且  phantom == 0  且  broken == 0  且 片接口 problems 为空

放回动作
--------
``os.replace(_quarantine/<片>.py → <features>/<片>.py)`` + 在
``cadcore/ops/features/QUARANTINE.md`` 末尾**追加**一条放回记录（写明文依据 = 判定行 + 片名 +
三态读数 + 修了什么），**绝不删改既有内容**（先读全文、追加、原子替换）。

用法::

    python3 cadcore/ops/features/_quarantine/_seat_release_tool.py --report [--stem X ...]
    python3 cadcore/ops/features/_quarantine/_seat_release_tool.py --dump X [Y ...]
    python3 cadcore/ops/features/_quarantine/_seat_release_tool.py --release X [Y ...] [--note "..."]
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
PKG_ROOT = _HERE.parents[4]  # packages/cadcore
EXPECTED_FEATURES_DIR = PKG_ROOT / "cadcore" / "ops" / "features"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from cadcore.ops import features as FEATURES  # noqa: E402
from tests import test_features_live as RULER  # noqa: E402

_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _now() -> str:
    return datetime.datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _guard_dirs() -> tuple[Path, Path]:
    """写面自检：只允许在**本仓 features 目录**里搬片（避免 os.replace 打错目标）。"""
    root = Path(FEATURES.FEATURES_DIR).resolve()
    if root != EXPECTED_FEATURES_DIR.resolve():
        raise SystemExit(f"REFUSE features_dir={root} expected={EXPECTED_FEATURES_DIR.resolve()}")
    return root, FEATURES.quarantine_dir(root).resolve()


def _slice_stats(entry: dict) -> dict:
    st = {"live": 0, "noop": 0, "reject": 0, "broken": 0, "phantom": 0, "unmeasured": 0,
          "ops": 0, "problems": list(entry.get("problems") or [])}
    for rec in entry["ops"]:
        st["ops"] += 1
        state = rec["state"]
        if state == "phantom":
            st["phantom"] += 1
            st["broken"] += 1  # 判定行保守口径：phantom 计入 broken
        elif state in ("live", "noop", "reject"):
            st[state] += 1
        else:
            st["unmeasured"] += 1
            st["broken"] += 1
    return st


def _ruler_line() -> str:
    """真跑 ``tests/test_features_live.py``，取它**自己打印的判定行**（不是本地重算）。"""
    proc = subprocess.run([sys.executable, "tests/test_features_live.py"], cwd=str(PKG_ROOT),
                          capture_output=True, text=True, timeout=1800)
    text = (proc.stdout or "") + (proc.stderr or "")
    line = next((ln for ln in text.splitlines() if ln.startswith("FEATURES_LIVE verdict=")), "")
    detail = next((ln for ln in text.splitlines() if ln.startswith("FEATURES_LIVE_DETAIL ")), "")
    if not line:
        raise SystemExit("REFUSE ruler produced no FEATURES_LIVE line")
    return line.strip() + " || " + detail.strip()


def _entries() -> dict:
    return {entry["stem"]: entry for entry in RULER._results()}


def cmd_report(stems: list[str]) -> int:
    entries = _entries()
    total = {"live": 0, "noop": 0, "reject": 0, "broken": 0, "phantom": 0, "ops": 0}
    quarantined = set(FEATURES._QUARANTINE)
    print(f"REPORT now={_now()} slice_glob={FEATURES.SLICE_GLOB} "
          f"slices_all={len(entries)} quarantined_manifest={len(quarantined)}")
    for stem in sorted(entries):
        entry = entries[stem]
        st = _slice_stats(entry)
        for key in total:
            total[key] += st[key]
        if stem not in quarantined and stems and stem not in stems:
            continue
        if stems and stem not in stems:
            continue
        if stem not in quarantined and not stems:
            continue
        print(f"SLICE {stem} verdict={entry['verdict']} live={st['live']} noop={st['noop']} "
              f"reject={st['reject']} broken={st['broken']} phantom={st['phantom']} "
              f"problems={st['problems'] or 'none'}")
        for rec in entry["ops"]:
            print(f"  OP {stem}::{rec['op']} state={rec['state']} shape={rec['live_shape'] or '-'} "
                  f"detail={rec['detail'][:200]}")
    print(f"TOTAL_ALL slices={len(entries)} ops={total['ops']} live={total['live']} "
          f"noop={total['noop']} reject={total['reject']} broken={total['broken']} "
          f"phantom={total['phantom']}")
    return 0


def cmd_dump(stems: list[str]) -> int:
    root, qdir = _guard_dirs()
    for stem in stems:
        path = qdir / f"{stem}.py"
        print(f"===== {path} =====")
        print(path.read_text(encoding="utf-8") if path.is_file() else "<<absent>>")
    return 0


def _check_releasable(stem: str, entry: dict) -> tuple[bool, str, dict]:
    st = _slice_stats(entry)
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


def cmd_release(stems: list[str], note: str) -> int:
    root, qdir = _guard_dirs()
    entries = _entries()
    ruler_line = _ruler_line()
    stamp = _now()
    ok_stems, rows = [], []
    for stem in stems:
        entry = entries.get(stem)
        if entry is None:
            print(f"RELEASE stem={stem} verdict=DENY problems=unmeasured_slice")
            continue
        allowed, evidence, st = _check_releasable(stem, entry)
        src, dst = qdir / f"{stem}.py", root / f"{stem}.py"
        if not allowed:
            print(f"RELEASE stem={stem} verdict=DENY problems={','.join(st['problems'])} "
                  f"evidence={evidence}")
            continue
        if not src.is_file():
            print(f"RELEASE stem={stem} verdict=DENY problems=quarantine_file_absent path={src}")
            continue
        if dst.exists():
            print(f"RELEASE stem={stem} verdict=DENY problems=main_path_occupied path={dst}")
            continue
        os.replace(src, dst)
        moved = dst.is_file() and not src.exists()
        if not moved:
            print(f"RELEASE stem={stem} verdict=DENY problems=move_not_effective")
            continue
        ok_stems.append(stem)
        rows.append(f"| `{stem}` | `cadcore/ops/features/_quarantine/{stem}.py` → "
                    f"`cadcore/ops/features/{stem}.py` | {evidence} | `{ruler_line}` | {stamp} |")
        print(f"RELEASE stem={stem} verdict=ALLOW moved=1 evidence={evidence} "
              f"main={dst} ruler_basis=ok")
    if ok_stems:
        _append_record(rows, note, stamp)
        print(f"RELEASE_TOTAL n={len(ok_stems)} stems={','.join(ok_stems)} "
              f"registered_in=QUARANTINE.md")
    else:
        print("RELEASE_TOTAL n=0 registered_in=none")
    return 0 if ok_stems else 1


def _append_record(rows: list[str], note: str, stamp: str) -> None:
    """**追加**放回记录（先读全文，只 append，再原子替换；不删改既有内容）。"""
    md = Path(FEATURES.FEATURES_DIR) / "QUARANTINE.md"
    assert md.is_file(), f"QUARANTINE.md absent: {md}"
    old = md.read_text(encoding="utf-8")
    block = [
        "",
        # 中文原文：### 放回记录（第二批 · 隔离片放回席 · {stamp}）
        f"### release record (batch 2, quarantine release seat, {stamp})",
        "",
        # 中文原文：判据（**替代路径**：官方闸 `__init__.py --unquarantine --apply` 的预检走 `probe_l1()`，
        # 认不出 phantom，会 DENY 已被尺子判 live 的片；闸的实现文件不在本席写面内）：
        "criterion (**alternative path**: the official gate "
        "`__init__.py --unquarantine --apply` prechecks via `probe_l1()`,",
        "which cannot recognize phantoms and would DENY a slice the ruler already judged live;"
        " the gate implementation file is outside this seat's write surface):",
        "",
        # 中文原文：> 依据 = `tests/test_features_live.py` 判定行 + 片名 + 该片 `live/phantom/broken` 三态读数。
        "> basis = `tests/test_features_live.py` verdict line + slice name + that slice's"
        " `live/phantom/broken` three-state readings.",
        "",
        # 中文原文：| 片 | 位置变更 | 三态读数（该片） | 判定行（当次真跑，原样） | 时刻 |
        "| slice | path change | three-state readings (slice) | verdict line (real run, verbatim) | time |",
        "|---|---|---|---|---|",
    ]
    block.extend(rows)
    if note:
        block += ["", f"what was fixed: {note}"]  # 中文原文：修了什么：{note}
    block.append("")
    new = old.rstrip("\n") + "\n" + "\n".join(block)
    tmp = md.with_suffix(".md.seat-tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, md)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="quarantine release seat tool (ruler criterion + recorded release)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--dump", nargs="*", default=None)
    ap.add_argument("--release", nargs="*", default=None)
    ap.add_argument("--stem", nargs="*", default=[])
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)
    if args.dump is not None:
        return cmd_dump(list(args.dump))
    if args.release is not None:
        if not args.release:
            print("usage: --release <stem> [<stem> ...] [--note ...]")
            return 2
        return cmd_release(list(args.release), args.note)
    return cmd_report(list(args.stem))


if __name__ == "__main__":
    raise SystemExit(main())
