# RepoRescue

[English](README.md) | [简体中文](README.zh-CN.md)

用证据解释 Python 项目为什么跑不起来，并生成可供审查的修复预览和验证计划。

**v0.3.0-alpha.1 · Windows First · Python only · READ ONLY by default · MIT**

## 演示

![RepoRescue 诊断 Python 模块导入失败](docs/assets/repo-rescue-demo.gif)

使用已安装的 CLI，诊断一个结果确定的模块导入失败样例。

## RepoRescue 是什么

RepoRescue 把版本、解释器和启动错误等线索整理成一条可审查的流程：

证据 → 诊断 → 根因链 → 修复预览 → 验证计划。

它帮助你判断下一步该检查什么、建议的修复涉及什么，以及之后如何验证。
Traceback 给出症状，RepoRescue 将受支持的症状与实际捕获的证据关联起来；
证据不足时不会把猜测写成确定根因。没有发现问题，也不代表整个项目可以正常运行。

## 快速开始（Windows）

使用 Python 3.12 或更新版本。在源码仓库中创建并激活环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
repo-rescue --help
repo-rescue --version
```

普通诊断不会执行项目代码：

```powershell
repo-rescue "C:\work\sample"
```

显式确认本次运行受支持的启动探测：

```powershell
repo-rescue "C:\work\sample" --run-startup-probe
```

**`--run-startup-probe` 会执行项目代码。** 该参数确认运行风险等级为 CAUTION 的根目录 `main.py` 启动探测，
不会再弹出交互确认。项目代码自身可能写文件、访问网络或启动服务；该参数不会绕过安全检查。

如果未激活环境，可将 `repo-rescue` 替换为 `.\.venv\Scripts\repo-rescue.exe`。
例如，未激活环境时可直接运行 `.\.venv\Scripts\repo-rescue.exe --version`。
安装后也可以使用 `python -B -m agent_doctor.cli <project>`。
软件包版本为 `0.3.0a1`，内部 Python 包名为 `agent_doctor`。
项目曾用名为 Agent Doctor；临时兼容命令 `agent-doctor` 会调用同一个 CLI（命令行工具）。

如果 GitHub Releases 提供 release wheel，可下载后安装本地文件：

```powershell
python -m pip install <path-to-downloaded-wheel>
```

### 退出码

- `0`：诊断完成，没有 ERROR/CRITICAL；可能存在 WARNING。
- `1`：诊断完成，发现 ERROR/CRITICAL。
- `2`：参数或输入无效、报告写入失败，或工具自身出错。

## 当前诊断能力

| 观察到的情况 | RepoRescue 的处理 |
| --- | --- |
| `requires-python` 不匹配 | 在支持的约束范围内报告 ERROR |
| 项目本地解释器不匹配 | 报告 WARNING，不能据此证明项目故障 |
| `ModuleNotFoundError` | 识别明确的缺失模块异常行 |
| `ImportError` 符号导入失败 | 有限支持明确的 cannot-import-name 异常行 |
| 启动非零退出 | 报告 ERROR 症状，具体导入诊断保持独立 |
| 启动超时 | 记录 INFO 观察，不能据此判断卡死或已就绪 |

项目检测只扫描根目录和直接子目录，最多 1,000 个条目；`likely` / `unknown` 表示文件名证据，
不表示应用已经可以启动。工具检查当前解释器，并可能受控执行 `--version`。
对于本地 `.venv` / `venv`，只查找 `Scripts/python.exe` 或 `bin/python` 并比较文件身份，
不会运行候选解释器；存在真实歧义时不会自动选择。

根目录 `pyproject.toml` 的版本检查支持 `>=`、`>`、`<=`、`<`、`==`、`!=`、逗号组合及
`major.minor[.patch]`；相等和排除约束还支持 `.*`。这不是完整 PEP 440：
`~=`、预发布版本等不支持的约束只会产生能力限制说明，不猜测不兼容。
导入名不会被映射成 PyPI 分发包名，也不据此断言缺少某个软件包。

GitHub Actions 已验证 Windows + Python 3.12 / 3.13，本地验证使用 Python 3.13.1。
Linux/macOS 尚未单独验证；runtime dependencies 为 `[]`。

## 示例与输出流程

安装并激活环境后，从仓库根目录运行：

```powershell
repo-rescue examples/fixtures/missing-module --run-startup-probe
```

以下选自真实 CLI 输出；省略其他行，但不改写原文：

```text
[CAUTION] Executing project code for the startup probe.
Project code may have its own side effects.
Project: examples\fixtures\missing-module
Findings:
[ERROR] Python import: Python could not import the module 'reporescue_fixture_missing_dependency_xyz'.
[ERROR] Startup probe exited with code 1.
  Missing import: reporescue_fixture_missing_dependency_xyz
