"""G17 补登记回归（席位 B）：5 项真实现必须经 `OPERATIONS` 可调、可验 L1 后果；`dwg_io` 按**新契约**三态记账。

契约变更（2026-09-30 DWG 只读桥落地）：`dwg_io` 由「整条能力 absent（`EXTERNAL_MISSING["dwg_io"]`）」
改为「**读**只读可用 / **写**仍外部欠账 / 后端未装 ⇒ 读侧 NA」。本席把断言**改到新契约**（不是删断言）：

* 读侧绑定 `cadcore.io.dwg_bridge`（`dwg_to_dxf` + `dwg_entities`，**只** subprocess + 文件交互，
  GPL-3.0 进程边界，不链接/不复制），注册层入口 `open_dwg` / `dwg_read` 可裸调并落 L1 后果；
* 写侧仍是外部欠账：`EXTERNAL_MISSING["dwg_io_write"]` 非空、`save_dwg` 抛 `dependency_missing`；
* 依赖（`dwg2dxf`/`dwgread`）缺席 ⇒ 读侧**如实 NA**（`reason=dependency_missing`）：
  **不抛意外异常、不落假 .dwg、不把 `data_read` 冒充成 True**。

判据（不许放宽）：
* `lineweight`/`plot_area`/`plot_scale`/`plot_flag`/`model_space_plot` 在 `OPERATIONS` 里且可调用；
* 每次裸调（探针同口径：`op(doc)`）都返回非空 uuid 列表，**且每个 uuid 能在调用后的 L1 载荷里解引用**
  （悬空 uuid = 假绿，判红）；调用前后 L1 载荷必须变化（没变化的"没抛异常"不算后果）；
* 门 G17：单个 op 的外部依赖缺席只记 `external_ops=N`（该 op **不进** `ops_ok`），**不许把整门判成 NA**；
  能力整条只由外部依赖承担且依赖缺席时仍**判红**（阴性对照见 `test_all_external_capability_is_red`）。
"""

import importlib.util
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from cadcore.ops import output as ops

#: 本席补登记的五项（真实现原本只在 `_LEGACY_OPERATIONS`，门探针调不到 ⇒ 判缺失）。
NEW_OPS = ("lineweight", "plot_area", "plot_scale", "plot_flag", "model_space_plot")


def _dump(doc):
    """L1 载荷文本：Document 对象走 `document_to_l1`，映射直接 JSON（uuid 要在文本里找得到）。"""
    if isinstance(doc, dict):
        return json.dumps(doc, sort_keys=True, default=str)
    from cadcore import docmodel
    return docmodel.document_to_l1(doc)


def _output_records(doc, bucket):
    """`doc.extra["output"][bucket]`（纯 dict 载荷同样支持，与 `ops._holder` 同口径）。"""
    extra = getattr(doc, "extra", None)
    container = extra if isinstance(extra, dict) else doc
    return container["output"][bucket]


class OutputRegisteredOpsTest(unittest.TestCase):
    """族 `output`：登记面 + 探针可达性 + 外部欠账声明。"""

    def test_new_ops_registered_in_operations(self):
        for name in NEW_OPS:
            self.assertIn(name, ops.OPERATIONS, "%s 未登记进 OPERATIONS" % name)
            self.assertTrue(callable(ops.OPERATIONS[name]), name)

    def test_bare_call_returns_resolvable_uuids_and_changes_l1(self):
        for name in NEW_OPS:
            doc, _sketch, _built = ops._fixture()
            before = _dump(doc)
            out = ops.OPERATIONS[name](doc)
            self.assertIsInstance(out, list, name)
            self.assertTrue(out, "%s 返回空 uuid 列表" % name)
            self.assertTrue(all(isinstance(item, str) and item.strip() for item in out), name)
            after = _dump(doc)
            self.assertNotEqual(before, after, "%s 调用后 L1 载荷没变" % name)
            for uid in out:
                self.assertIn(uid, after, "%s 悬空 uuid（载荷里解引用不到）：%s" % (name, uid))


