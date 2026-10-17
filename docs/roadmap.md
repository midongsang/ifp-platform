# IFP 路线图（ROADMAP）

> 本路线图以 [README · 项目状态](../README.md#项目状态) 中列出的能力为基础展开，按里程碑组织。
> 每个里程碑给出**目标**与**验收标准**；不预设具体日期，交付以验收标准达成与否为准。
> 相关文档：[DSL 语法规范](dsl-spec.md) · [建模规范](modeling-standard.md) · [术语表](glossary.md)

当前版本：**v0.2.0**，`解析 → 检查 → 验证 → 仿真 → Diff → 导出` 这条链路已经跑通，退款域示例可端到端复现；v0.2.0 在第一个版本之上补齐了隔离沙箱与任务调度器。

---

## v0.1.0 — 参考实现（第一个版本，已完成）

**目标**：建立端到端可用的最小闭环，覆盖“业务规则建模 → 机器校验 → 治理 → 协作 → 导出”主流程，跑通退款域试点。

| 已完成能力 | 实现位置 |
| --- | --- |
| `.ifp` DSL 解析（语法 + AST + 多文件合并） | [`grammar.lark`](../src/ifp/grammar.lark) · [`parser.py`](../src/ifp/parser.py) · [`ast.py`](../src/ifp/ast.py) |
| 类型检查（IFP1001~IFP1034） | [`typecheck.py`](../src/ifp/typecheck.py) |
| 模型静态检查 Lint（IFP2001~IFP2009） | [`lint.py`](../src/ifp/lint.py) |
| 属性测试（反例搜索 + 一致性检查 IFP3001~IFP3010） | [`smt.py`](../src/ifp/smt.py) |
| 值对象字段在 SMT 中内联展开 | [`smt.py`](../src/ifp/smt.py) · [`typecheck.py`](../src/ifp/typecheck.py) |
| 多步路径反例搜索（`--steps` / `--mode` / `max_steps`，超时降级） | [`smt.py`](../src/ifp/smt.py) · [`cli.py`](../src/ifp/cli.py) · [`config.py`](../src/ifp/config.py) |
| 场景仿真（正向 / 边界 / 非法拦截） | [`simulate.py`](../src/ifp/simulate.py) |
| 语义 Diff（目录匹配 / `--json` / `--github` / `--threshold` / `--fail-on-approval`） | [`diff.py`](../src/ifp/diff.py) · [`cli.py`](../src/ifp/cli.py) |
| 基线治理与风险评分 | [`baseline.py`](../src/ifp/baseline.py) |
| 审计日志（Hash Chain） | [`audit.py`](../src/ifp/audit.py) |
| 角色访问控制 RBAC（四类角色 / 五个权限点） | [`rbac.py`](../src/ifp/rbac.py) |
| 模型仓库（多版本 `.ifp` 存取 + 业务变更关联 + 对齐审计） | [`repo.py`](../src/ifp/repo.py) |
| 基于业务语义的三方合并 | [`semantic_merge.py`](../src/ifp/semantic_merge.py) |
| 模型度量（规则覆盖率 / 死不变量 / 基线数量） | [`metrics.py`](../src/ifp/metrics.py) |
| 内置 LSP（诊断 / 结构大纲 / 跳转定义 / 快速修复） | [`lsp.py`](../src/ifp/lsp.py) |
| VS Code 插件（语法高亮 / 诊断 / 跳转 / 快速修复 / 一键 verify·simulate） | [`editors/vscode/`](../editors/vscode) |
| Web 编辑器（在线编辑 / 可视化状态机与不变量 / 属性测试） | [`web.py`](../src/ifp/web.py) · [`webassets/`](../src/ifp/webassets) |
| 自更新（读取来源仓库发行版，展示版本 / 发行时间 / 资产大小，来源可配置） | [`updater.py`](../src/ifp/updater.py) · [`prefs.py`](../src/ifp/prefs.py) |
| 环境自检（Python 版本 / 依赖 / 内置资源 / 工具链） | [`doctor.py`](../src/ifp/doctor.py) |
| 打标签自动发布发行版（校验版本一致 + sdist / wheel 资产） | [`.github/workflows/release.yml`](../.github/workflows/release.yml) |
| CI 语义门禁 | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) |
| 制品导出（JSON / OpenAPI / Protobuf / Markdown） | [`exporter.py`](../src/ifp/exporter.py) |
| 插件 SDK（自定义 Lint / 导出器） | [`plugin.py`](../src/ifp/plugin.py) |
| CLI（check / verify / simulate / diff / baseline / export / init / rbac / repo / merge / metrics / align / lsp / web / doctor / update / config / version） | [`cli.py`](../src/ifp/cli.py) |

