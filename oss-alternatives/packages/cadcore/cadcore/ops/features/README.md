# packages/cadcore/cadcore/ops/features —— 功能片注册表（feature slice registry）

**一句话用途**：cadcore 操作功能片的注册表 —— 扫描本目录 `*_w*.py`，把 `<family>__w<k>.py` 的
`FAMILY` + `OPERATIONS` 汇总成 `{family: {op_name: callable}}`，并把同族功能片并入族模块 `OPERATIONS`
视图，供 parity 红门当探针集 / cadcore ops registry / feature slice registry / 功能片注册表。

关键中英文检索词：功能片 / feature slice / 注册表 / registry / `load_all` / `merge_into` / FAMILY / OPERATIONS。

## 1. 冻结契约

功能片 = 独立模块 `cadcore/ops/features/<family>__w<k>.py`（`__` 双下划线；单下划线 `<family>_w<k>.py`
也接受，族名从文件名回退解析）：

| 名字 | 类型 | 约束 |
|---|---|---|
| `FAMILY` | `str` | 非空；归一化后与族模块一致（`sw_drawings` → `sw-drawings`） |
| `OPERATIONS` | `dict[str, Callable]` | 非空；名字非空字符串、值可调用 |

操作签名与族模块（`cadcore/ops/<family>.py`）一致：第一个位置参 `sk_or_doc`，**其余关键字**；
返回 uuid 字符串列表；非法输入抛 `cadcore.errors.CadError`。

## 2. 用法

```python
import sys; sys.path.insert(0, "packages/cadcore")
from cadcore.ops.features import load_all, merge_into, load_report, failed

loaded = load_all()          # {family: {op_name: callable}}，import 失败的片被隔离
print(loaded, failed, load_report())

from cadcore.ops import annotation as fam     # 族模块
view = merge_into(fam)       # fam.OPERATIONS <- SliceView（族模块自身操作优先）
print(view.report())         # ops_total / from_family_module / from_slices / collisions / origin
```

* `load_all(*, directory=None, strict_signature=False, reload=False)`：缺省扫本目录；`directory=` 供自检注入
  合成片目录；`reload=True` 重执行源码。
* 隔离：import 失败 → `failed` 记 `kind=import_failed`；`FAMILY`/`OPERATIONS` 坏 → `kind=contract_broken`；
  签名疑点缺省只记 `warnings`（`kind=signature_suspect`，片**仍被加载**），`strict_signature=True` 时升为
  `signature_broken` 并隔离。**任何一片坏都不会带崩整体。**
* 冲突：同名操作按文件名字典序先到者胜，落 `collisions`；`merge_into` 里**族模块自身操作优先**。
* `merge_into` 幂等：重复调用不会套娃视图，也不丢片操作。
* 附：`validate_result(result, op=...)` 校验返回值 = uuid 字符串列表，不合规抛 `SliceContractError`。

## 3. 收尾自检（真跑，原样贴回）

```bash
cd /data/dev/infra/oss-alternatives/packages/cadcore
python3 -c "import sys;sys.path.insert(0,'.');from cadcore.ops.features import load_all;print(load_all())"
```

```text
{}
```

`{}` 是**如实结果**：本目录此刻还没有任何 `*_w*.py` 功能片落盘（不是注册表没工作）。完整报告：

```text
$ python3 -c "import sys;sys.path.insert(0,'.');import cadcore.ops.features as r;print('LOADED', r.load_all());print('FAILED', r.failed);print('REPORT', r.load_report())"
LOADED {}
FAILED []
REPORT {'features_dir': '/data/dev/infra/oss-alternatives/packages/cadcore/cadcore/ops/features', 'slices': [], 'failed': [], 'warnings': [], 'collisions': [], 'loaded_families': []}
```

合成片探针（临时目录注入 4 片：1 好 + 1 import 崩 + 1 契约坏 + 1 签名坏；含 `merge_into` 优先级 /
冲突 / 幂等 / strict 分支断言）原样输出：

```text
B1 families: ['annotation', 'output'] | annotation_ops: ['add_dimension', 'add_note'] | output_ops: ['bad_sig']
B2 failed: [('annotation__w2', 'import_failed'), ('output__w1', 'contract_broken')]
B3 warnings: [('output__w2', 'bad_sig:signature:extra_positional:extra')]
B4 merged ops: ['add_dimension', 'add_leader', 'add_note'] | fam.OPERATIONS is SliceView: True
B5 collisions: [{'op': 'add_dimension', 'kept': 'family_module', 'dropped': 'annotation__w1'}]
B6 idempotent re-merge ok, ops_total: 3 | from_family_module: 2 from_slices: 1
B7 output family: contract-broken slice NOT merged, suspect slice merged -> ['bad_sig'] | isolated: [('annotation__w2', 'import_failed'), ('output__w1', 'contract_broken')]
B8 validate_result: ['a', 'b']
B8 contract red as expected: op_result_not_uuid_list:?:int
B9 strict loaded: ['annotation'] | output_ops: [] | failed kinds: ['import_failed', 'contract_broken', 'signature_broken']
B10 loaded_families: ['annotation', 'output'] | slices: ['annotation__w1', 'output__w2']
ALL_SYNTHETIC_ASSERTS_PASSED
```

## 4. 欠账（**未完成，不许当绿**）

1. **4 道红门尚未接本注册表** —— `tools/cad/parity_annotation_gate.py`、`parity_output_gate.py`、
   `parity_precision_ops_gate.py`、`parity_sw_drawings_gate.py` 目前只扫 `cadcore/ops/*.py`（各自
   `_family_module_paths()` / `_scan_family_files()`），**不会**加载 `ops/features/` 下同族片的 OPERATIONS。
   本席写权只有 `features/__init__.py` 与 `features/README.md` 两个文件（并行席隔离），**改门不在本席写权内**
   ⇒ 如实留 missing，未改、未假装改过。接手判据：探针集 = 族模块 OPERATIONS ∪ `load_all()` 中同族片 OPERATIONS
   （分母不变，仍以 must_have 为准）。
2. **must_have 清单的真实路径**是 `docs/research/cad-parity/features/autocad-<族>.json`
   （12 个族：annotation/output/precision/sw-drawings 等），**不是** `cadcore/ops/features/<族>.json`；
   接门时按前者取 must_have。
3. 本目录**暂无功能片**（`*_w*.py` 为 0 件），所以真实 `load_all()` 返回 `{}`；片的落盘属各族的席。