class DwgReadOnlyContractTest(unittest.TestCase):
    """新契约：读侧绑定只读桥 / 写侧仍外部 / 依赖缺席 = 读侧 NA（不是假成功）。"""

    def test_read_side_bound_to_bridge_and_write_side_still_external(self):
        self.assertIn("dwg_io", ops.READ_ONLY_AVAILABLE, "读侧可用必须显式登记（读/写两态不同形）")
        declared = str(ops.READ_ONLY_AVAILABLE["dwg_io"])
        for token in ("cadcore.io.dwg_bridge", "dwg_to_dxf", "dwg_entities", "subprocess",
                      "write still external"):
            self.assertIn(token, declared, "读侧登记必须点名 %s" % token)
        # 旧契约断言是 `dwg_io in EXTERNAL_MISSING`；新契约反向：读可用 ⇒ 整条能力不许再记 absent。
        self.assertNotIn("dwg_io", ops.EXTERNAL_MISSING, "读侧已可用 ⇒ 整条能力不许再记 absent")
        self.assertTrue(str(ops.EXTERNAL_MISSING["dwg_io_write"]).strip(), "写侧欠账必须如实登记")
        self.assertIs(ops.OPERATIONS["dwg_read"], ops.OPERATIONS["open_dwg"])
        with self.assertRaises(Exception) as caught:
            ops.OPERATIONS["save_dwg"]({})
        self.assertEqual("dependency_missing", getattr(caught.exception, "reason", None),
                         "写半边必须是诚实缺席（dependency_missing），绝不落假 .dwg")

    def test_open_dwg_bare_call_registers_read_only_declaration(self):
        from cadcore.io import dwg_bridge
        doc, _sketch, _built = ops._fixture()
        before = _dump(doc)
        out = ops.OPERATIONS["dwg_read"](doc)
        self.assertIsInstance(out, list)
        self.assertTrue(out)
        after = _dump(doc)
        self.assertNotEqual(before, after, "裸调必须落 L1 后果（不是没抛异常就绿）")
        for uid in out:
            self.assertIn(uid, after)
        rec = _output_records(doc, "imports")[-1]
        self.assertEqual(out[0], rec["uuid"])
        self.assertEqual("dwg read", rec["kind"])
        self.assertEqual("read_only", rec["dwg"]["read"]["mode"])
        self.assertEqual("subprocess", rec["dwg"]["read"]["linkage"])
        self.assertIn("GPL-3.0", rec["dwg"]["read"]["linkage_license"])
        self.assertIs(False, rec["dwg"]["read"]["write_backend"])
        self.assertIs(False, rec["write_backend"])
        self.assertIs(False, rec["dwg"]["read"]["data_read"], "裸调没读文件 ⇒ data_read 必须 False，不许冒充")
        self.assertEqual(bool(dwg_bridge.availability()["read_ready"]), rec["backend_present"],
                         "backend_present 必须现读 availability()，不是抄死的常量")
        if rec["backend_present"]:
            self.assertEqual("", rec["na_reason"])
        else:
            self.assertEqual("external_missing:dwg2dxf,dwgread", rec["na_reason"])

    def test_dependency_absent_read_side_is_na_not_fake_success(self):
        """把 `dwg2dxf`/`dwgread` 伪装缺席一次（monkeypatch `shutil.which`）⇒ 读侧 NA，绝不假成功。"""
        from cadcore.io import dwg_bridge
        real_which = shutil.which
        doc, _sketch, _built = ops._fixture()

        def hidden(name):
            return None if name in ("dwg2dxf", "dwgread") else real_which(name)

        with tempfile.TemporaryDirectory(prefix="dwg-contract-") as tmp:
            fake = Path(tmp) / "synthetic.dwg"
            fake.write_bytes(b"AC1032" + b"\0" * 64)          # 真 DWG 头签名（AC1032 = R2018）
            with mock.patch("shutil.which", side_effect=hidden):
                avail = dwg_bridge.availability()
                self.assertIs(False, avail["read_ready"], "依赖伪装缺席必须被 availability() 现读看见")
                self.assertIs(False, avail["write_ready"])
                with self.assertRaises(Exception) as caught:
                    ops.OPERATIONS["open_dwg"](doc, path=str(fake))
                self.assertEqual("dependency_missing", getattr(caught.exception, "reason", None),
                                 "后端缺席 ⇒ 读侧 NA（dependency_missing），不是意外异常、更不是假成功")
                out = ops.OPERATIONS["dwg_read"](doc)          # 裸调：只登记能力声明
                self.assertTrue(out)
                self.assertEqual([fake.name], sorted(p.name for p in Path(tmp).iterdir()),
                                 "依赖缺席时不许落任何产物（含假 .dwg）")
        rec = _output_records(doc, "imports")[-1]
        self.assertIs(False, rec["dwg"]["read"]["data_read"])
        self.assertIs(False, rec["dwg"]["read"]["entities_parsed"])
        self.assertIs(False, rec["backend_present"])
        self.assertEqual("external_missing:dwg2dxf,dwgread", rec["na_reason"])


