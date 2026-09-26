# RepoRescue

[English](README.md) | [简体中文](README.zh-CN.md)

面向故障开发项目的证据驱动诊断与恢复规划工具。

RepoRescue 帮助开发者理解 Python 项目为什么跑不起来：有哪些证据、根因链说明了什么，以及可以如何修复。
它提供修复方案预览和验证计划，便于你在实际操作前审查改动，并明确修复后该如何检查。

**v0.3.0-alpha.1 · Windows First · Python only · READ ONLY by default · MIT**

详见 [v0.3 alpha 发布说明](docs/releases/v0.3.0-alpha.1.md)。

默认采用安全策略。执行项目代码需要显式确认。
RepoRescue 不会自动执行修复或安装软件包。

## 演示

![RepoRescue 诊断 Python 模块导入失败](docs/assets/repo-rescue-demo.gif)

使用已安装的 CLI，诊断一个结果确定的模块导入失败样例。

## 为什么是 RepoRescue

Traceback、运行时错误和环境错误提供了有用的故障线索。
RepoRescue 将当前能够识别的证据整理成便于审查的流程：

证据（Evidence）→ 诊断 → 根因链 → 修复预览 → 验证计划。

结论以实际观察到的证据为限。根因链可能解释了故障的后果，却尚未确定更深层的原因；
报告没有发现问题，也不代表整个项目可以正常运行。

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

## 快速演示

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

## 当前能力

- 浅层 Python 项目检测：扫描根目录和直接子目录，最多 1,000 个条目。
  `likely` / `unknown` 表示文件名证据，不表示应用已经能够正常启动。
- 检查当前解释器，并受控执行 `--version`。
- 根据根目录 `pyproject.toml` 的 `[project].requires-python` 诊断 Python 版本兼容性。
  支持正式版本比较的保守子集，而非完整 PEP 440：`>=`、`>`、`<=`、`<`、`==`、`!=`、逗号组合及
  `major.minor[.patch]`，相等或排除约束还支持末尾 `.*`。
  `~=`、预发布版本等不支持的约束会作为工具限制说明，不猜测版本不兼容。
- 检测项目本地 `.venv` / `venv` 并比较解释器身份：仅检查 `Scripts/python.exe` 和 `bin/python` 是否存在，
  不运行候选解释器。当前解释器与候选不同会产生 WARNING；多个候选存在歧义时不会自动选择。
- 从已捕获输出识别明确的 `ModuleNotFoundError: No module named ...`，以及有限形式的
  `ImportError: cannot import name ... from ...`。不会把导入名映射为 PyPI 分发包名，也不据此断言缺少软件包。
- 显式确认后受控探测根目录 `main.py`，诊断非零退出、记录超时观察，并限制 stdout/stderr 的捕获大小。
- 与证据关联的根因链、修复方案预览，以及计划中的验证步骤。
- 便于人阅读的终端报告和简化的已知路径展示；机器可读的 JSON 仍保留诊断与自动化需要的真实路径。

当前范围为 Windows First、Python only。GitHub Actions 已验证发布前基线在 Windows + Python 3.12
及 Windows + Python 3.13 下通过；本地验证使用 Python 3.13.1。Linux/macOS 尚未在此 alpha 阶段单独验证。

## 当前支持的诊断

| 问题或操作 | 当前支持情况 |
| --- | --- |
| Python 版本不匹配 | 支持已说明的版本约束子集 |
| 项目本地解释器不匹配 | 支持，等级为 WARNING；不能证明项目有故障 |
| `ModuleNotFoundError` | 支持明确的缺失模块异常行 |
| `ImportError` 符号导入失败 | 有限支持，识别明确的 cannot-import-name 异常行 |
| 启动非零退出 | 支持，作为 ERROR 症状；具体导入诊断保持独立 |
| 启动超时 | 支持，作为 INFO 观察；不能证明卡死或已就绪 |
| 自动修复 / 安装软件包 | 未实现 |
| 执行验证 / 回滚 | 未实现 |

## 安全设计

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

## 故障样例

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

## 路线图

以下均为未来规划，此 alpha 版本尚未实现，也不承诺交付时间：

- 规划中：深入 Python 诊断。
- 规划中：Node / Java / C++ / Docker 支持。
- 规划中：安全修复执行器（Safe Repair Executor）。
- 规划中：执行验证与回滚。
- 规划中：RepoRescue Desktop，为不熟悉命令行的用户提供 GUI / EXE。
- 规划中：Agent 集成。

当前工具没有 GUI、其他语言支持，也不依赖 LLM。

## 开发

从仓库根目录运行：

```powershell
python -m unittest discover
```

测试不需要安装项目或手工设置 `PYTHONPATH`。默认工作流测试不执行项目代码；
启动测试会显式确认运行经过审查的小型样例，并检查样例文件与修改时间保持不变。

CLI 协调扫描与检测、检查与命令提议、受控执行、基于证据的诊断，以及终端和 JSON 报告。
[原始架构](docs/architecture.md) 是历史 v0.1 设计；[docs](docs/) 还包含 JSON 契约和发布审查说明。

实验性的 [Extension SDK foundation](docs/extension-sdk.md) 提供公共 SDK、Pack model、显式 `PackRegistry`、
受控的已安装软件包 discovery API 及[官方示例 Pack](examples/extensions/example_language_pack.py)。
Extension API v1 仍为实验性。当前运行时仅使用 built-in Pack，唯一真实语言 Pack 为 `python.core`；
宿主可显式发现已安装 Pack 的 entry points；CLI 尚未自动启用 discovery，
没有 marketplace 或 installer。
加载 entry point 与调用 factory 会执行第三方 Python 代码，只应加载可信来源的 Pack。
SDK 验证不是 sandbox，也不授予 Core 执行权限。

[GIF 生成脚本](tools/generate_demo_gif.py) 使用已安装的项目 CLI，并将 Pillow 作为本地文档工具。
Pillow 不是运行时依赖；缺少 Pillow 时只显示明确提示，不会自动安装。

## 参与贡献

请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。保持改动聚焦，验证其行为，并提供结果证据。
运行时依赖仍为空。源码仓库：[furX7/RepoRescue](https://github.com/furX7/RepoRescue)。

## License

[MIT](LICENSE)。Copyright (c) 2026 RepoRescue contributors.