Root cause:
  ModuleNotFoundError names 'reporescue_fixture_missing_dependency_xyz' -> The requested import 'reporescue_fixture_missing_dependency_xyz' did not complete -> The providing distribution and underlying cause are not established
Repair preview:
  Suggested repair (preview only, MEDIUM): Confirm which distribution provides the missing import module and ensure it is available in the intended Python environment.
Verification (planned, not run):
  - Using the intended interpreter, verify that importing 'reporescue_fixture_missing_dependency_xyz' completes successfully.
  - After explicit confirmation, rerun the same supported startup probe.
No repair actions were executed.
```

该演示样例的结果确定，不访问网络，也不进行持久文件写入。
通常几秒内结束，不需要额外安装软件包或交互确认，也不会生成 `__pycache__`。
退出码 1 是预期结果。不加该参数时，启动探测仍为 `requires_confirmation`，不会执行项目代码。

### 故障样例

[examples/fixtures](examples/fixtures/README.md) 包含结果确定的故障项目，用于回归测试、演示和发布验证，
不是生产应用示例：

- `python-version-mismatch`
- `missing-module`
- `symbol-import-failure`
- `startup-nonzero`
- `startup-timeout`
- `combined-failures`

这些样例不需要网络或第三方软件包安装。显式确认启动前，请先检查它们的简短源码。
符号导入样例禁用本地字节码生成；超时样例会刻意运行到超出观察窗口。

## JSON / 自动化

将结构化报告写入新文件：

```powershell
repo-rescue "C:\work\sample" --output "C:\work\report.json"
```

不指定 `--output` 就不会写报告文件。输出的父目录必须已经存在，已有报告不会被覆盖。
写入失败时可能留下不完整的新文件。

JSON schema **`0.2`** 与软件包版本 `0.3.0a1` 分开管理。报告包含项目和环境信息、结构化 Evidence、诊断、
根因链、修复预览及验证计划。字段定义见 [JSON 契约](docs/json-schema.md)。
`healthy` 状态只表示当前有限检查未发现问题，不是对项目整体健康的保证；仅有 INFO 不表示项目故障。

JSON 可以用于自动化和 CI，并为未来与编程 Agent 的原生集成提供数据基础。
Agent 集成和 MCP **尚未实现**；当前没有 Codex、Claude Code 或 Cursor 集成。

## 安全与限制

- **默认诊断不执行项目代码。** RepoRescue 本身不会修改用户项目文件、改变依赖、运行测试或构建，也不会执行修复。
  当前 Python 的 `--version` 探测与项目代码执行是不同操作。
- **启动需要显式确认。** 只支持绝对路径的当前 Python 解释器，加上绝对路径的根目录 `main.py`。
  执行器独立检查完整 argv、工作目录和解析后的入口路径。不接受任意 shell 命令、额外参数、其他入口或环境覆盖。
  执行时使用 `shell=False`、禁用标准输入，并复制当前环境；不读取 `.env`。
  没有受支持的入口属于能力限制。
- **启动探测风险为 CAUTION。** 项目代码可能写文件或字节码、访问网络、启动服务或阻塞。
  RepoRescue 不会自动运行 pip、安装软件包、修改依赖声明或修复项目。
- **观察范围有界。** 启动观察窗口为 5 秒。超时后停止直接子进程，终止确认和输出清理最多可能再用两秒。
  不监督后代进程。启动捕获每个流最多保留 64 KiB，返回的 stdout/stderr 摘录各最多 4,096 个字符。
- **结果只说明有限观察。** 退出码 0 只表示探测成功结束；长时间运行的应用超时可能是正常现象。
  结构化 Evidence 记录实际观察。修复计划保持 `not_executed`，验证步骤保持 `not_run`。
- **这些控制不是沙箱。** 没有通用 secret 脱敏、进程树或资源隔离，也不能防止并发路径变化。
  终端路径简化仅用于展示。JSON 和捕获输出仍可能包含本机路径或敏感数据，分享前请审查报告。

漏洞的私下反馈方式见 [SECURITY.md](SECURITY.md)。

自动修复、执行验证和回滚均未实现。当前真实诊断能力仍是 Python only；
其他语言及产品形态属于后文的未来规划。

## 扩展 RepoRescue

v0.3 已实现实验性的扩展基础，面向希望补充领域诊断知识的开发者。
普通 CLI 用户可以直接使用前面的命令，不需要编写或加载 Pack。

程序化接入流程为：

公共 SDK → 显式注册 / 受控的已安装软件包发现 → Extension Pipeline → Core。

Discovery 将实例加入 registry，宿主再显式将 snapshot 提供给 Core pipeline。
这些 API 不会给普通 CLI 增加 Pack 加载参数。

### 公共 SDK

Pack 作者应从 `agent_doctor.sdk` 导入类型，避免依赖私有模块。
SDK 通过明确的 public surface 导出 metadata、能力协议、Core models，以及作者需要的验证和错误类型，
复用原有类型，不维护另一套 models。

`EXTENSION_API_VERSION = "1"` 在 v0.3 中仍为实验性，与软件包版本 `0.3.0a1`、
JSON schema `0.2` 分开管理。SDK 不暴露 workflow 内部实现、Core 执行对象或 Python 私有诊断逻辑。

### Pack model

Pack 是某个技术领域的知识集合；Extension contract 定义它能提供什么能力。
Metadata 声明稳定 ID、`PackKind`、API 版本、平台、所需工具和 `Capability` 集合。

| Contract | Pack 提供的内容 |
| --- | --- |
| Detector | 识别项目特征 |
| EvidenceProvider | 收集观察证据 |
| DiagnosisRule | 生成与证据关联的诊断 |
| RepairPlanner | 描述修复动作与风险 |
| Verifier | 描述验证步骤 |

Pack 负责知识和计划，Core 负责调度、兼容性、安全、确认、资源限制和执行权限。
唯一真实的内置语言 Pack 是 `python.core`；其他 kind 不代表已实现对应支持。

### PackRegistry

`PackRegistry()` 创建空 registry，`PackRegistry.default()` 只包含 `python.core`。
通过 `register(pack)` 显式注册实例，插入前完成声明验证和重复 ID 检查；
失败注册不改变已有成员，也不会调用 Pack 的业务阶段。

用 `snapshot()` 捕获注册顺序；运行时兼容性由 Core pipeline 判断。
声明稳定性和数据使用规则见 SDK 指南。

### 受控 discovery

宿主可以显式发现当前 Python 环境中已安装、可信的软件包：

```python
from agent_doctor.sdk import PackRegistry, discover_installed_packs

