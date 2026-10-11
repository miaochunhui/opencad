#!/usr/bin/env python3
"""_seat_release_tool_v8.py —— 隔离片放回席（第八批）唯一工具。

口径：**全部走官方件，不重写任何判据**
--------------------------------------
* 逐片读数 = 尺子原件 ``tests/test_features_live.py`` 的 ``_results(SCOPE_QUARANTINE)``
  （同一 loader / 同一 ladder / 同一 query 声明规则）。
* 放回判定 = 官方闸 ``cadcore.ops.features.unquarantine(stem, apply=True)``，其 docstring 的
  **权威五档口径**：``(live>0 ∨ query>0 ∨ write>0) ∧ phantom==0 ∧ broken==0``（片接口 problems 亦红）。
* 放回动作 = **三者全或无**：①闸搬片 ②闸对 ``__init__.py`` 的清单文本手术（备注行删除 +
  加入 ``_MEASURED_MAIN``）③本工具把该片登记进 ``QUARANTINE.md``（读全文 → 追加 → 原子替换）。
  ③失败 ⇒ 还原 ``__init__.py`` 快照 + 把片搬回 ``_quarantine/``（本工具自带回滚，含闸已做的两步）。

用法::

    python3 .../_seat_release_tool_v8.py --report [--show-ops]
    python3 .../_seat_release_tool_v8.py --release [--apply] [--stem X ...] [--note "..."]
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[4]  # packages/cadcore
FEATURES_DIR = PKG_ROOT / "cadcore" / "ops" / "features"
QDIR = FEATURES_DIR / "_quarantine"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from cadcore.ops import features as FEATURES  # noqa: E402
from tests import test_features_live as RULER  # noqa: E402

_TZ = datetime.timezone(datetime.timedelta(hours=8))
_DEFAULT_NOTE = "batch 7 extended fixture + context getters only; no assertion/criterion relaxed"
_KEYS = ("live", "query", "write", "noop", "reject", "broken", "phantom", "unmeasured")


def _now() -> str:
    return datetime.datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _stats(entry: dict) -> dict:
    st = {k: 0 for k in _KEYS}
    for rec in entry["ops"]:
        state = rec["state"]
        st[state if state in st else "unmeasured"] += 1
    st["ops"] = len(entry["ops"])
    st["problems"] = list(entry.get("problems") or [])
    st["success"] = st["live"] + st["query"] + st["write"]
    st["red"] = st["broken"] + st["phantom"] + st["unmeasured"] + len(st["problems"])
    return st


def _entries() -> dict:
    return {e["stem"]: e for e in RULER._results(RULER.SCOPE_QUARANTINE)}


def _verdict_line() -> str:
    proc = subprocess.run([sys.executable, "tests/test_features_live.py"], cwd=str(PKG_ROOT),
                          capture_output=True, text=True, timeout=1800)
    text = (proc.stdout or "") + (proc.stderr or "")
    return next((ln.strip() for ln in text.splitlines()
                 if ln.startswith("FEATURES_LIVE verdict=")), "RULER_LINE_NA")


def cmd_report(stems: list[str], show_ops: bool) -> int:
    entries = _entries()
    manifest = set(FEATURES._QUARANTINE)
    counts = {"qualify": 0, "red": 0, "no_success": 0}
    for stem in sorted(entries):
        if stems and stem not in stems:
            continue
        st = _stats(entries[stem])
        label = ("QUALIFY" if st["red"] == 0 and st["success"] > 0 else
                 "RED" if st["red"] > 0 else "NO_SUCCESS_PATH")
        counts[{"QUALIFY": "qualify", "RED": "red"}.get(label, "no_success")] += 1
        print(f"SLICE {stem} label={label} in_manifest={stem in manifest} "
              f"live={st['live']} query={st['query']} write={st['write']} noop={st['noop']} "
              f"reject={st['reject']} broken={st['broken']} phantom={st['phantom']} "
              f"unmeasured={st['unmeasured']} problems={st['problems'] or 'none'}")
        if show_ops and (st["success"] or st["red"]):
            for rec in entries[stem]["ops"]:
                if rec["state"] in ("live", "query", "write", "phantom", "broken", "unmeasured"):
                    print(f"  OP {stem}::{rec['op']} state={rec['state']} "
                          f"shape={rec['live_shape'] or '-'} detail={rec['detail'][:200]}")
    print(f"REPORT_TOTAL measurable={len(entries)} qualify={counts['qualify']} "
          f"red={counts['red']} no_success_path={counts['no_success']}")
    return 0


def _rollback(init_path: Path, snapshot: bytes, dst: Path, src: Path) -> None:
    tmp = init_path.with_suffix(".py.seat8-tmp")
    tmp.write_bytes(snapshot)
    os.replace(tmp, init_path)
    if dst.is_file() and not src.exists():
        os.replace(dst, src)


def _append_md(stem: str, evidence: str, ruler_line: str, stamp: str, note: str) -> None:
    md = FEATURES_DIR / "QUARANTINE.md"
    assert md.is_file(), f"QUARANTINE.md absent: {md}"
    old = md.read_text(encoding="utf-8")
    block = [
        "",
        f"### Release record (batch 8 / quarantine release seat / {stamp} / `{stem}`)",
        "",
        "Basis = official gate `cadcore.ops.features.unquarantine(..., apply=True)` (its five-tier "
        "criterion `(live>0 or query>0 or write>0) and phantom==0 and broken==0`, judge bytes "
        "unchanged) + this slice's live/query/write/broken/phantom readings + the official verdict "
        "line below, verbatim. Atomicity: move + `__init__.py` manifest surgery + this record are "
        "all-or-nothing (record failure rolls the other two back).",
        "",
        "| slice | location change | readings (gate, that slice) | official verdict line (that run, verbatim) | time |",
        "|---|---|---|---|---|",
        f"| `{stem}` | `cadcore/ops/features/_quarantine/{stem}.py` → "
        f"`cadcore/ops/features/{stem}.py` | {evidence} | `{ruler_line}` | {stamp} |",
    ]
    if note:
        block += ["", f"what was fixed: {note}"]
    block.append("")
    tmp = md.with_suffix(".md.seat8-tmp")
    tmp.write_text(old.rstrip("\n") + "\n" + "\n".join(block), encoding="utf-8")
    os.replace(tmp, md)


def cmd_release(stems: list[str], apply: bool, note: str) -> int:
    entries = _entries()
    manifest = set(FEATURES._QUARANTINE)
    targets = sorted(stems) if stems else sorted(entries)
    init_path = FEATURES_DIR / "__init__.py"
    ruler_line = _verdict_line() if apply else "DRYRUN"
    stamp = _now()
    moved, denied = [], []
    for stem in targets:
        entry = entries.get(stem)
        if entry is None:
            print(f"RELEASE stem={stem} verdict=DENY problems=not_in_quarantine_dir")
            denied.append(stem)
            continue
        st = _stats(entry)
        if st["red"] > 0 or st["success"] == 0:
            print(f"RELEASE stem={stem} verdict=DENY problems="
                  f"{'red=' + str(st['red']) if st['red'] else 'no_live_query_or_write_path'} "
                  f"live={st['live']} query={st['query']} write={st['write']} "
                  f"broken={st['broken']} phantom={st['phantom']} unmeasured={st['unmeasured']} "
                  f"problems={st['problems'] or 'none'}")
            denied.append(stem)
            continue
        snapshot = init_path.read_bytes() if apply else b""
        res = FEATURES.unquarantine(stem, apply=apply)
        evidence = (f"live={res['ruler_live']} query={res['ruler_query']} write={res['ruler_write']} "
                    f"broken={res['ruler_broken']} phantom={res['ruler_phantom']} "
                    f"tiers={res['gate_success_tiers']} basis={res['gate_basis']}")
        if res["verdict"] != "ALLOW":
            print(f"RELEASE stem={stem} verdict=DENY problems={','.join(res['problems'])} "
                  f"evidence={evidence}")
            denied.append(stem)
            continue
        if not apply:
            print(f"RELEASE stem={stem} verdict=ALLOW(dryrun) evidence={evidence}")
            continue
        if not (res["moved"] and res["manifest_updated"]):
            print(f"RELEASE stem={stem} verdict=DENY problems=gate_partial "
                  f"moved={res['moved']} manifest_updated={res['manifest_updated']}")
            denied.append(stem)
            continue
        dst, src = FEATURES_DIR / f"{stem}.py", QDIR / f"{stem}.py"
        try:
            _append_md(stem, evidence, ruler_line, stamp, note)
        except BaseException as exc:  # noqa: BLE001 - 登记失败必须整体回滚
            _rollback(init_path, snapshot, dst, src)
            print(f"RELEASE stem={stem} verdict=DENY "
                  f"problems=md_register_failed:{type(exc).__name__} rolled_back=1")
            denied.append(stem)
            continue
        moved.append(stem)
        print(f"RELEASE stem={stem} verdict=ALLOW moved=1 manifest_updated=1 "
              f"registered_in=QUARANTINE.md evidence={evidence} in_manifest_before={stem in manifest}")
    print(f"RELEASE_TOTAL n={len(moved)} stems={','.join(moved)} denied={len(denied)} "
          f"mode={'apply' if apply else 'dryrun'}")
    return 0 if moved or not apply else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show-ops", action="store_true")
    ap.add_argument("--stem", nargs="*", default=[])
    ap.add_argument("--note", default=_DEFAULT_NOTE)
    args = ap.parse_args()
    if args.report:
        return cmd_report(args.stem, args.show_ops)
    if args.release:
        return cmd_release(args.stem, args.apply, args.note)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
