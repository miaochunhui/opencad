# SPDX-License-Identifier: Apache-2.0
"""test_edit_reach_contract -- autocad-edit 八条编辑命令的「三层接线」静态契约（快尺子）。

为什么要有这个文件
------------------
端到端尺子是 `oss-alternatives/tools/cad/parity_edit_reach_gate.py`（真起 server + 真 app.js 解析器 + undo 字节比对），
但它依赖 node 与真起服务，跑一次秒级到十几秒；本文件是同一批事实的**静态快尺子**（毫秒级），
把三层接线的任一层被改坏当场报红，供日常回归用：

  1. 库级：`packages/cadcore/cadcore/edit/modify.py` 里八个 op 的**签名关键字**与 web 命令行送进来的参数名一致；
  2. API 级：`apps/opencad/server.py` 的 `_PASSTHROUGH` 把八个 verb 映射到 `edit.modify`；
  3. UI 级：`apps/opencad/web/js/app.js` 的 `WIRE_VERBS`/`WIRE_TOOLS` 含八个 verb/tool，`index.html` 有对应 `data-tool` 按钮；
  4. 独立仓行为：不依赖 infra 历史状态账，真 Workspace 执行八个编辑动词。

四层里任何一层被删（白名单去掉 move、词表去掉 trim、改名 kwargs、操作不改几何）本文件立刻红。
反向自证（阴性对照）：`_strip` 变异体证明这些断言看得见"被删"。
"""

from __future__ import annotations

import inspect
import re
import unittest
import tempfile
from pathlib import Path

_THIS = Path(__file__).resolve()
OSS = _THIS.parents[3]            # tests -> cadcore -> packages -> oss-alternatives
REPO = OSS.parent
MODIFY_PY = OSS / "packages" / "cadcore" / "cadcore" / "edit" / "modify.py"
SERVER_PY = OSS / "apps" / "opencad" / "server.py"
APP_JS = OSS / "apps" / "opencad" / "web" / "js" / "app.js"
INDEX_HTML = OSS / "apps" / "opencad" / "web" / "index.html"
PKG_DIR = OSS / "packages" / "cadcore"

#: 条目名 -> (verb, tool, 必需 kwargs)。kwargs 名 = web 命令行/点击流真正送进来的键。
REACH = {
    "移动": ("move", "move", {"refs", "dx_nm", "dy_nm"}),
    "复制": ("copy", "copy", {"refs", "dx_nm", "dy_nm"}),
    "缩放": ("scale", "scale", {"refs", "center_nm", "factor"}),
    "镜像": ("mirror", "mirror", {"refs", "p1_nm", "p2_nm"}),
    "偏移": ("offset", "offset", {"ref", "distance_nm"}),
    "修剪": ("trim", "trim", {"target_ref", "cutter_refs"}),
    "延伸": ("extend", "extend", {"target_ref", "boundary_refs"}),
    "阵列": ("array_linear", "array_linear", {"refs", "rows", "cols", "dx_nm", "dy_nm"}),
}


def _strip(text: str, marker: str, verb: str) -> str:
    """阴性对照用：从 JS 数组字面量里删掉一个元素（找不到就原样返回）。"""
    match = re.search(re.escape(marker) + r"\s*=\s*\[(.*?)\]", text, re.S)
    if not match:
        return text
    body = re.sub(r'"%s"\s*,?\s*' % re.escape(verb), "", match.group(1))
    return text[:match.start(1)] + body + text[match.end(1):]


def _js_array(text: str, marker: str) -> list:
    match = re.search(re.escape(marker) + r"\s*=\s*\[(.*?)\];", text, re.S)
    return re.findall(r'"([^"]+)"', match.group(1)) if match else []


class EditReachContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for path in (MODIFY_PY, SERVER_PY, APP_JS, INDEX_HTML):
            assert path.is_file(), "夹具自证：真件缺席 %s" % path
        if str(PKG_DIR) not in __import__("sys").path:
            __import__("sys").path.insert(0, str(PKG_DIR))
        from cadcore.edit import modify as modify_mod
        cls.modify = modify_mod
        cls.server_text = SERVER_PY.read_text(encoding="utf-8")
        cls.app_text = APP_JS.read_text(encoding="utf-8")
        cls.index_text = INDEX_HTML.read_text(encoding="utf-8")

    # ── 0. 夹具自证 ──────────────────────────────────────────────────────────────────
    def test_fixture_is_real(self) -> None:
        """夹具自证：测的是真件（八个 op 的 def 行在源文件里逐条命中），且清单非空。"""
        self.assertGreater(len(REACH), 0, "夹具自证：被测清单非空")
        source = MODIFY_PY.read_text(encoding="utf-8")
        for verb in sorted({v for v, _t, _k in REACH.values()}):
            self.assertRegex(source, r"(?m)^def %s\(" % re.escape(verb),
                             "夹具自证：modify.py 里没有 def %s(" % verb)
        self.assertIn("_PASSTHROUGH", self.server_text)
        self.assertIn("WIRE_VERBS", self.app_text)
        self.assertEqual(len(REACH), 8, "independent edit contract must contain eight commands")

    # ── 1. 库级签名 ──────────────────────────────────────────────────────────────────
    def test_library_signatures(self) -> None:
        for name, (verb, _tool, kwargs) in REACH.items():
            self.assertTrue(callable(getattr(self.modify, verb, None)), "%s: modify.%s 缺席" % (name, verb))
            params = set(inspect.signature(getattr(self.modify, verb)).parameters)
            missing = kwargs - params
            self.assertFalse(missing, "%s: modify.%s 签名缺 %s" % (name, verb, sorted(missing)))

    # ── 2. server 白名单 ────────────────────────────────────────────────────────────
    def test_server_passthrough(self) -> None:
        for name, (verb, _tool, _kwargs) in REACH.items():
            pattern = r'"%s"\s*:\s*"edit\.modify"' % re.escape(verb)
            self.assertRegex(self.server_text, pattern, "%s: _PASSTHROUGH 缺 %s" % (name, verb))

    # ── 3. web 词表 + 按钮 ──────────────────────────────────────────────────────────
    def test_web_wiring(self) -> None:
        verbs = _js_array(self.app_text, "const WIRE_VERBS")
        tools = (_js_array(self.app_text, "const WIRE_DRAW")
                 + _js_array(self.app_text, "const WIRE_XFORM"))
        tail = re.search(r"const WIRE_TOOLS\s*=\s*WIRE_DRAW\.concat\(WIRE_XFORM,\s*\[(.*?)\]\)",
                         self.app_text, re.S)
        tools += re.findall(r'"([^"]+)"', tail.group(1)) if tail else []
        self.assertTrue(verbs and tools, "词表抽不出来（源码漂移）")
        for name, (verb, tool, _kwargs) in REACH.items():
            self.assertIn(verb, verbs, "%s: WIRE_VERBS 缺 %s" % (name, verb))
            self.assertIn(tool, tools, "%s: WIRE_TOOLS 缺 %s" % (name, tool))
            self.assertIn('data-tool="%s"' % tool, self.index_text, "%s: index.html 无按钮" % name)

    # Independent behavior replaces historical status evidence.
    def test_commands_have_real_effect_not_historical_status(self) -> None:
        import sys
        sys.path.insert(0, str(SERVER_PY.parent))
        import server
        self.assertEqual(Path(server.__file__).resolve(), SERVER_PY)
        with tempfile.TemporaryDirectory(prefix="edit-contract-") as directory:
            def workspace():
                return server.Workspace(server._load_cadcore(), locale="zh-CN",
                                        standard="gbt", out_root=directory)

            specs = (
                ("move", {"dx_nm": 100, "dy_nm": 200}),
                ("copy", {"dx_nm": 100, "dy_nm": 200}),
                ("scale", {"center_nm": [0, 0], "factor": 2}),
                ("mirror", {"p1_nm": [0, 0], "p2_nm": [0, 1000]}),
                ("array_linear", {"rows": 1, "cols": 2, "dx_nm": 5000, "dy_nm": 0}),
            )
            for verb, args in specs:
                ws = workspace()
                entity = ws.command("add_line", {"p1": [1000, 1000], "p2": [3000, 1000]})["uuid"]
                before = ws.l1_text()
                result = ws.command(verb, dict(args, refs=[entity]))
                self.assertTrue(result["ok"], result)
                self.assertNotEqual(ws.l1_text(), before, verb)
                self.assertTrue(result["uuids"], verb)
            ws = workspace()
            circle = ws.command("add_circle", {"center": [0, 0], "radius_nm": 1000})["uuid"]
            before = ws.l1_text()
            self.assertTrue(ws.command("offset", {"ref": circle, "distance_nm": 100})["ok"])
            self.assertNotEqual(ws.l1_text(), before)
            for verb, end, keyword in (("trim", [10000, 0], "cutter_refs"),
                                       ("extend", [3000, 0], "boundary_refs")):
                ws = workspace()
                target = ws.command("add_line", {"p1": [0, 0], "p2": end})["uuid"]
                boundary = ws.command("add_line", {"p1": [5000, -1000], "p2": [5000, 1000]})["uuid"]
                before = ws.l1_text()
                self.assertTrue(ws.command(verb, {"target_ref": target, keyword: [boundary]})["ok"])
                self.assertNotEqual(ws.l1_text(), before, verb)

    # ── 5. 阴性对照：尺子看得见"被删" ────────────────────────────────────────────────
    def test_negative_control(self) -> None:
        stripped = _strip(self.app_text, "const WIRE_VERBS", "trim")
        self.assertNotEqual(stripped, self.app_text, "变异体没改动源码 ⇒ 尺子自证失败")
        self.assertNotIn("trim", _js_array(stripped, "const WIRE_VERBS"), "变异体没被看见")


if __name__ == "__main__":
    unittest.main(verbosity=2)