registry = PackRegistry.default()
result = discover_installed_packs(registry)
snapshot = registry.snapshot()
```

Discovery 只查询 `reporescue.packs` entry points，由无参数 factory 返回 Pack 实例。
处理顺序确定，普通 discovery 错误互相隔离，重复 ID 不覆盖已有 Pack。
它不是 installer、marketplace 或 enable/disable 管理器，不扫描插件目录、不查询网络，
也不调用 Pack 的诊断阶段。

**CLI 不会自动发现或加载第三方 Pack。** 导入 SDK 或创建默认 registry 也不会触发 discovery。

### 最小示例 Pack

[官方示例](examples/extensions/example_language_pack.py) 的 ID 为 `example.language`，
只依赖公共 SDK 和 Python 标准库。它观察 `.reporescue-example` 标记，
生成 Evidence、INFO 诊断及描述性的修复/验证计划，不执行命令，也不修改项目。

[SDK 演示目录](examples/extension-demo/README.md) 是 synthetic 示例，独立于真实故障样例。
Example Pack 不默认加载，也不作为 wheel runtime module 安装。在源码仓库中可以显式注册：

```python
from agent_doctor.sdk import PackRegistry
from examples.extensions.example_language_pack import ExampleLanguagePack

registry = PackRegistry.default()
registry.register(ExampleLanguagePack())
snapshot = registry.snapshot()
```

### 信任边界与开发文档

**第三方 Pack 是可执行 Python，validation 不是 sandbox。**
加载 entry point 和调用 factory 都会执行其代码。SDK 不授予 Core executor、任意 shell 或修复权限，
但第三方代码仍可自行使用其他 Python API 并产生副作用。只安装、加载可信来源的 Pack。

先阅读 [SDK 指南](docs/extension-sdk.md)，了解作者契约和接入示例。
[架构文档](docs/architecture.md) 区分当前 v0.3 实现与历史 v0.1 设计。
[发布说明](docs/releases/v0.3.0-alpha.1.md)、[CHANGELOG](CHANGELOG.md) 和 [docs](docs/)
提供兼容性与发布背景。

## 路线图

以下均为未来规划，此 alpha 版本尚未实现，也不承诺交付时间：

- 规划中：深入 Python 诊断。
- 规划中：Node / Java / C++ / Docker 支持。
- 规划中：安全修复执行器（Safe Repair Executor）。
- 规划中：执行验证与回滚。
- 规划中：RepoRescue Desktop，为不熟悉命令行的用户提供 GUI / EXE。
- 规划中：Agent 集成。

当前工具没有 GUI、其他语言支持，也不依赖 LLM。

## 参与贡献

从仓库根目录运行：

```powershell
python -m unittest discover
```

测试不需要安装项目或手工设置 `PYTHONPATH`。默认工作流测试不执行项目代码；
启动测试会显式确认运行经过审查的小型样例，并检查样例文件与修改时间保持不变。

[GIF 生成脚本](tools/generate_demo_gif.py) 使用已安装的项目 CLI，并将 Pillow 作为本地文档工具。
Pillow 不是运行时依赖；缺少 Pillow 时只显示明确提示，不会自动安装。

请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。保持改动聚焦，验证其行为，并提供结果证据。
运行时依赖仍为空。源码仓库：[furX7/RepoRescue](https://github.com/furX7/RepoRescue)。

## 安全反馈

漏洞反馈方式见 [SECURITY.md](SECURITY.md)。分享敏感细节前先安排私下反馈渠道，
不要在公开 issue 中贴出凭证或私人用户数据。

## License

[MIT](LICENSE)。Copyright (c) 2026 RepoRescue contributors.


### 依赖版本来源（v0.4 Phase 1 / Step 4）

只读 Evidence 分别记录 declared 声明约束、locked 锁定版本、用户提供安装日志中明确的
pip dry-run resolved 选择，以及当前解释器的 installed 分发元数据。版本数字不同不会
自动触发兼容性诊断。每条记录保留来源及具体位置；不确定或冲突信息保持
unknown/unavailable/ambiguous。支持的格式与资源边界见
[JSON 契约](docs/json-schema.md#version-provenance--v04-phase-1--step-4)。
不会安装依赖、import 第三方包或执行项目代码。


### 兼容性证据（v0.4 Phase 1 / Step 5）

针对已选定分发读取其自身 WHEEL tag；从用户已有安装日志的明确 wheel 文件名记录
来源与 tag。报告增加当前解释器的 ABI/platform 事实及有限 tag 匹配。
明确的符号导入失败可触发有界静态 Python 源码观察，不 import 或执行源码。
tag 匹配或看到定义都不能证明运行时/API 兼容性；未知格式和判断保持显式限制。
不生成升级/降级决策，也不关联跨证据根因。支持范围与限额见
[JSON 契约](docs/json-schema.md)。
