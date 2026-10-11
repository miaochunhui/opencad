"""C7 残留回归：cadcore 的测试/门探针不许把生成件写回源树（包根）。

根因（2026-10-01 复核 commit 2f907e313 的残留两件）：
* 门 G17 parity_output_gate.py 在 ctx.tmp is None 时把 @tmp: 占位符现解析成 ""（_resolve_value）；
  注册层 plot(out="") / publish(out_dir="") 又把空路径解释成进程 CWD ⇒
  publish 在包根落下 page_001.svg、plot 在包根落下空干 .svg；
* 两件都是测试运行产物（全仓零引用），不是资产。

判据（本文件 = 尺子，自带阴性对照）：
1. 空路径必须被拒绝（bad_reference）：plot / preview / publish 与 choke point
   draw.sheet.export_sheet。把 "" 当 CWD 就是残留的成因；
2. 走历史污染路径（门 G17 在 ctx.tmp=None 下全量 evaluate 真实 output 族）后，包根与 CWD
   都不得新增 page_*.svg / 空干 .svg；
3. 尺子看得见红：故意把两件写进临时根、把 page_999.svg 写回真包根必须被 _residue() 抓到，
   清理后必须回到原状（E2.5 夹具自证）。
"""

import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

from cadcore.draw import sheet as _sheet
from cadcore.errors import CadError
from cadcore.ops import output as ops

#: 包根 = oss-alternatives/packages/cadcore（残留落点，也是验收命令的 CWD）。
_PKG_ROOT = Path(__file__).resolve().parents[1]


def _residue(root):
    """root 顶层的污染件名：page_*.svg + 空干 .svg（残留的两种真实形态，缺一即假绿）。"""
    root = Path(root)
    names = [p.name for p in root.glob("page_*.svg")]
    if (root / ".svg").exists():
        names.append(".svg")
    return sorted(names)


def _residue_fingerprint(root):
    """污染件名 -> sha256：同名件被重写也要看得见（否则盘上已存在的残留会被洗绿）。"""
    root = Path(root)
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in _residue(root)}


def _changed(before, after):
    """前后指纹差：新增或内容变了的名字（重写同名残留也算）。"""
    return sorted({name for name in set(before) | set(after) if before.get(name) != after.get(name)})


def _gate_module():
    """按路径加载门 G17（与 test_output_registered_ops.py 同一入口；缺件判红，不静默跳过）。"""
    path = Path(__file__).resolve().parents[3] / "tools" / "cad" / "parity_output_gate.py"
    if not path.is_file():
        raise AssertionError("门 G17 不在位（判据不许静默放宽）：%s" % path)
    name = "cad_parity_output_gate_c7_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ResidueRulerTest(unittest.TestCase):
    """尺子自证：看得见红（阴性对照）、清得干净。"""

    def test_ruler_sees_planted_residue_in_clean_temp_root(self):
        with tempfile.TemporaryDirectory(prefix="c7-ruler-") as tmp:
            root = Path(tmp)
            self.assertEqual([], _residue(root), "干净目录必须先报空（阳性对照）")
            (root / "page_001.svg").write_text("<svg/>", encoding="utf-8")
            (root / ".svg").write_text("<svg/>", encoding="utf-8")
            self.assertEqual([".svg", "page_001.svg"], _residue(root),
                             "尺子必须同时看得见 page_*.svg 与空干 .svg，否则是假绿")

    def test_ruler_sees_planted_residue_in_real_package_root(self):
        planted = _PKG_ROOT / "page_999.svg"
        self.assertFalse(planted.exists(), "阴性对照占位名不该已存在：%s" % planted)
        before = _residue(_PKG_ROOT)
        try:
            planted.write_text("<svg/>", encoding="utf-8")
            after = _residue(_PKG_ROOT)
        finally:
            planted.unlink(missing_ok=True)
        self.assertEqual({"page_999.svg"}, set(after) - set(before),
                         "故意写回源树必须被尺子抓到（阴性对照）")
        self.assertEqual(before, _residue(_PKG_ROOT), "清理后必须回到原状")


