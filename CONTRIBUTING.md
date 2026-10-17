# 贡献指南（Contributing）

感谢你考虑为 **IFP · 意图形式化平台**（`ifp-platform`）做贡献。

## 开发环境

```bash
git clone https://github.com/midongsang/ifp-platform.git
cd ifp-platform
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

- 需要 Python **3.11+**。
- 依赖：`lark`（解析）、`z3-solver`（SMT 求解）、`typer` + `rich`（CLI）、`pyyaml`（配置）。

## 目录结构

```text
src/ifp/         平台核心（解析 / 类型检查 / Lint / SMT / 仿真 / Diff / 基线 / 审计 / 导出 / 插件）
specs/           IFP DSL 语法规范（EBNF）
examples/        示例模型（refund 退款域）
docs/            建模规范、术语表、培训与 ROI 文档
tests/           pytest 测试
```

- 语法权威定义在 [src/ifp/grammar.lark](src/ifp/grammar.lark)。修改语法后，必须同步更新
  [specs/ifp-dsl.ebnf](specs/ifp-dsl.ebnf) 与 [docs/dsl-spec.md](docs/dsl-spec.md)。

## 提交前自检

```bash
ruff check src tests      # 静态检查
pytest                    # 单元测试
ifp check examples/refund/ && ifp simulate examples/refund/
python -m build           # 打包 sdist + wheel（CI 会校验 wheel 内含 py.typed 与 DSL 资源）
```

CI（[.github/workflows/ci.yml](.github/workflows/ci.yml)）会对 push 与 PR 运行以上检查：`test` 作业在
Python 3.11 / 3.12 / 3.13 上跑 lint 与单测，`example` 作业跑退款域端到端，`build` 作业构建并冒烟验证
wheel；PR 还会运行**语义门禁**：对模型变更执行 `ifp diff`，输出风险分值与审批判定，涉及基线变更或
风险超阈值时阻断合并并归档审计日志。

本地可加 `--actor` / `--actor-roles` 复现 RBAC 判定（如 `ifp diff old new --actor alice --actor-roles modeler`，
基线变更会因缺少 `approve_baseline` 权限以退出码 3 阻断），角色与权限矩阵见 `ifp rbac`。

## 代码约定

- 全部公开 API 与诊断信息使用中文；错误码形如 `IFP####`（见 [建模规范](docs/modeling-standard.md)）。
- 公开 API 带类型标注，包内随附 `py.typed`（PEP 561），下游可直接获得类型提示。
- **资金一律使用 `Decimal`** 建模，禁止浮点；SMT 编码中金额按实数精确编码。
- 新增诊断码时，须在文档中登记并说明触发条件。
- 保持改动最小、聚焦；不要引入与任务无关的重构。

## 提交 Pull Request

1. 从 `main` 切出新分支（如 `feat/multi-step-counterexample`）。
2. 保证 `ruff` 与 `pytest` 通过；如新增功能，请补充对应测试。
3. 按仓库既有风格编写提交信息（中文，说明“为什么”而非仅“做了什么”）。
4. 涉及 **基线不变量** 的变更（`.ifp` 中 `baseline: true`）需在 PR 描述中说明业务影响与审批情况；语义门禁会把此类变更判定为“需强制审批”并阻断合并。

## 报告问题

请在 Issues 中附上：

- 复现命令与最小 `.ifp` 片段；
- `ifp version` 输出；
- 期望行为与实际问题（含完整报错）。

## 许可

贡献即表示你同意以 [Apache License 2.0](LICENSE) 授权你的贡献。