def _gate_module():
    """按路径加载门 G17（不 import 成包；缺件直接判红，不静默跳过）。"""
    path = Path(__file__).resolve().parents[3] / "tools" / "cad" / "parity_output_gate.py"
    if not path.is_file():
        raise AssertionError("门 G17 不在位（判据不许静默放宽）：%s" % path)
    name = "cad_parity_output_gate_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _features(gate):
    features, ferr = gate._load_features(gate._oss_root())
    assert features is not None and ferr == "", "features 必须读到：%s" % ferr
    return features


def _hidden_which():
    real_which = shutil.which

    def which(name):
        return None if name in ("dwg2dxf", "dwgread") else real_which(name)

    return mock.patch("shutil.which", side_effect=which)


class GateExternalTriStateTest(unittest.TestCase):
    """门 G17：单 op 外部依赖缺席 ⇒ 记 external、不当整门 NA；整条能力外部缺席 ⇒ 仍判红。"""

    def test_external_op_is_recorded_and_gate_is_not_na(self):
        gate = _gate_module()
        features = _features(gate)
        fam = gate._load_family_modules(gate._oss_root(), injected=None)
        self.assertTrue(fam.modules, "族模块必须加载到：%s" % fam.problems)
        # `@tmp:` 占位符要落到进程内临时目录（与 CLI `main()` 同口径；不设 ⇒ 导出类深验必然假红）。
        with tempfile.TemporaryDirectory(prefix="cad-parity-gate-") as tmp:
            ctx = gate.build_ctx()
            ctx.tmp = Path(tmp)
            with _hidden_which():                               # 依赖伪装缺席一次
                rep = gate.evaluate(fam.modules, features, ctx=ctx)
        self.assertGreaterEqual(rep.external_ops, 1, "依赖缺席的 op 必须记 external")
        self.assertEqual(gate.ST_EXTERNAL, rep.op_state["save_dwg"])
        self.assertEqual(rep.ops_ok, sum(1 for v in rep.op_state.values() if v == gate.ST_OK),
                         "external op 绝不进 ops_ok")
        self.assertLess(rep.ops_ok, rep.ops_total)
        self.assertEqual("PASS", rep.verdict,
                         "单 op 外部依赖缺席不许把整门判 NA/红：reasons=%s detail=%s"
                         % (rep.reasons, rep.detail[:4]))
        self.assertEqual(0, rep.rc)
        self.assertIn("external_ops=%d" % rep.external_ops, rep.line())
        self.assertNotIn("verdict=NA", rep.line())

    def test_all_external_capability_is_red(self):
        """阴性对照：能力整条只由外部依赖承担 ⇒ 必须红（rc=1），不许被 external 记账洗绿/洗成 NA。"""
        from cadcore.errors import CadError
        gate = _gate_module()
        features = _features(gate)

        def external(_target, **_kwargs):
            raise CadError("external dependency missing", reason="dependency_missing")

        fake = types.ModuleType("cad_ops_all_external_fixture")
        fake.FAMILY = gate.FAMILY_ID
        fake.OPERATIONS = {"save_dwg": external}
        rep = gate.evaluate(fake, features, ctx=gate.build_ctx())
        self.assertEqual(gate.ST_EXTERNAL, rep.op_state["save_dwg"])
        self.assertGreaterEqual(rep.external_must_have, 1, "整条能力外部缺席必须记 external 欠账")
        self.assertNotEqual("PASS", rep.verdict, "全外部缺席绝不当绿")
        self.assertEqual(1, rep.rc, "能力没验到 ⇒ 红（rc=1），不是门腿断的 NA（rc=2）")


if __name__ == "__main__":
    unittest.main()
