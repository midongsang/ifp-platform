# IFP · 意图形式化平台

> **中文** · [English](README.en.md)

业务规则通常散落在 PRD、聊天记录和代码注释里，真正发现矛盾时，往往已经上线了。
IFP 想把它们收拢成一个能写、能查、能验证的模型。

[![CI](https://github.com/midongsang/ifp-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/midongsang/ifp-platform/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-v0.1.0-blue.svg)](#项目状态)

仓库：[github.com/midongsang/ifp-platform](https://github.com/midongsang/ifp-platform) ·
包名 `ifp-platform` · 命令行 `ifp`

## 它是什么

IFP（Intention Formal Platform）是一套面向业务领域的形式化建模工具：用可读的 `.ifp`
语言描述实体、状态流转与约束，平台再把模型编码成 SMT 约束，自动搜索“前置条件成立、
却会打破业务约束”的反例。

它不替代业务代码，也不是线上规则引擎。要处理的问题很具体——规则冲突、边界漏洞、
资损与合规风险，以往多半要等事故复盘才暴露出来，IFP 希望把它们挡在合并之前。

## 能做什么

- **建模**：限界上下文、Intent、值对象、状态机、不变量、Pre/Post、跨域契约（[grammar.lark](src/ifp/grammar.lark)）
- **检查**：字段 / 枚举 / 类型安全与建模规范 Lint，资金统一用 `Decimal`（[typecheck.py](src/ifp/typecheck.py)、[lint.py](src/ifp/lint.py)）
- **验证**：SMT 反例搜索，从初始状态出发、按步数由短到长寻找打破不变量的最短路径（[smt.py](src/ifp/smt.py)）
- **仿真**：手写操作序列，覆盖正向、边界与非法操作拦截（[simulate.py](src/ifp/simulate.py)）
- **治理**：语义 Diff、基线变更风险评分与审批判定、Hash Chain 审计日志，以及角色访问控制 RBAC（[diff.py](src/ifp/diff.py)、[baseline.py](src/ifp/baseline.py)、[audit.py](src/ifp/audit.py)、[rbac.py](src/ifp/rbac.py)）
- **协作**：Git 兼容的模型仓库（多版本存取 + 业务变更关联）、基于业务语义的三方合并、模型度量与模型/代码版本对齐审计（[repo.py](src/ifp/repo.py)、[semantic_merge.py](src/ifp/semantic_merge.py)、[metrics.py](src/ifp/metrics.py)）
- **导出**：OpenAPI / Protobuf / Markdown / JSON，也可用插件注册自定义 Lint 与导出器（[exporter.py](src/ifp/exporter.py)、[plugin.py](src/ifp/plugin.py)）
- **上手与更新**：`ifp doctor` 环境自检，`ifp update` 从仓库发行版检查并按你的选择更新（展示发行时间与资产大小），`ifp config` 管理本地偏好（[updater.py](src/ifp/updater.py)、[doctor.py](src/ifp/doctor.py)、[prefs.py](src/ifp/prefs.py)）

## 快速开始

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ifp check examples/refund/                    # 类型检查 + Lint
ifp verify examples/refund/refund.ifp          # 属性测试：搜反例（示例会按预期返回非零）
ifp simulate examples/refund/                  # 场景仿真
ifp diff base/examples/refund examples/refund --fail-on-approval   # 语义 Diff / CI 门禁
ifp export examples/refund/refund.ifp --format openapi -o out/refund.openapi.json
ifp rbac                                        # 角色与权限矩阵
ifp metrics examples/refund/                    # 模型度量：规则覆盖率 / 死不变量 / 基线数量
ifp align examples/refund                       # 模型与代码版本对齐审计
ifp lsp                                         # 语言服务器（stdio，供编辑器诊断 / 跳转 / 快速修复）
ifp web                                         # Web 编辑器：浏览器内在线编辑与可视化
ifp doctor                                      # 环境自检：依赖、内置资源与工具链
ifp update --check                              # 检查仓库发行版是否有新版本（显示发行时间与大小）
```

`ifp diff` 接受文件或目录，`--json` 输出报告、`--github` 写作业输出、`--fail-on-approval`
在需强制审批时返回非零，可直接作为 PR 门禁。加上 `--actor` / `--actor-roles` 后会再走一遍
RBAC 判定：基线变更需要 `approver` 角色的 `approve_baseline` 权限，未获授权以退出码 3 阻断，
并把判定写入审计日志。

模型仓库与语义合并：

```bash
ifp repo init mymodels                           # 初始化（git 兼容）模型仓库
# 将 .ifp 源码放入 mymodels/ 后：
ifp repo commit mymodels mymodels/refund.ifp \
    -m "退款限额改为累计制" --business-change "退款限额由单笔改为累计" --code-revision a1b2c3d
ifp repo log mymodels                            # 查看历史提交与关联的业务变更
ifp merge base/refund.ifp ours.ifp theirs.ifp    # 业务语义三方合并（冲突则强制人工评审）
ifp align mymodels --code-revision a1b2c3d       # 模型与代码版本对齐审计
```

上手与更新：

```bash
ifp doctor                                      # 环境自检：快速确认依赖、资源与工具链
ifp update --check                              # 只检查：列出最新发行版、发行时间与资产大小
ifp update                                      # 交互确认后再更新；--yes 非交互，--json 供脚本消费
ifp update --auto                               # 开启 ifp version 时的自动检查；--no-auto 关闭
ifp config set update.source acme/ifp            # 换用你自己的发行仓库（默认 midongsang/ifp-platform）
```

`ifp update` 只做只读检查，展示发行时间与资产大小，确认后才下载安装；离线或网络受限时给出
可读提示而不会中断使用，也可用 `IFP_NO_UPDATE_CHECK=1` 彻底关闭检查。

## 一个退款场景

```text
context RefundContext {
    description: "电商退款限界上下文"

    intent RefundOrder {
        field orderPaidAmount: Decimal
        field totalRefundedAmount: Decimal = 0
        field status: RefundStatus = RefundStatus::Pending

        state RefundStatus { Pending, Approved, Refunded, Rejected }
        initial: RefundStatus::Pending

        // 核心资金不变量：累计退款不得超过订单实付
        invariant totalRefundNotExceedPaid {
            expr: totalRefundedAmount <= orderPaidAmount
            baseline: true
            risk: "累计退款超过订单实付金额，造成超额退款资金损失"
            owner: "finance-team"
        }

        action Audit {
            transition: RefundStatus::Approved
            pre: status == RefundStatus::Pending
            post: status == RefundStatus::Approved
        }

        action ExecuteRefund(amount: Decimal) {
            transition: RefundStatus::Refunded
            pre: status == RefundStatus::Approved && amount >= 0
            post: status == RefundStatus::Refunded
                  && totalRefundedAmount == old(totalRefundedAmount) + amount
        }
    }
}
```

`ExecuteRefund` 的前置条件只看了 `status` 和 `amount >= 0`，漏掉“本次退款不能让累计退款
超过订单实付”。这个洞不用人手工构造，`ifp verify` 两步就能找出来：

```text
不变量 totalRefundNotExceedPaid [BASELINE] 可被多步操作序列打破：
    步骤 1: Audit()
        动作前状态: orderPaidAmount=1, totalRefundedAmount=0, status=Pending
        动作后状态: orderPaidAmount=1, totalRefundedAmount=0, status=Approved
    步骤 2: ExecuteRefund(amount=2)
        动作前状态: orderPaidAmount=1, totalRefundedAmount=0, status=Approved
        动作后状态: orderPaidAmount=1, totalRefundedAmount=2, status=Refunded
    => 第 2 步执行后不变量 totalRefundNotExceedPaid 不成立
```

“审核 → 退款”之后，累计退款金额超过了订单实付——这是真实发生过的资损场景，IFP 在
编码之前就把它指了出来。

## 目录结构

```text
ifp-platform/
├── src/ifp/            # 平台核心（解析 / 检查 / 验证 / 治理 / 导出）
├── examples/refund/    # 退款域示例：模型 + 场景 + 属性测试配置
├── specs/              # IFP DSL 语法规范
├── docs/               # 建模规范、术语表、培训与 ROI 文档（中文）
├── docs/en/            # 上述文档的英文版
├── tests/              # pytest 测试
└── .github/workflows/  # CI 流水线
```

## 文档

英文版：

- [IFP DSL Specification](docs/en/dsl-spec.md)
- [IFP Modeling Standard](docs/en/modeling-standard.md)
- [IFP Glossary (English–Chinese)](docs/en/glossary.md)
- [Business Training Material](docs/en/training-business.md)
- [ROI Template](docs/en/roi-template.md)
- [Roadmap](docs/en/roadmap.md)

中文版：

- [IFP DSL 语法规范](docs/dsl-spec.md)
- [IFP 建模规范（开发 / 建模师版）](docs/modeling-standard.md)
- [IFP 术语表中英对照](docs/glossary.md)
- [业务侧培训材料](docs/training-business.md)
- [ROI 测算模板](docs/roi-template.md)
- [路线图](docs/roadmap.md)

## 项目状态

当前 `v0.1.0`（第一个版本），`解析 → 检查 → 验证 → 仿真 → Diff → 导出` 这条链路已经跑通，
退款域示例可端到端复现。

- **验证**：SMT 反例搜索支持值对象字段内联展开与多步路径搜索（带 `single` / `light` / `full`
  档位与超时降级）；语义 Diff 与基线审批判定接入流水线，在 PR 上自动运行并归档审计日志。
- **治理与协作**：区分建模提交 / 评审 / 审批 / 只读审计四类角色，基线变更要拿到
  `approve_baseline` 权限才能合并，判定结果写进审计日志；Git 兼容的模型仓库把多版本 `.ifp`
  源码与业务变更、代码版本关联起来（`ifp repo`）；语义合并按业务规则单元做三方比较，冲突
  定位到具体规则，无法自动消解时强制人工评审并留痕（`ifp merge`）；模型度量与模型 / 代码
  版本对齐审计分别由 `ifp metrics`、`ifp align` 提供。
- **编辑与可视化**：内置 LSP（`ifp lsp --stdio`）提供 `.ifp` 语法高亮、结构大纲、实时诊断
  （复用 typecheck / lint 诊断码）、跳转定义（`textDocument/definition`，支持 `State::Member`、
  字段类型、场景中的动作调用与 Intent，可跨文件定位）与快速修复（`textDocument/codeAction`，
  如为基线不变量补 `owner` / `risk`、为状态机补 `initial`、为未知字段类型生成值对象骨架）；
  VS Code 插件同步这些能力；Web 编辑器（`ifp web`）在浏览器内在线编辑、实时诊断、图形化
  查看状态机与不变量，并可直接触发属性测试。
- **上手与更新**：`ifp update` 直接读取来源仓库的发行版，展示版本、发行时间与资产大小，由
  用户自行决定是否更新（`--check` 只看不装、`--auto` 控制是否在 `ifp version` 时自动检查，
  `ifp config` 可切换更新来源），全程只依赖标准库并兼容离线与代理环境；推送 `v*` 标签即由 CI
  构建并发布 GitHub Release（附发行说明与 sdist / wheel 资产），来源仓库没有发行版本时更新
  检查回退到标签列表、发行版本缺少资产时回退为按标签安装；`ifp doctor` 一键自检 Python
  版本、依赖、内置资源与工具链。

后续方向见 [ROADMAP](docs/roadmap.md)。

### 版本号约定

版本号形如 `0.<默认迭代>.<修补>`：

- 首位固定为 `0`，表示正式发布；
- 第二位为**默认迭代**序号，随功能推进递增；
- 第三位为**修补**号，用于修复 bug 或一些不便对外说明的小问题。

`0.1.0` 即本项目的第一个版本。

## 贡献

欢迎提交 Issue 与 PR，动手前请先看 [CONTRIBUTING.md](CONTRIBUTING.md) 和[建模规范](docs/modeling-standard.md)。

## 许可

[Apache License 2.0](LICENSE)。
