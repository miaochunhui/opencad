"""tests.test_fixtures —— A20 夹具自证：`tests/data/plate.json`（G2 的 L1 模型）与 `tests/corpus/dxf/` 四份 DXF 语料。

设计一句话
----------
**夹具最容易骗人的地方不是"坏"，而是"没人真读过它"和"声明与事实各说各话"** —— 所以本文件只做两件事：
①**真读**：每份语料都真的过一遍 `packages/dxf` 的 reader / writer，并逐字节对照读→写结果；
②**对账**：`corpus/dxf/README.md` 里的机读清单声明了几个实体、几种子实体、几个未知实体、几个带 XDATA 的实体、
   几个图层、各类型计数，全部由 reader **现算**后逐项相等，且「清单里的文件」与「盘上的文件」**双向差集为空**。

为什么这样设计（背景）
----------------------
* `plate.json` 是 G2 `drawing_smoke.py --model …` 的输入。坐标写错一位，出图**不会报错**，只会画出一张
  "看着挺像"的图 —— 所以本文件不看"能不能解析 JSON"，而是**按几何现算每个尺寸**，与夹具声明的 `value_nm`
  逐条对账（夹具自证：它说的尺寸就是它自己的几何量出来的尺寸）。G2 还要求"改模型尺寸 ⇒ 出图尺寸跟着变"，
  那条判据成立的前提正是"夹具的尺寸与几何一致"。
* **`geom` 字段名与子形状白名单不是本席自定的**：以落盘的 `cadcore/sketch/constraints.py`（A9/A10）为准 ——
  长度 `x1/y1/x2/y2`、`cx/cy/r`，角度 `start_deg/end_deg` 归一到 `[0,360)`，
  尺寸引用白名单（line: `edge`/`start`/`end`，circle 与 arc: `radius`/`center`）同源。
  `test_13`/`test_14` 每次运行都拿那一层现算校验一遍（`validate_dimension` / `validate_constraint` /
  `closed_profiles`）—— 夹具与命令层口径脱节时，先红在这里。
* DXF 语料是 G3 `dxf_fidelity_gate.py --corpus …` 的输入，四份分别覆盖：常规实体 / 未知实体与代理对象保留 /
  XDATA 保留 / CJK 文字（TrueType 样式）。README 写 9 个实体、实际 8 个 ⇒ G3 的判定行会永远绿得没有意义，
  所以计数一律"现算 vs 声明"比对，不抄死数。
* 口径：`0`=真为零、`-1`=未采、`NA`=不适用。依赖缺席（如语言包或 `cadcore.docmodel` 未落盘）一律
  `skipTest("NA: …")` 并把原因写进判定行 —— **采不到不许静默当绿**。

夹具自证与副作用
----------------
每个夹具类在 `setUpClass` 里记下被读文件的 sha256，最后一个测试（按名字排序）复核它们**一个字节都没被改过**；
所有写盘都写进 `tempfile` 临时目录 ⇒ 本文件只读仓内夹具，不碰 `packages/dxf/**`、不碰别人的文件。

说明（数据 ≠ 文案）
------------------
`cjk_text.dxf` 里的中文是**夹具数据**：它测的就是"DXF 里的非 ASCII 文本能不能原样读回来"。
它在 `.py` 里只以 **sha256 指纹**出现（`CJK_*_SHA256`），正文真源在 `cjk_text.dxf`、
人读版在 `corpus/dxf/README.md` —— 因为 i18n 门的源码尺子扫的正是"字面量的值里有没有中文"。
同理：本文件的**断言消息与 skip 原因一律英文**（中文只出现在 docstring / 注释里），
与兄弟测试文件 `test_i18n_catalog.py` 的源码纪律一致（该文件的 `SourceDisciplineTests` 会扫 `.py`
的字面量、放过 docstring）；本文件末尾的 `SourceDisciplineTest` 用同一把尺子**自查**，并带变异体反证。

用法
----
    cd oss-alternatives/packages/cadcore
    python3 -m unittest tests.test_fixtures -v          # 跑本文件
    python3 tests/test_fixtures.py --emit-manifest      # 改了语料后重新打印清单行（数字由 reader 现算）
    python3 tests/test_fixtures.py --emit-cjk-digests   # 改了 CJK 语料后重新打印指纹，贴回常量
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_PKG_ROOT = _HERE.parents[1]                              # …/packages/cadcore
_OSS_ROOT = _HERE.parents[3]                              # …/oss-alternatives
_DXF_PKG_PARENT = _OSS_ROOT / "packages" / "dxf"

for _path in (str(_PKG_ROOT), str(_DXF_PKG_PARENT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_DXF_IMPORT_ERROR = ""
try:                                                       # 缺席不硬失败：下面按测试报红并说清原因
    from dxf.model import KNOWN_ENTITY_TYPES               # noqa: E402
    from dxf.reader import parse_bytes as dxf_parse_bytes  # noqa: E402
    from dxf.reader import read as dxf_read                 # noqa: E402
    from dxf.writer import dump_bytes as dxf_dump_bytes     # noqa: E402
    from dxf.writer import verify_byte_identity as dxf_verify_byte_identity  # noqa: E402
    from dxf.writer import write as dxf_write               # noqa: E402
except Exception as _exc:                                   # pragma: no cover - dxf 包在仓内，正常都在
    _DXF_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)

_DATA_DIR = _PKG_ROOT / "tests" / "data"
_CORPUS_DIR = _PKG_ROOT / "tests" / "corpus" / "dxf"
_PLATE_FILE = _DATA_DIR / "plate.json"
_CORPUS_README = _CORPUS_DIR / "README.md"
_CATALOG_DIR = _PKG_ROOT / "cadcore" / "data" / "i18n"
_STANDARDS_DIR = _PKG_ROOT / "cadcore" / "data" / "standards"

#: 语料清单的机读区间（README 里只有这一块是"声明"，其余是说明文字）
_MANIFEST_BEGIN = "<!-- CORPUS-MANIFEST:BEGIN -->"
_MANIFEST_END = "<!-- CORPUS-MANIFEST:END -->"
_MANIFEST_COLUMNS = ("file", "entities", "children", "unknown", "xdata", "layers", "types", "coverage")

#: `cjk_text.dxf` 的期望文本与中文图层名 —— 记的是**sha256 指纹**而不是正文本身。
#: 为什么：i18n 门的源码尺子扫的是"**字面量的值**里有没有中文"（`SourceDisciplineTests` 同款尺子），
#: 夹具数据也不例外；正文真源在 `cjk_text.dxf`、人读版在 `corpus/dxf/README.md` 的
#: "CJK 语料的期望文本"一节。改了语料 ⇒ `python3 tests/test_fixtures.py --emit-cjk-digests`
#: 把两组指纹重新打印出来，贴回下面两个常量（指纹不匹配 = 语料被改过，测试必红）。
CJK_TEXT_SHA256 = (
    "e405b12cc9bdbd0b0226a22a08adc9ba1c1c6840ed6edb0ccc00e15d88b840b5",   # TEXT #1: 4 Chinese characters
    "d2de7450871a0064b770c8b241eafdbb3ae38b0c7472043314672983db6c680d",   # TEXT #2: 6 Chinese characters + " R10"
)
CJK_LAYER_SHA256 = (
    "574301cad8505ea83fe64f40dd10915ee9e1b8be4255f58ce98374e0205005e1",   # layer A: 2 Chinese characters
    "9875b1f82509fec89e9cc2be78c9d92f6e91db02456f08a59a914c80b77eb5b4",   # layer B: 2 Chinese characters
)

#: 允许的字体文件后缀（TrueType / OpenType；**SHX 专有字体一律不许出现**）
_FONT_SUFFIXES = (".ttf", ".otf", ".ttc")

#: 每个语料文件的第一条 999 注释里必须出现的 clean-room 自证词
_PROVENANCE_MARKERS = ("clean-room", "hand-written")

_FIXTURE_FILES = (
    _PLATE_FILE,
    _CORPUS_DIR / "simple_entities.dxf",
    _CORPUS_DIR / "with_unknown_entity.dxf",
    _CORPUS_DIR / "with_xdata.dxf",
    _CORPUS_DIR / "cjk_text.dxf",
)


# --------------------------------------------------------------------------------------
# 通用小工具
# --------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(text: str) -> str:
    """文本内容的 sha256（用来在**不写中文字面量**的前提下锁住夹具正文）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_dxf() -> None:
    """`packages/dxf` 不在场时**响亮报错**（语料没有第二条读法，不许降级成"没测也算过"）。"""
    if _DXF_IMPORT_ERROR:
        raise AssertionError(
            "cannot read packages/dxf (%s) -- corpus accounting is impossible; "
            "this test never degrades into a pass" % _DXF_IMPORT_ERROR)