class EmptyPathIsRefusedTest(unittest.TestCase):
    """空路径 = bad_reference，不许被解释成进程 CWD（残留的直接成因）。"""

    def setUp(self):
        self.doc, _sketch, _built = ops._fixture()
        self.sheet_uuid = ops.create_layout(self.doc, name="C7Probe")[0]

    def test_plot_preview_publish_refuse_blank_paths(self):
        for blank in ("", "   "):
            with self.assertRaises(CadError) as caught:
                ops.OPERATIONS["plot"](self.doc, sheet_uuid=self.sheet_uuid, fmt="svg", out=blank)
            self.assertEqual("bad_reference", getattr(caught.exception, "reason", None),
                             "plot out=%r 必须 bad_reference（不是写出 .svg）" % blank)
            with self.assertRaises(CadError) as caught:
                ops.OPERATIONS["preview"](self.doc, sheet_uuid=self.sheet_uuid, out=blank)
            self.assertEqual("bad_reference", getattr(caught.exception, "reason", None),
                             "preview out=%r 必须 bad_reference" % blank)
            with self.assertRaises(CadError) as caught:
                ops.OPERATIONS["publish"](self.doc, sheet_uuids=[self.sheet_uuid], fmt="svg",
                                          out_dir=blank)
            self.assertEqual("bad_reference", getattr(caught.exception, "reason", None),
                             "publish out_dir=%r 必须 bad_reference（不是 page_001.svg 落 CWD）" % blank)

    def test_export_sheet_choke_point_refuses_blank_out(self):
        sheet = ops._sheet_by_uuid(self.sheet_uuid)
        for blank in ("", "   "):
            with self.assertRaises(CadError) as caught:
                _sheet.export_sheet(sheet, fmt="svg", out=blank)
            self.assertEqual("bad_reference", getattr(caught.exception, "reason", None),
                             "export_sheet out=%r 必须 bad_reference（不许 _with_extension('') 变 .svg）"
                             % blank)


class GateWithoutTmpDoesNotPolluteSourceTreeTest(unittest.TestCase):
    """历史污染路径复现：G17 门在 ctx.tmp=None 下全量 evaluate 真实 output 族。"""

    def test_gate_evaluate_with_tmp_none_keeps_package_root_clean(self):
        gate = _gate_module()
        features, ferr = gate._load_features(gate._oss_root())
        self.assertIsNotNone(features, "features 必须读到：%s" % ferr)
        self.assertEqual("", ferr, "features 读取必须无错")
        fam = gate._load_family_modules(gate._oss_root(), injected=None)
        self.assertTrue(fam.modules, "族模块必须加载到：%s" % fam.problems)
        ctx = gate.build_ctx()
        ctx.tmp = None  # 明示历史条件：@tmp: -> ""；不依赖 build_ctx 的缺省
        cwd_before = Path.cwd()
        pkg_before, cwd_fp_before = _residue_fingerprint(_PKG_ROOT), _residue_fingerprint(cwd_before)
        os.chdir(_PKG_ROOT)  # 与验收命令同 CWD（残留正是从这里落进包根的）
        try:
            gate.evaluate(fam.modules, features, ctx=ctx)
        finally:
            os.chdir(cwd_before)
        pkg_after, cwd_fp_after = _residue_fingerprint(_PKG_ROOT), _residue_fingerprint(cwd_before)
        self.assertEqual(pkg_before, pkg_after,
                         "门在 ctx.tmp=None 下往包根写了新件或重写了残留：%r"
                         % _changed(pkg_before, pkg_after))
        self.assertEqual(cwd_fp_before, cwd_fp_after,
                         "门在 ctx.tmp=None 下往 CWD 写了新件或重写了残留：%r"
                         % _changed(cwd_fp_before, cwd_fp_after))


if __name__ == "__main__":
    unittest.main()