**验收标准**：`ifp check` / `ifp simulate` 对退款域示例全部通过；`ifp verify` 按预期发现超额退款反例并以非零码退出；上表每项能力均有对应单元测试（见 [`tests/`](../tests)）；CI 示例（见 [`ci.yml`](../.github/workflows/ci.yml)）全绿。

> 说明：`ifp diff` / `ifp merge` / `ifp align` / `ifp update` / `ifp doctor` 的退出码语义见对应测试文件；更新与发布全程只依赖标准库，并自动遵循 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`。

### 版本号约定

版本号形如 `0.<默认迭代>.<修补>`：

- 首位固定为 `0`，表示正式发布；
- 第二位为**默认迭代**序号，随功能推进递增；
- 第三位为**修补**号，用于修复 bug 或一些不便对外说明的小问题。

当前 `0.1.0` 即第一个版本。

---

## v0.2.0 — 隔离沙箱与任务调度器（第二个版本，已完成）

**目标**：把校验与仿真从「一次跑一个」推进到「受控并发、隔离执行」，让一批模型能在受限制的沙箱里稳定跑完，并给出可脚本消费的逐任务报告。

| 已完成能力 | 实现位置 |
| --- | --- |
| 隔离沙箱（独立临时目录 / POSIX 资源上限 / 墙钟超时 / 进程组终止 / 令牌剥离 / 输出截断） | [`sandbox.py`](../src/ifp/sandbox.py) |
| 任务调度器（线程池并发 / 保序返回 / `fail-fast` 跳过 / 汇总统计） | [`scheduler.py`](../src/ifp/scheduler.py) |
| `ifp run` 子命令（check / verify / simulate 拆任务；`--job` / `-j` / `--timeout` / `--cpu` / `--memory` / `--fail-fast` / `--json`） | [`cli.py`](../src/ifp/cli.py) |
| `ifp verify` / `ifp simulate` 的 `--sandbox` 单跑 | [`cli.py`](../src/ifp/cli.py) |
| 沙箱默认参数偏好（`sandbox.jobs` / `sandbox.timeout` / `sandbox.cpu` / `sandbox.memory`） | [`prefs.py`](../src/ifp/prefs.py) |
| 环境自检覆盖沙箱可用性 | [`doctor.py`](../src/ifp/doctor.py) |

任务粒度：`check` 按输入路径（目录作为一个整体检查，保证跨文件引用可解析），`verify` 按「文件 × Intent」，`simulate` 按场景。每个任务返回 `ok` / `failed` / `timeout` / `blocked` / `skipped` 五种状态之一，超时、非零退出、无法启动都会映射为明确状态，绝不中断整批任务。

**验收标准**：`ifp run` 对退款域示例的 check / verify / simulate 三类任务均能并发调度并逐任务报告状态与耗时；失败任务不会带走整批报告，`--fail-fast` 在首个失败后把剩余任务标记为 `skipped`；`ifp doctor` 报告沙箱可用性；新增能力均有对应单元测试（见 [`tests/test_sandbox.py`](../tests/test_sandbox.py) · [`tests/test_scheduler.py`](../tests/test_scheduler.py) · [`tests/test_run_cli.py`](../tests/test_run_cli.py)）。

---

## 后续方向

以下能力属于平台规划，当前参考实现尚未提供（详见[术语表](glossary.md)）：事件与事件回放、API 密钥、工作流。

---

## 交付原则

- 各里程碑按**验收标准**判定完成，不预设具体时间点。
- 任一新增能力都必须与现有诊断码体系、审计日志与建模规范保持一致。
- 细粒度任务以仓库 issue 为准；本文件仅描述方向与验收标准。