def _load_dxf(path: Path):
    _require_dxf()
    return dxf_read(str(path))


def _actual_counts(path: Path) -> dict:
    """用 reader **现算**清单里的每一项（数字不来自任何声明）。"""
    document = _load_dxf(path)
    top = document.entities()
    with_children = document.entities(include_children=True)
    types: dict = {}
    for entity in top:
        key = entity.type.upper()
        types[key] = types.get(key, 0) + 1
    return {
        "file": path.name,
        "entities": len(top),
        "children": len(with_children) - len(top),
        "unknown": len(document.unknown_entities()),
        "xdata": len(document.xdata_entities()),
        "layers": len(document.layer_names()),
        "types": "+".join("%s=%d" % (name, types[name]) for name in sorted(types)),
        "document": document,
    }


def _read_manifest(*, required: bool = True) -> dict:
    """读 `README.md` 里的机读清单 → `{文件名: 行}`；格式不对**响亮报错**（不静默跳过）。"""
    if not _CORPUS_README.is_file():
        if required:
            raise AssertionError("corpus manifest is missing: %s" % _CORPUS_README)
        return {}
    text = _CORPUS_README.read_text(encoding="utf-8")
    if text.count(_MANIFEST_BEGIN) != 1 or text.count(_MANIFEST_END) != 1:
        raise AssertionError(
            "README must contain %s / %s exactly once (found %d / %d)"
            % (_MANIFEST_BEGIN, _MANIFEST_END, text.count(_MANIFEST_BEGIN), text.count(_MANIFEST_END)))
    block = text.split(_MANIFEST_BEGIN, 1)[1].split(_MANIFEST_END, 1)[0]
    rows: dict = {}
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(_MANIFEST_COLUMNS):
            raise AssertionError("manifest row must have %d cells, got %d: %r"
                                 % (len(_MANIFEST_COLUMNS), len(cells), line))
        if cells[0] == "file" or set(cells[0]) <= set("-: "):
            continue                                     # 表头与分隔行
        row: dict = {}
        for name, raw in zip(_MANIFEST_COLUMNS, cells):
            if name in ("file", "types", "coverage"):
                row[name] = raw
                continue
            try:
                row[name] = int(raw)
            except ValueError:
                raise AssertionError("manifest cell %s of %r is not an integer: %r"
                                     % (name, cells[0], raw)) from None
        if row["file"] in rows:
            raise AssertionError("manifest lists %r twice" % row["file"])
        rows[row["file"]] = row
    if required and not rows:
        raise AssertionError("manifest has no data rows: %s" % _CORPUS_README)
    return rows


def _manifest_line(actual: dict, coverage: str) -> str:
    return "| %s | %d | %d | %d | %d | %d | %s | %s |" % (
        actual["file"], actual["entities"], actual["children"], actual["unknown"],
        actual["xdata"], actual["layers"], actual["types"], coverage)


# --------------------------------------------------------------------------------------
# DXF 语料
# --------------------------------------------------------------------------------------


class DxfCorpusTest(unittest.TestCase):
    """四份 DXF 语料：真读一遍 + 与 README 机读清单逐项对账 + 逐字节往返。"""

    @classmethod
    def setUpClass(cls) -> None:
        _require_dxf()
        if not _CORPUS_DIR.is_dir():
            raise AssertionError("corpus directory is missing: %s" % _CORPUS_DIR)
        cls.manifest = _read_manifest()
        missing = [str(path) for path in _FIXTURE_FILES if not path.is_file()]
        if missing:
            raise AssertionError("fixture files are missing: %s" % ", ".join(missing))
        cls.before = {path.name: _sha256(path) for path in _FIXTURE_FILES}

    # —— 夹具自证：真件在场、且没被本套件改过 ——

    def test_01_fixtures_are_real_and_nonempty(self) -> None:
        """自证：读的是仓内真件（非空、在 corpus 目录里、有 clean-room 出处行）。"""
        for path in _FIXTURE_FILES:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), "%s is not on disk" % path)
                self.assertGreater(path.stat().st_size, 0, "%s is empty" % path)
        for path in _CORPUS_DIR.glob("*.dxf"):
            with self.subTest(path=path.name):
                self.assertIn("corpus/dxf", path.as_posix())
                head = path.read_text(encoding="utf-8").splitlines()[:3]
                self.assertEqual(head[0].strip(), "999", "first tag must be the 999 provenance comment")
                lowered = head[1].lower()
                for marker in _PROVENANCE_MARKERS:
                    self.assertIn(marker, lowered, "%s: provenance line lacks %r" % (path.name, marker))

    def test_zz_no_side_effects_on_repo_fixtures(self) -> None:
        """自证：跑完本套件后，夹具文件的 sha256 一个都没变（写盘只发生在临时目录）。"""
        for name, digest in self.before.items():
            with self.subTest(file=name):
                current = _sha256(next(path for path in _FIXTURE_FILES if path.name == name))
                self.assertEqual(current, digest, "%s was modified by this test module" % name)

    # —— 声明 vs 现算 ——

    def test_02_manifest_and_disk_agree_both_ways(self) -> None:
        """清单里的文件 vs 盘上的 `*.dxf`：**双向差集都必须为空**。"""
        on_disk = {path.name for path in _CORPUS_DIR.glob("*.dxf")}
        declared = set(self.manifest)
        self.assertEqual(declared - on_disk, set(), "declared in manifest but absent on disk")
        self.assertEqual(on_disk - declared, set(), "present on disk but not declared in manifest")

    def test_03_declared_counts_match_reader(self) -> None:
        """README 声明的计数必须与 reader 现算的一致（实体/子实体/未知/XDATA/图层/类型计数）。"""
        for name, row in sorted(self.manifest.items()):
            path = _CORPUS_DIR / name
            with self.subTest(file=name):
                actual = _actual_counts(path)
                for field in ("entities", "children", "unknown", "xdata", "layers"):
                    self.assertEqual(actual[field], row[field],
                                     "%s %s: manifest=%r measured=%r"
                                     % (name, field, row[field], actual[field]))
                self.assertEqual(actual["types"], row["types"],
                                 "%s types: manifest=%r measured=%r" % (name, row["types"], actual["types"]))
                self.assertGreater(actual["entities"], 0, "%s has no entity at all" % name)

    def test_04_read_write_is_byte_exact_and_idempotent(self) -> None:
        """读→写逐字节相等，且写出的文件再读再写仍相等（语料必须落在"未改即不丢字节"这条判据上）。"""
        with tempfile.TemporaryDirectory() as tmp:
            for path in sorted(_CORPUS_DIR.glob("*.dxf")):
                with self.subTest(file=path.name):
                    original = path.read_bytes()
                    document = _load_dxf(path)
                    self.assertEqual(dxf_dump_bytes(document), original,
                                     "%s re-rendered bytes differ" % path.name)
                    self.assertEqual(dxf_verify_byte_identity(document).get("ok"), 1,
                                     "%s: verify_byte_identity did not report ok=1" % path.name)
                    target = Path(tmp) / path.name
                    dxf_write(document, str(target))
                    self.assertEqual(target.read_bytes(), original,
                                     "%s bytes differ after write()" % path.name)
                    second = dxf_read(str(target))
                    self.assertEqual(dxf_dump_bytes(second), original,
                                     "%s bytes differ after a second round-trip" % path.name)

    def test_05_every_dxf_is_utf8_with_lf_newlines(self) -> None:
        """语料一律 UTF-8 落盘 + LF 行尾（仓内可读可 diff；GBK 路径由下面的 CJK 测试现做对照）。"""
        for path in sorted(_CORPUS_DIR.glob("*.dxf")):
            with self.subTest(file=path.name):
                document = _load_dxf(path)
                self.assertEqual(document.encoding, "utf-8", "%s detected encoding is not utf-8" % path.name)
                self.assertEqual(document.newline_style, "\n", "%s newline style is not LF" % path.name)

    def test_06_no_shx_proprietary_fonts(self) -> None:
        """不许出现 SHX 专有字体：文件里搜不到 `.shx`，STYLE 表主字体（组码 3）必须是 TTF/OTF。"""
        for path in sorted(_CORPUS_DIR.glob("*.dxf")):
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(".shx", text.lower(), "%s references an SHX font" % path.name)
                document = _load_dxf(path)
                table = document.table("STYLE")
                self.assertIsNotNone(table, "%s has no STYLE table" % path.name)
                self.assertTrue(table.entries, "%s STYLE table is empty" % path.name)
                for entry in table.entries:
                    font = entry.get(3)
                    self.assertIsInstance(font, str, "%s STYLE entry lacks the primary font (code 3)" % path.name)
                    self.assertTrue(str(font).lower().endswith(_FONT_SUFFIXES),
                                    "%s primary font %r is not TrueType/OpenType" % (path.name, font))
                    big = entry.get(4)
                    self.assertIn(big, (None, ""),
                                  "%s STYLE entry must leave code 4 (big font, SHX only) unset, got %r"
                                  % (path.name, big))

    def test_07_unknown_entities_and_vendor_section_are_preserved(self) -> None:
        """未知实体 / 代理对象 / 未知段：认不出就**只保留不解释**，且字节照旧带回去。"""
        path = _CORPUS_DIR / "with_unknown_entity.dxf"
        document = _load_dxf(path)
        kinds = sorted({entity.type.upper() for entity in document.unknown_entities()})
        self.assertEqual(kinds, ["ACAD_PROXY_ENTITY", "MYAPP_NOTE"], "unknown entity set changed: %r" % kinds)
        known = [name.upper() for name in KNOWN_ENTITY_TYPES]
        for entity in document.unknown_entities():
            with self.subTest(entity=entity.type):
                self.assertNotIn(entity.type.upper(), known)
                self.assertEqual(entity.points(), [], "unknown entity must not guess geometry")
        self.assertEqual(len(document.proxy_entities()), 1, "expected exactly one proxy entity")
        self.assertIn("MYAPP_META", document.section_names(), "vendor section must survive verbatim")
        self.assertTrue(any("MYAPP_META" in warning for warning in document.warnings),
                        "unknown section must leave a kept-but-not-interpreted warning, got %r"
                        % document.warnings)
        self.assertEqual(dxf_dump_bytes(document), path.read_bytes(),
                         "unknown entity/section did not round-trip byte-exactly")

    def test_08_xdata_is_preserved(self) -> None:
        """XDATA（组码 ≥1000）保留：个数、应用名（1001）与原文都必须在。"""
        path = _CORPUS_DIR / "with_xdata.dxf"
        document = _load_dxf(path)
        by_type = {entity.type.upper(): entity for entity in document.entities()}
        self.assertEqual(sorted(by_type), ["CIRCLE", "LINE", "TEXT"], "entity type mix changed")
        line, circle, text = by_type["LINE"], by_type["CIRCLE"], by_type["TEXT"]
        self.assertEqual(line.xdata_apps, ["MYAPP"])
        self.assertEqual(circle.xdata_apps, ["ACAD", "MYAPP"], "both XDATA apps must survive on one entity")
        self.assertEqual(text.xdata_apps, ["MYAPP"])
        self.assertEqual([tag.code for tag in line.xdata], [1001, 1000, 1040, 1070],
                         "LINE XDATA tag sequence changed")
        self.assertEqual([tag.code for tag in text.xdata], [1001, 1000, 1040],
                         "TEXT XDATA tag sequence changed (1070 belongs to the CIRCLE, not here)")
        self.assertTrue(all(tag.code >= 1000 for tag in circle.xdata), "XDATA codes must be >= 1000")
        self.assertEqual(str(line.get(1000)), "girder-A", "XDATA string value must survive")
        self.assertEqual(float(circle.get(1040)), 4.0, "XDATA real value must survive")

    def test_09_cjk_text_is_readable_and_uses_truetype_style(self) -> None:
        """CJK 文字：中文 TEXT 必须原样读回，且用的样式是 TrueType（组码 3 为 .ttf，组码 4 缺省）。"""
        path = _CORPUS_DIR / "cjk_text.dxf"
        document = _load_dxf(path)
        texts = [entity.text() for entity in document.entities_of_type("TEXT")]
        self.assertEqual(len(texts), len(CJK_TEXT_SHA256),
                         "CJK fixture should hold %d TEXT entities, measured %d"
                         % (len(CJK_TEXT_SHA256), len(texts)))
        self.assertEqual([_digest(text) for text in texts], list(CJK_TEXT_SHA256),
                         "CJK TEXT content changed (digest mismatch); the readable copy of the expected "
                         "text lives in tests/corpus/dxf/README.md")
        utf8_texts = [text for text in texts if all(ord(char) < 128 for char in text)]
        self.assertEqual(utf8_texts, [], "CJK fixture text decoded to pure ASCII -- mojibake or wrong codec")
        non_ascii_layers = [name for name in document.layer_names() if any(ord(char) > 127 for char in name)]
        self.assertEqual(sorted(_digest(name) for name in non_ascii_layers), sorted(CJK_LAYER_SHA256),
                         "Chinese layer names changed (digest mismatch)")
        self.assertEqual(len(document.layer_names()), 3, "the fixture declares layer 0 + 2 Chinese layers")
        styles = {entity.get(7) for entity in document.entities_of_type("TEXT")}
        self.assertEqual(styles, {"HZ"}, "TEXT style names changed: %r" % styles)
        entry = [item for item in document.table("STYLE").entries if str(item.get(2)) == "HZ"]
        self.assertEqual(len(entry), 1, "STYLE table must hold exactly one HZ style")
        self.assertEqual(str(entry[0].get(3)), "NotoSansCJKsc-Regular.ttf", "HZ primary font is not TrueType")
        self.assertIn(entry[0].get(4), (None, ""), "code 4 (SHX big font) must be unset")

    def test_10_cjk_survives_gbk_reencode(self) -> None:
        """编码对照：把 CJK 语料**重新编成 GBK** 再读一次 —— 文本与实体计数必须和 UTF-8 版一模一样。

        为什么现做而不是再放一份 GBK 文件：语料要能在仓里直接 review（UTF-8）；
        而 reader 的 `utf-8 → gbk` 探测路径是真代码，必须被真打到。
        """
        path = _CORPUS_DIR / "cjk_text.dxf"
        utf8_doc = _load_dxf(path)
        try:
            gbk_bytes = path.read_text(encoding="utf-8").encode("gbk")
        except UnicodeEncodeError as exc:                 # pragma: no cover - 语料只用 GBK 可编码的字
            self.fail("CJK fixture holds a character GBK cannot encode (%s); "
                      "the Chinese fixture must stay inside the GBK charset" % exc)
        gbk_doc = dxf_parse_bytes(gbk_bytes, path=str(path) + " (gbk)")
        self.assertEqual(gbk_doc.encoding, "gbk", "GBK bytes must be caught by the gbk branch")
        gbk_texts = [entity.text() for entity in gbk_doc.entities_of_type("TEXT")]
        self.assertEqual([_digest(text) for text in gbk_texts], list(CJK_TEXT_SHA256),
                         "GBK-decoded text differs from the UTF-8 reading")
        self.assertEqual(gbk_doc.entity_count(), utf8_doc.entity_count())
        self.assertEqual(gbk_doc.render_bytes(), gbk_bytes, "GBK round-trip must be byte-exact")


    def test_11_readme_documents_the_cjk_digests(self) -> None:
        """单一真源：单测常量里的指纹必须在 `corpus/dxf/README.md` 的人读表里查得到（禁两套话）。"""
        text = _CORPUS_README.read_text(encoding="utf-8")
        for digest in CJK_TEXT_SHA256 + CJK_LAYER_SHA256:
            with self.subTest(digest=digest[:12]):
                self.assertIn(digest[:12], text,
                              "README must document CJK digest %s... (regenerate with --emit-cjk-digests)"
                              % digest[:12])


# --------------------------------------------------------------------------------------
# plate.json（G2 的 L1 模型）
# --------------------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

#: 实体 geom 的字段表 —— **与落盘的 `cadcore/sketch/constraints.py` 同一口径**：
#: 长度字段一律 `int` 纳米，角度字段是 `[0, 360)` 的浮点度（`arc` 独有）。
#: 表里没有的字段 = 夹具写错了字段名（`test_04` 会当场报红）。
_LENGTH_FIELDS = {
    "line": ("x1", "y1", "x2", "y2"),
    "circle": ("cx", "cy", "r"),
    "arc": ("cx", "cy", "r"),
}
_ANGLE_FIELDS = {"arc": ("start_deg", "end_deg")}


def _walk_json(node, path="$"):
    """深度遍历 JSON：产出 `(路径, 值)`。"""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_json(value, "%s.%s" % (path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_json(value, "%s[%d]" % (path, index))
    else:
        yield (path, node)


class PlateModelTest(unittest.TestCase):
    """`tests/data/plate.json`：结构合规 + 坐标/尺寸自洽 + 文案走 key（G2 的输入夹具）。"""

    @classmethod
    def setUpClass(cls) -> None:
        if not _PLATE_FILE.is_file():
            raise AssertionError("model fixture is missing: %s" % _PLATE_FILE)
        cls.raw_text = _PLATE_FILE.read_text(encoding="utf-8")
        cls.model = json.loads(cls.raw_text)
        cls.before = {path.name: _sha256(path) for path in _FIXTURE_FILES if path.is_file()}

    # —— 自证 ——

    def test_01_fixture_is_real_and_json_is_stable(self) -> None:
        """自证：真件在场，且"读进来再序列化"与原文的**规范化内容**一致（不是别人塞的内存对象）。"""
        self.assertGreater(_PLATE_FILE.stat().st_size, 0)
        self.assertIn("tests/data", _PLATE_FILE.as_posix())
        again = json.loads(json.dumps(self.model, ensure_ascii=True, sort_keys=True))
        self.assertEqual(again, self.model, "model parsed twice into different values")

    def test_zz_no_side_effects_on_repo_fixtures(self) -> None:
        for name, digest in self.before.items():
            with self.subTest(file=name):
                current = _sha256(next(path for path in _FIXTURE_FILES if path.name == name))
                self.assertEqual(current, digest, "%s was modified by this test module" % name)

    # —— 结构 ——

    def test_02_contract_sections_are_present(self) -> None:
        """契约 §C 的字段一个不少（少了 G2 的建模/出图就无据可依）。"""
        for key in ("schema_version", "meta", "unit", "standard_profile", "projection_angle",
                    "sketches", "features", "annotations", "sheets"):
            with self.subTest(key=key):
                self.assertIn(key, self.model, "missing field %r" % key)
        self.assertEqual(self.model["schema_version"], 1)
        self.assertEqual(self.model["unit"], "mm")
        self.assertIn(self.model["standard_profile"], ("iso", "gbt", "ansi", "din", "jis"))
        self.assertIn(self.model["projection_angle"], ("first", "third"))
        for key in ("doc_uuid", "created_at", "app_version", "title_key"):
            self.assertIn(key, self.model["meta"], "meta is missing %r" % key)
        self.assertTrue(self.model["sketches"], "at least one sketch is required")
        self.assertTrue(self.model["features"], "at least one feature is required")
        self.assertTrue(self.model["sheets"], "at least one sheet is required (G2 exports a drawing)")

    def test_03_no_user_facing_literals_in_model(self) -> None:
        """文案一律走语言包 key：模型文件里**一个中文字符都不许有**（用户可见文字从 key 现查）。"""
        hits = [(path, value) for path, value in _walk_json(self.model)
                if isinstance(value, str) and _CJK_RE.search(value)]
        self.assertEqual(hits, [], "model holds user-facing literals; use i18n keys instead: %r" % hits)

    def test_04_all_geometry_is_integer_nanometres(self) -> None:
        """长度真源一律整数纳米、角度一律 `[0,360)` 浮点度，且字段名与落盘的草图层同一套。

        这条尺子是**字段级**的：`geom` 的键集合必须恰好等于该实体类型的长度字段 + 角度字段，
        多一个少一个都报红 —— 夹具与 `cadcore/sketch/` 的 `geom` 口径脱节时，这里必须先响。
        """
        checked = 0
        for sketch in self.model["sketches"]:
            for entity in sketch["entities"]:
                kind = entity["kind"]
                geom = entity.get("geom")
                with self.subTest(entity=entity["uuid"], kind=kind):
                    self.assertIn(kind, _LENGTH_FIELDS, "unknown entity kind: %r" % kind)
                    self.assertIsInstance(geom, dict, "entity %s lacks geom" % entity.get("uuid"))
                    expected_keys = set(_LENGTH_FIELDS[kind]) | set(_ANGLE_FIELDS.get(kind, ()))
                    self.assertEqual(set(geom), expected_keys,
                                     "geom field names drifted from the sketch layer: %r" % sorted(geom))
                for field in _LENGTH_FIELDS[kind]:
                    with self.subTest(entity=entity["uuid"], field=field):
                        self.assertIs(type(geom[field]), int,
                                      "geom.%s must be an int nanometre, got %r" % (field, geom[field]))
                    checked += 1
                for field in _ANGLE_FIELDS.get(kind, ()):
                    with self.subTest(entity=entity["uuid"], field=field):
                        value = geom[field]
                        self.assertIsInstance(value, float, "geom.%s is a float degree, got %r" % (field, value))
                        self.assertTrue(0.0 <= value < 360.0,
                                        "geom.%s must be normalised to [0, 360), got %r" % (field, value))
                    checked += 1
        self.assertGreater(checked, 0, "no geometry number was checked -- the ruler missed the real thing")
        for feature in self.model["features"]:
            params = feature["params"]
            self.assertIs(type(params["distance_nm"]), int)
            self.assertIs(type(params["symmetric"]), bool)
            self.assertIsInstance(params["angle_deg"], str, "angles are stored as exact decimal strings")

    # —— 几何自洽（夹具自证的核心）——

    def _entity_index(self) -> dict:
        index: dict = {}
        for sketch in self.model["sketches"]:
            for entity in sketch["entities"]:
                self.assertNotIn(entity["uuid"], index, "duplicate entity uuid: %s" % entity["uuid"])
                index[entity["uuid"]] = entity
        return index

    @staticmethod
    def _measure(entity: dict, sub: str):
        """按 `geom` 现算一个可标注量的**定义**：`("coord", 轴, 值)` / `("point", x, y)` / `("radius", r)`。

        子形状名与落盘的草图层白名单同一套（line: edge/start/end，circle 与 arc: radius/center）。
        """
        kind = entity["kind"]
        geom = entity["geom"]
        if kind == "line":
            if sub in ("edge", ""):
                if geom["y1"] == geom["y2"]:
                    return ("coord", "y", geom["y1"])
                if geom["x1"] == geom["x2"]:
                    return ("coord", "x", geom["x1"])
                raise AssertionError("the dimension ruler only covers edges parallel to an axis: %s"
                                     % entity["uuid"])
            if sub == "start":
                return ("point", geom["x1"], geom["y1"])
            if sub == "end":
                return ("point", geom["x2"], geom["y2"])
        if kind in ("circle", "arc"):
            if sub in ("radius", ""):
                return ("radius", geom["r"])
            if sub == "center":
                return ("point", geom["cx"], geom["cy"])
        raise AssertionError("entity/sub-shape combination not covered by this ruler: %s / %r" % (kind, sub))

    def _expected_dimension_nm(self, dimension: dict, index: dict) -> int:
        kind = dimension["kind"]
        measurements = []
        for ref_uuid, sub in dimension["refs"]:
            entity = index.get(ref_uuid)
            self.assertIsNotNone(entity, "dimension references a missing entity: %s" % ref_uuid)
            measurements.append(self._measure(entity, sub))
        if kind in ("diameter", "radial"):
            self.assertEqual(len(measurements), 1, "a %s dimension references exactly one quantity" % kind)
            self.assertEqual(measurements[0][0], "radius")
            radius = measurements[0][1]
            return 2 * radius if kind == "diameter" else radius
        if kind == "linear":
            self.assertEqual(len(measurements), 2, "a linear dimension references two quantities")
            if all(item[0] == "coord" for item in measurements):
                (_, axis_a, value_a), (_, axis_b, value_b) = measurements
                self.assertEqual(axis_a, axis_b, "the two reference edges of a linear dimension are parallel")
                return abs(value_a - value_b)
            if all(item[0] == "point" for item in measurements):
                (_, x_a, y_a), (_, x_b, y_b) = measurements
                return abs(x_a - x_b) if x_a != x_b else abs(y_a - y_b)
            raise AssertionError("mixed reference kinds in a linear dimension: %r" % (measurements,))
        raise AssertionError("dimension kind not covered by this ruler: %r" % kind)

    def test_05_declared_dimensions_match_geometry(self) -> None:
        """**核心自证**：每个尺寸声明的 `value_nm` == 由它引用的几何现算出来的值（坐标笔误在这里必红）。"""
        index = self._entity_index()
        dimensions = [dim for sketch in self.model["sketches"] for dim in sketch["dimensions"]]
        self.assertGreater(len(dimensions), 0, "the sketch declares no dimension at all")
        for dimension in dimensions:
            with self.subTest(dim=dimension["uuid"], kind=dimension["kind"]):
                expected = self._expected_dimension_nm(dimension, index)
                self.assertEqual(dimension["value_nm"], expected,
                                 "dimension %s declares %r but the geometry measures %r"
                                 % (dimension["uuid"], dimension["value_nm"], expected))
                self.assertIs(type(dimension["value_nm"]), int)

    def test_06_at_least_eight_distinct_dimensionable_features(self) -> None:
        """G2 要求 `dim_entities>=8`：模型至少要有 8 个**互不相同**的可标注几何特征与对应注释。"""
        annotations = [item for item in self.model["annotations"] if item["kind"] == "dimension"]
        targets = {tuple(item["target"]) for item in annotations}
        self.assertGreaterEqual(len(annotations), 8, "fewer than 8 dimension annotations: %d" % len(annotations))
        self.assertGreaterEqual(len(targets), 8, "fewer than 8 distinct dimensionable features: %d" % len(targets))
        self.assertEqual(len({item["uuid"] for item in annotations}), len(annotations),
                         "duplicate annotation uuid")
        for item in annotations:
            with self.subTest(annotation=item["uuid"]):
                self.assertIs(type(item["value_nm"]), int)
                self.assertGreater(item["value_nm"], 0)
                self.assertFalse(item["literal"], "dimension text comes from an i18n key, never a literal")
        sketch_dimensions = [dim for sketch in self.model["sketches"] for dim in sketch["dimensions"]]
        self.assertEqual({dim["uuid"] for dim in sketch_dimensions}, {item["uuid"] for item in annotations},
                         "sketch dimensions and document annotations must be one and the same set of uuids")

    def test_07_outer_profile_closes_and_holes_stay_inside(self) -> None:
        """轮廓闭合 + 四个孔完全落在腹板内：拉伸与出图的几何前提（现算，不看声明）。"""
        index = self._entity_index()
        edges: dict = {}
        for entity in index.values():
            geom = entity["geom"]
            if entity["kind"] == "line":
                edges[entity["uuid"]] = ((geom["x1"], geom["y1"]), (geom["x2"], geom["y2"]))
            elif entity["kind"] == "arc":
                radius = geom["r"]
                points = []
                for angle in (geom["start_deg"], geom["end_deg"]):
                    points.append((int(round(geom["cx"] + radius * math.cos(math.radians(angle)))),
                                   int(round(geom["cy"] + radius * math.sin(math.radians(angle))))))
                edges[entity["uuid"]] = (points[0], points[1])
        self.assertEqual(len(edges), 8,
                         "the outer profile is 4 lines + 4 fillet arcs, measured %d edges" % len(edges))
        followers: dict = {}
        for start, end in edges.values():
            self.assertNotIn(start, followers, "the profile branches at %r" % (start,))
            followers[start] = end
        start = next(iter(followers))
        walked = [start]
        cursor = followers[start]
        while cursor != start:
            walked.append(cursor)
            self.assertIn(cursor, followers, "the profile breaks at %r (not closed)" % (cursor,))
            cursor = followers[cursor]
        self.assertEqual(len(walked), len(edges),
                         "the profile does not chain all 8 edges into one loop (walked %d)" % len(walked))
        for entity in index.values():
            if entity["kind"] != "circle":
                continue
            cx, cy = entity["geom"]["cx"], entity["geom"]["cy"]
            radius = entity["geom"]["r"]
            with self.subTest(circle=entity["uuid"]):
                self.assertLessEqual(abs(cx) + radius, 50000000, "a hole crosses the straight web edge")
                self.assertLessEqual(abs(cy) + radius, 30000000, "a hole crosses the straight web edge")

    def test_08_references_resolve(self) -> None:
        """引用完整：feature 的 inputs 指向草图、注释的 target 指向特征、图纸的 dimensions 指向注释。"""
        sketch_uuids = {sketch["uuid"] for sketch in self.model["sketches"]}
        entity_uuids = {entity["uuid"] for sketch in self.model["sketches"] for entity in sketch["entities"]}
        feature_uuids = {feature["uuid"] for feature in self.model["features"]}
        annotation_uuids = {item["uuid"] for item in self.model["annotations"]}
        for feature in self.model["features"]:
            with self.subTest(feature=feature["uuid"]):
                self.assertIn(feature["kind"], ("extrude", "revolve", "boolean"))
                self.assertTrue(set(feature["inputs"]) <= (sketch_uuids | feature_uuids),
                                "feature.inputs points at a missing object: %r" % feature["inputs"])
        for sketch in self.model["sketches"]:
            for constraint in sketch["constraints"]:
                for ref_uuid, _sub in constraint["refs"]:
                    self.assertIn(ref_uuid, entity_uuids, "constraint references a missing entity: %s" % ref_uuid)
        for item in self.model["annotations"]:
            with self.subTest(annotation=item["uuid"]):
                self.assertIn(item["target"][0], feature_uuids,
                              "annotation target is not a feature: %r" % item["target"])
        for sheet in self.model["sheets"]:
            with self.subTest(sheet=sheet["uuid"]):
                self.assertTrue(set(sheet["dimensions"]) <= annotation_uuids,
                                "the sheet references a missing annotation: %r" % sheet["dimensions"])
                self.assertTrue(sheet["views"], "a sheet needs at least one view")
                for view in sheet["views"]:
                    self.assertEqual(len(view["direction"]), 3, "a view direction is a 3D integer vector")

    def test_09_solved_cache_never_fakes_success(self) -> None:
        """口径：未采就是未采。`dof=-1`（未采）时不许同时声明 `converged=true`（假装求解成功）。"""
        for sketch in self.model["sketches"]:
            solved = sketch["solved"]
            with self.subTest(sketch=sketch["uuid"]):
                self.assertIs(type(solved["dof"]), int)
                self.assertGreaterEqual(solved["dof"], -1, "dof is >= 0, or -1 for not-measured")
                self.assertIs(type(solved["converged"]), bool)
                if solved["dof"] == -1:
                    self.assertFalse(solved["converged"],
                                     "dof=-1 means not-measured; claiming converged=true would fake success")
                self.assertTrue(solved["cache"], "solved is a cache node that can be deleted and rebuilt")

    def test_10_projection_matches_standard_profile(self) -> None:
        """投影角与标准档一致（换标准档 ⇒ 投影必须变，这正是 G2 的阴性对照要打的东西）。"""
        path = _STANDARDS_DIR / ("%s.json" % self.model["standard_profile"])
        if not path.is_file():
            self.skipTest("NA: standard profile %s not landed, projection consistency unverifiable" % path.name)
        profile = json.loads(path.read_text(encoding="utf-8"))
        expected = profile.get("projection_angle")
        if expected is None:
            self.skipTest("NA: standard profile %s leaves projection_angle unverified (null)" % path.name)
        self.assertEqual(self.model["projection_angle"], expected,
                         "model projection angle disagrees with standard profile %s" % path.name)
        self.assertEqual(self.model["sheets"][0]["standard_profile"], self.model["standard_profile"],
                         "sheet standard profile must match the document one")

    def test_11_text_keys_exist_in_every_catalog(self) -> None:
        """模型里引用的语言包 key 必须在**每一个**语言包里都存在（否则用户会看到回落串或 key 名）。"""
        catalog_files = sorted(_CATALOG_DIR.glob("*.json")) if _CATALOG_DIR.is_dir() else []
        catalog_files = [path for path in catalog_files if path.name != "languages.json"]
        if not catalog_files:
            self.skipTest("NA: language pack directory not landed (%s), key existence unverifiable" % _CATALOG_DIR)
        catalogs = {}
        for path in catalog_files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            catalogs[path.name] = set(payload.get("strings", {}))
            self.assertTrue(catalogs[path.name], "catalog %s has an empty strings map" % path.name)
        keys = {self.model["meta"]["title_key"]}
        for sketch in self.model["sketches"]:
            keys.update(dim["text_key"] for dim in sketch["dimensions"] if dim.get("text_key"))
        for item in self.model["annotations"]:
            if item.get("text_key"):
                keys.add(item["text_key"])
        for sheet in self.model["sheets"]:
            keys.update(value for value in sheet["title_block"].values() if isinstance(value, str))
        for name, available in sorted(catalogs.items()):
            missing = sorted(keys - available)
            with self.subTest(catalog=name):
                self.assertEqual(missing, [], "%s is missing these keys: %r" % (name, missing))

    def test_12_docmodel_can_load_it_when_present(self) -> None:
        """跨模块：`cadcore.docmodel` 落盘后，它必须能打开 plate.json 且不变量检查为空。"""
        try:
            from cadcore import docmodel  # type: ignore[import-not-found]
        except Exception as exc:
            self.skipTest("NA: cadcore.docmodel not landed (%s: %s)" % (type(exc).__name__, exc))
        if not hasattr(docmodel, "document_from_l1"):
            self.skipTest("NA: cadcore.docmodel has no document_from_l1 yet")
        document = docmodel.document_from_l1(self.raw_text)
        self.assertIsNotNone(document)
        if hasattr(docmodel, "check_invariants"):
            problems = docmodel.check_invariants(document)
            self.assertEqual(list(problems), [], "docmodel reported invariant violations: %r" % (problems,))


    def test_13_sketch_layer_accepts_the_model_when_present(self) -> None:
        """跨模块：落盘的 `cadcore/sketch` 必须**原样吃下**这份模型（字段名/白名单/闭合轮廓都现算）。

        为什么放在夹具这边：`geom` 的字段名与子形状白名单是 A9/A10 定的，夹具一旦写歪，
        G2 出图只会画出"看着挺像"的东西；这条把两边钉在一起，谁改了口径这里先红。
        """
        try:
            from cadcore.sketch import constraints as sketch_constraints  # type: ignore[import-not-found]
        except Exception as exc:
            self.skipTest("NA: cadcore.sketch.constraints not landed (%s: %s)" % (type(exc).__name__, exc))
        validated = 0
        for sketch in self.model["sketches"]:
            for dimension in sketch["dimensions"]:
                with self.subTest(dimension=dimension["uuid"]):
                    sketch_constraints.validate_dimension(
                        sketch, kind=dimension["kind"],
                        refs=[tuple(ref) for ref in dimension["refs"]], value_nm=dimension["value_nm"])
                    validated += 1
            for constraint in sketch["constraints"]:
                with self.subTest(constraint=constraint["uuid"]):
                    sketch_constraints.validate_constraint(
                        sketch, kind=constraint["kind"],
                        refs=[tuple(ref) for ref in constraint["refs"]], value_nm=constraint["value_nm"])
                    validated += 1
        self.assertGreater(validated, 0, "the sketch layer validated nothing -- the ruler missed the real thing")
        loops = sketch_constraints.closed_profiles(self.model["sketches"][0])
        outer = [loop for loop in loops if len(loop) == 8]
        self.assertEqual(len(outer), 1, "expected exactly one 8-edge closed outer profile, got %r" % (loops,))
        self.assertEqual(len(loops), 5, "expected the outer ring plus 4 hole circles, got %d loop(s)" % len(loops))

    def test_14_model_exposes_at_least_eight_measurable_subshapes(self) -> None:
        """G2 的 `dim_entities>=8` 下界：按落盘命令层认得的口径数一遍**可测子形状**，必须够 8 个。"""
        measurable_sub = {"line": "edge", "circle": "radius", "arc": "radius"}
        sketch = self.model["sketches"][0]
        measurable = 0
        for entity in sketch["entities"]:
            with self.subTest(entity=entity["uuid"], kind=entity["kind"]):
                self.assertIn(entity["kind"], measurable_sub,
                              "no measurable sub-shape is defined for kind %r" % entity["kind"])
                measurable += 1
        self.assertGreaterEqual(measurable, 8,
                                "the model exposes only %d measurable sub-shapes" % measurable)

    def test_15_view_directions_match_draw_layer_when_present(self) -> None:
        """跨模块：图纸视图的 `direction` 必须是落盘 `cadcore/draw/sheet.py` 认得的那几个向量。"""
        try:
            from cadcore.draw import sheet as draw_sheet  # type: ignore[import-not-found]
        except Exception as exc:
            self.skipTest("NA: cadcore.draw.sheet not landed (%s: %s)" % (type(exc).__name__, exc))
        directions = getattr(draw_sheet, "VIEW_DIRECTIONS", None)
        if not isinstance(directions, dict) or not directions:
            self.skipTest("NA: cadcore.draw.sheet exposes no VIEW_DIRECTIONS map")
        allowed = {tuple(value) for value in directions.values()}
        for sheet in self.model["sheets"]:
            self.assertTrue(sheet["views"], "a sheet needs at least one view")
            for view in sheet["views"]:
                with self.subTest(view=view["uuid"]):
                    self.assertIn(tuple(view["direction"]), allowed,
                                  "view direction %r is not one of %r" % (view["direction"], sorted(allowed)))


# --------------------------------------------------------------------------------------
# 源码纪律（自查本文件；与兄弟文件 test_i18n_catalog.py::SourceDisciplineTests 同款尺子）
# --------------------------------------------------------------------------------------

#: 与 i18n 门同款的"中文字面量"字符类（CJK 标点 + 汉字 + 全角区）
_CJK_LITERAL_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
_SELF_SOURCE = _HERE


def _string_literals(source: str) -> list:
    """源码里**非 docstring** 的字符串字面量值（docstring 不算文案，与 i18n 门同口径）。"""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            out.append(node.value)
    return out


class SourceDisciplineTest(unittest.TestCase):
    """本文件自己也要守纪律：`.py` 里不许有中文**值**（中文只出现在 docstring / 注释里）。"""

    def test_01_module_under_test_is_the_real_file(self) -> None:
        """夹具自证：扫的就是仓里这个文件（不是内存副本、也不是别处的同名模块）。"""
        self.assertTrue(_SELF_SOURCE.is_file())
        self.assertIn("packages/cadcore/tests", _SELF_SOURCE.as_posix())
        self.assertIn("def _string_literals", _SELF_SOURCE.read_text(encoding="utf-8"))

    def test_02_ruler_sees_red_on_a_mutated_copy(self) -> None:
        """变异体反证：往源码副本里注入一条中文字面量，尺子必须**恰好**看见那一条。"""
        source = _SELF_SOURCE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            mutated = Path(tmp) / "test_fixtures_mutated.py"
            mutated.write_text(source + '\n_LEAK_TEXT = "\\u56fe\\u5c42"\n', encoding="utf-8")
            hits = [text for text in _string_literals(mutated.read_text(encoding="utf-8"))
                    if _CJK_LITERAL_RE.search(text)]
            self.assertEqual(len(hits), 1,
                             "the ruler must see exactly the injected literal, saw %d: %r" % (len(hits), hits))
            self.assertTrue(_CJK_LITERAL_RE.findall(hits[0]))

    def test_03_module_source_has_no_cjk_string_literals(self) -> None:
        """真件上跑：本文件除 docstring 外的字面量里，一个中文字符都不许有。"""
        hits = [text for text in _string_literals(_SELF_SOURCE.read_text(encoding="utf-8"))
                if _CJK_LITERAL_RE.search(text)]
        self.assertEqual(hits, [], "keep fixture copy in docstrings or in data files, never in .py literals: %r"
                         % hits)


# --------------------------------------------------------------------------------------
# 清单再生成（改了语料之后用）
# --------------------------------------------------------------------------------------


def _emit_manifest() -> int:
    """按 reader 现算的结果打印清单行（coverage 列沿用 README 里已有的文字）。"""
    _require_dxf()
    existing = _read_manifest(required=False)
    for path in sorted(_CORPUS_DIR.glob("*.dxf")):
        actual = _actual_counts(path)
        coverage = existing.get(path.name, {}).get("coverage", "")
        print(_manifest_line(actual, coverage))
    return 0


def _emit_cjk_digests() -> int:
    """打印 `cjk_text.dxf` 里 TEXT 正文与中文图层名的 sha256（连同可读原文，供人核对后贴回常量）。"""
    document = _load_dxf(_CORPUS_DIR / "cjk_text.dxf")
    print("CJK_TEXT_SHA256 = (")
    for entity in document.entities_of_type("TEXT"):
        print('    "%s",   # %s' % (_digest(entity.text()), entity.text()))
    print(")")
    print("CJK_LAYER_SHA256 = (")
    for name in document.layer_names():
        if any(ord(char) > 127 for char in name):
            print('    "%s",   # %s' % (_digest(name), name))
    print(")")
    return 0


if __name__ == "__main__":
    if "--emit-manifest" in sys.argv:
        raise SystemExit(_emit_manifest())
    if "--emit-cjk-digests" in sys.argv:
        raise SystemExit(_emit_cjk_digests())
    unittest.main()
