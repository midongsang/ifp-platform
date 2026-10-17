# IFP 建模规范（开发 / 建模师版）

> 目标：统一建模风格、降低 SMT 求解开销、保证模型可读性、保障模型与业务对齐。
> 适用范围：`.ifp` 模型文件编写、模型评审、静态 Lint / 类型检查、仿真与属性测试配置。
> 面向读者：建模工程师、后端开发、平台维护与评审人员。
>
> 相关文档：[DSL 语法规范](dsl-spec.md) · [术语表](glossary.md) · [业务侧培训材料](training-business.md)

---

## 1. 是否建模：前置判断

建模前先评估，不盲目建模：

| 结论 | 典型场景 |
| --- | --- |
| ✅ 适合建模 | 资金结算、退款、计费、风控、合规审批等高风险业务；规则复杂、存在多场景叠加；一旦出错产生资损 / 合规处罚 |
| ❌ 不建议建模 | 简单 CRUD、一次性活动、低风险业务；建模维护成本高于收益 |

> 前置输出物：业务域边界文档，明确限界上下文范围，识别外部依赖（其他域的 `Contract`）。

---

## 2. 限界上下文规范

1. 一个限界上下文对应一个独立业务域，不要跨大业务模块合并。
2. 上下文内部高内聚；跨域交互只能通过 `Contract`，**禁止直接引用其他域 Intent 的内部属性**。
3. 命名：大驼峰，业务语义清晰，如 `RefundContext`、`SettlementContext`；禁止无意义缩写。
4. 每个 `context` 首行写 `description`（或注释），说明域职责、外部依赖、风险等级。
5. 上下文名建议以 `Context` 结尾，否则 Lint 报 [`IFP2006`](#62-lint-规则码ifp2001ifp2009)（warning）。

```text
context RefundContext {
    description: "电商退款限界上下文，负责退款单生命周期与累计退款资金约束"
    // ...
}
```

---

## 3. Intent（聚合根）建模规范

1. 一个 Intent 代表一个聚合，遵循 DDD 聚合设计原则：**聚合根负责维护自身所有不变量**。
2. 聚合内部实体、值对象由聚合根统一管理；外部只能通过聚合根操作，不能直接修改内部子对象。
3. 命名：大驼峰、业务实体名，如 `RefundOrder`。
4. 状态机必须在 Intent 内定义，状态枚举命名语义化，禁止 `Status1`/`Status2` 这类无意义命名。
5. 操作（Action）：聚合上允许的业务动作；每个 Action 都应定义 `pre`、`post`。
   - `pre`：执行前必须成立；不成立代表动作非法，不允许执行。
   - `post`：动作成功执行后必须保证的结果。
6. 不变量写在 Intent 顶层，**只有“聚合在任意合法状态下都必须永久成立”的约束**才写成 `invariant`。
   - 资金总额、限额、数量上限这类硬性约束优先标记 `baseline`。
   - 业务临时特例、可人工审批豁免的规则**不能写进 `invariant`**，走“规则豁免（Rule Exception）”流程。
7. 禁止：在不变量里写临时业务开关、可动态配置的策略（策略类放到 `Contract` 或字段，而非硬编码在 `invariant`）。

> 约束关联：一个 Intent 若**没有任何不变量**，Lint 报 [`IFP2005`](#62-lint-规则码ifp2001ifp2009)（warning）。

---

## 4. 值对象 ValueObject 规范

1. 值对象不可变、无独立 ID，生命周期依附于聚合根。
2. 适合：金额、地址、明细条目、费率、证件信息等。
3. 值对象内部可以定义自身不变量（例如金额 `>= 0`）。
4. 命名：大驼峰。
5. 值对象**禁止定义状态机与业务 Action**（DSL 语法层面即不支持，见 [DSL 规范 · value](dsl-spec.md#3-值对象value)）。

```text
value Money {
    description: "金额值对象，资金一律使用 Decimal，禁止浮点"
    field amount: Decimal
    field currency: String
    invariant nonNegative {
        expr: amount >= 0
        note: "金额不允许为负数，资金基础约束"
        owner: "finance-team"
    }
}
```

### 4.1 在聚合中使用值对象

1. Intent 字段可直接声明为值对象类型，例如 `field balance: Money`。
2. 引用其成员用**成员访问**：`balance.amount`（DSL 规范 [§12.7](dsl-spec.md#127-值对象成员访问)）。
3. 值对象自身的不变量会**自动内联**到聚合上（`amount >= 0` → `balance.amount >= 0`），建模时**无需**手工把成员约束抄进 Intent。
4. 值对象内的 `String` 成员（如 `Money.currency`）不参与 SMT 数值求解，但不影响该 Intent 的其余字段验证。

```text
intent Wallet {
    field balance: Money              // 值对象字段

    invariant balanceWithinLimit {
        expr: balance.amount <= 1000  // 直接引用成员
        note: "单账户余额上限"
        owner: "finance-team"
    }

    action Deposit(amount: Decimal) {
        pre: amount >= 0
        post: balance.amount == old(balance.amount) + amount   // 对成员赋值
    }
}
```

> 值对象内联展开后，其不变量在反例中以 `balance.nonNegative` 这类**带前缀的名字**出现，便于定位是哪个字段实例被打破。

---

## 5. 表达式与 DSL 编码规范

### 5.1 命名

| 对象 | 风格 | 示例 |
| --- | --- | --- |
| Intent / ValueObject / Contract | 大驼峰 | `OrderAmount` |
| 属性 / 字段 / 局部变量 | 小驼峰 | `totalRefundAmount` |
| 状态枚举 | 大驼峰，枚举值大写 | `RefundStatus::AuditPass` |

### 5.2 类型

1. **资金类型统一使用 `Decimal`，禁止浮点数**（浮点精度问题会导致 SMT 求解误报）。
2. 布尔、整数、字符串按需使用；字符串尽量用枚举替代自由文本。
3. 类型必须显式声明，不使用隐式类型推断。

### 5.3 表达式写法

1. 表达式拆分，一行一个约束，不要把大量逻辑写在同一行，便于阅读与语义 Diff。
2. 用 `&&`、`||` 组合条件，并加括号明确优先级。
3. 避免过深嵌套布尔表达式；复杂条件拆成中间布尔属性。
4. 复杂业务约束必须写注释，说明业务来源，方便业务评审。

### 5.4 性能约束（SMT 求解优化重点）

1. 避免超大范围无界整数，尽量给出合理上下界，减少求解器搜索空间（对应 VerifyConfig 的 `lower` / `upper`）。
2. 避免多层嵌套、大量集合全量遍历，会显著增加属性测试耗时。
3. 集合大小设置合理上限，仿真/属性测试时可配置边界。
4. 模型出现求解超时（[`IFP3010`](#6-诊断码总览)）：简化不变量、拆分聚合、减少组合状态空间。

---

## 6. 诊断码总览

IFP 平台的全部诊断码分五类：**语法解析（IFP0001）**、**类型检查（IFP1xxx）**、**模型静态检查 Lint（IFP2xxx）**、**验证/一致性（IFP3xxx）**，以及 CLI 运行期错误。所有诊断包含 `文件:行` 与严重级别（error / warning / info）。

IFP0001 表示 `.ifp` 源码存在词法/语法错误，无法构造 AST：CLI 在任何检查执行前即报错终止，编辑器则由语言服务器 `ifp lsp` 作为诊断内联展示（见 [§13.3](#133-编辑器诊断lsp)）。

### 6.1 类型检查码（IFP1001~IFP1034）

由 [`typecheck.py`](../src/ifp/typecheck.py) 产生，关注“类型与引用是否合法”。大类如下：

| 码 | 含义 | 级别 |
| --- | --- | --- |
| IFP1001 | 引用了未定义的标识符 | error |
| IFP1002 | 表达式内出现函数调用（调用只允许出现在场景 `when`/`rejected`） | error |
| IFP1003 | 不变量表达式不是布尔类型 | error |
| IFP1004 | 动作 `pre`/`post` 不是布尔表达式 | error |
| IFP1005 | 引用未定义的状态机 | error |
| IFP1006 | 状态机中不存在该状态 | error |
| IFP1007 | `old(...)` 出现在 `post` 之外 | error |
| IFP1008 | 算术运算类型不匹配（非数值操作数） | error |
| IFP1009 | 比较运算类型不匹配 | error |
| IFP1010 | 逻辑运算操作数不是布尔 | error |
| IFP1011 | 字段类型未定义（应为状态机或值对象） | error |
| IFP1012 | 字段重复定义 | error |
| IFP1023 | 值对象成员访问非法（成员不存在，或根不是值对象） | error |
| IFP1020 | 动作缺少前置条件 `pre` | warning |
| IFP1021 | 动作缺少后置条件 `post` | warning |
| IFP1022 | 契约 `expose` 暴露了未定义字段 | error |
| IFP1030 | 场景缺少 `intent` | error |
| IFP1031 | 场景引用了未定义的 intent | error |
| IFP1032 | 场景 `given` 引用了未定义字段 | error |
| IFP1033 | 场景调用了未定义动作 | error |
| IFP1034 | 场景调用动作参数不匹配 | error |

### 6.2 Lint 规则码（IFP2001~IFP2009）

由 [`lint.py`](../src/ifp/lint.py) 产生，关注“是否符合建模规范与最佳实践”。

| 码 | 规则 | 级别 | 处置建议 |
| --- | --- | --- | --- |
| IFP2001 | 基线不变量缺少 `owner`（业务负责人） | **error** | 为基线不变量补充 `owner: "<负责人>"` |
| IFP2002 | 基线不变量缺少 `risk`（风险描述） | warning | 补充 `risk`，说明打破该约束的业务后果 |
| IFP2003 | 不可达状态：某状态既非初始、也无动作 `transition` 到达 | warning | 补充动作或删除冗余状态 |
| IFP2004 | 死不变量：恒真表达式或与其他不变量表达式完全重复 | warning | 清理冗余/恒真不变量 |
| IFP2005 | Intent 未定义任何不变量 | warning | 补充核心业务约束；确无约束的简单聚合可不建模 |
| IFP2006 | 上下文命名建议以 `Context` 结尾 | warning | 改名符合命名规范 |
| IFP2007 | 场景缺少 `rejected` 步骤（未覆盖非法操作拦截） | **info** | 补充非法操作拦截场景 |
| IFP2008 | 定义了状态机但缺少 `initial` | warning | 补充 `initial: <状态机>::<状态值>` |
| IFP2009 | 状态机没有任何状态值 | **error** | 至少声明一个状态值 |

> “死不变量”当前实现检测两类：表达式 `render` 后**完全重复**，以及**恒真比较**（两侧相同，如 `x <= x`）与布尔字面量 `true`。

### 6.3 验证 / 一致性码（IFP3xxx）

由 [`smt.py`](../src/ifp/smt.py) 产生：

| 码 | 含义 | 级别 |
| --- | --- | --- |
| IFP3001 | 不变量集合互相矛盾，不存在任何合法状态（规则矛盾） | error |
| IFP3002 | 动作前置条件与不变量冲突，永远不会被触发（动作不可触发） | error |
| IFP3003 | Intent 无法进行 SMT 编码 / 一致性检查（如含 String 字段、值对象循环引用） | warning |
| IFP3010 | 属性测试求解超时（超过 `timeout_ms`） | warning |

> 反例（counterexample）本身不是 Diagnostic，而是 `VerifyReport.counterexamples` 中的独立对象，含基线标记。
>
> 说明：值对象字段（如 `Money`）会被**内联展开**参与求解，不再触发 IFP3003；仅当值对象含循环引用、或出现无法编码的顶层 `String` 字段时才会报该码。

---

## 7. 基线 Baseline 标记规范

1. **仅核心资金、合规类不变量**可标记 `baseline`。
2. 标记基线的 `invariant` 修改，会**自动触发强制审批**流程（见 [§11 语义 Diff 与变更风险](#11-语义-diff-与变更风险)）。
3. 基线注释必须写明风险描述（`risk`）与业务负责人（`owner`）；缺 `owner` 直接判为 error（IFP2001）。
4. **禁止滥用基线**：普通非核心规则不打 `baseline`，否则每次微小修改都要走重审批，拖累迭代。

```text
invariant totalRefundNotExceedPaid {
    expr: totalRefundedAmount <= orderPaidAmount
    baseline: true
    risk: "累计退款超过订单实付金额，造成超额退款资金损失"
    owner: "finance-team"
    note: "基线不变量，修改需单独审批"
}
```

---

## 8. 跨域契约 Contract 规范

1. `Contract` 为对外**最小接口契约**，只暴露外部域需要的属性与操作，隐藏内部实现细节。
2. 跨域调用方依赖 `Contract`，不直接依赖对方 Intent。
3. `Contract` 变更会触发依赖方模型重校验；契约版本化管理。
4. 契约中应声明前置/后置约束，保证跨域交互合法性。
5. `expose` 只能暴露上下文中**已定义**的字段，否则报 IFP1022。

---

## 9. 仿真场景 Scenario 编写规范

1. `Scenario` 用于正向、常规异常流程验证，人工构造业务操作序列。
2. 每个场景写注释：场景目的、业务来源。
3. 覆盖三类流程：**正常流程、边界正向流程、非法操作拦截（`rejected`）**。
4. `Scenario` 不用于大规模边界挖掘；大规模边界交给 `PropertyTest`。
5. 至少包含一个 `rejected` 步骤，否则 Lint 报 IFP2007（info）。

```text
scenario AuditRejectedWhenNotPending {
    context: RefundContext
    intent: RefundOrder
    given {
        orderPaidAmount = 100
        totalRefundedAmount = 0
        status = RefundStatus::Approved
    }
    rejected Audit()   // 非待审核状态下发起审核，必须被拦截
}
```

---

## 10. 属性测试 PropertyTest 配置规范

1. 属性测试自动搜索反例，验证 `Invariant` 在任意合法操作序列下始终成立。
2. 搜索**从 Intent 初始状态出发**（`initial` 声明 + 字段默认值确定起点），按“动作步数由短到长”寻找**打破不变量**的最短操作序列；反例给出完整动作序列及每步前后状态。
3. 区分轻量 / 全量模式：CI 流水线只跑轻量；本地或夜间任务跑全量。
4. 设置资源限制：`timeout_ms`、数值上下界（`lower`/`upper`），防止求解器长时间占用资源。
5. 发现反例后，建模师第一步先区分三类情况：
   - ① 真实业务漏洞 → 修改业务规则 / 模型；
   - ② 模型表达错误（DSL 写错）→ 修复模型表达式；
   - ③ 求解器误报 / 约束过紧 → 调整模型边界。
6. 所有反例必须留存记录，写入评审记录。

配置样例（[`examples/refund/property_test.yaml`](../examples/refund/property_test.yaml)）：

```yaml
property_test:
  intent: RefundOrder      # 只验证该 Intent；留空则验证全部
  lower: 0                 # 数值变量下界
  upper: 100000            # 数值变量上界（避免无界整数导致求解耗时）
  timeout_ms: 5000         # 单次求解超时（毫秒）
  mode: light              # 步数档位：single(1) | light(2) | full(3)
  max_steps: 2             # 显式覆盖最大步数（优先级高于 mode）
  invariants: []           # 只验证指定不变量；留空表示全部
```

### 10.1 步数档位与超时降级

| 档位 | 最大动作步数 | 适用场景 |
| --- | --- | --- |
| `single` | 1 | 快速回归，只查“一个动作即打破不变量”的浅层漏洞 |
| `light` | 2 | CI 默认；可覆盖需“先置前置状态、再触发”的常见漏洞（如先审核后退款） |
| `full` | 3 | 本地 / 夜间任务；更深的可达路径，耗时更高 |

- `max_steps` 若显式配置，则覆盖 `mode` 档位（取 `max(1, max_steps)`）。
- 步数越大，需枚举的动作序列越多，求解耗时增长较快；建议 CI 用 `light`，夜间任务用 `full`。
- **超时降级**：单次求解超过 `timeout_ms` 时记为 [`IFP3010`](#6-诊断码总览)（warning）并跳过该目标，**不抛异常、不阻塞流水线**；CI 只需关注 error 级诊断与实际反例。

---

## 11. 语义 Diff 与变更风险

由 [`diff.py`](../src/ifp/diff.py) 与 [`baseline.py`](../src/ifp/baseline.py) 提供。语义 Diff 比较的是**业务规则结构**（上下文、Intent、字段、状态机、不变量、动作、契约、场景），而非源码字符。

### 11.1 风险评分规则

对每处变更按类别加权累计，封顶 100：

| 变更类别 | 基础权重 | 附加规则 |
| --- | --- | --- |
| context（上下文新增/删除） | 25 | `removed` 再 +10 |
| invariant（不变量） | 25 | 基线不变量变更再 +40 |
| contract（契约） | 20 | `removed` 再 +10 |
| action（动作，含 `pre`/`post`） | 15 | `removed` 再 +10 |
| state（状态机） | 15 | `removed` 再 +10 |
| field（字段） | 10 | `removed` 再 +10 |
| scenario（场景） | 5 | `removed` 再 +10 |
| description（描述） | 0 | — |
| 其他 | 5 | — |

- 变更项若带**基线标记**（仅不变量可能携带）会使该项 +40。
- **删除类变更**（`removed`）额外 +10。

### 11.2 强制审批判定

满足以下之一即需强制审批（CLI 输出“需强制审批：是”）：

1. 存在**基线变更**（`has_baseline_change`）；或
2. **风险分值 ≥ 40**（`APPROVAL_THRESHOLD`）。

因此，任何涉及基线不变量的修改、或累积风险较高的变更，都必须人工审批并存档。

---

## 12. 模型评审流程

模型评审分两轮：**业务评审** + **技术建模评审**。

1. **业务评审**：业务/产品确认不变量、状态、业务逻辑是否符合业务意图（详见[业务侧培训 · 参与模型评审](training-business.md#10-怎么参与模型评审)）。
2. **技术建模评审**（建模师/开发）：
   - 聚合、限界上下文划分是否合理；
   - DSL 表达式正确性、类型安全；
   - SMT 求解性能，是否存在求解超时风险；
   - `baseline` 标记是否合理，有无滥用；
   - `Scenario`、`PropertyTest` 覆盖度；
   - 模型注释完整性。
3. 用**语义 Diff** 查看本次变更影响范围；基线变更自动升级评审级别。

---

## 13. CI 流水线集成规范

参考 [`ci.yml`](../.github/workflows/ci.yml) 的示例：

1. 模型提交自动执行：类型检查 + Lint + 轻量属性测试 + 语义 Diff。
2. 校验失败禁止合并进模型仓库。
3. 制品导出：模型通过后自动导出 OpenAPI / Protobuf / Markdown 报表等制品。
4. 模型版本与代码版本建立关联，便于追溯，保证模型与代码对齐。

### 13.1 语义门禁（PR 模型变更风险）

PR 阶段自动比较 base 分支与 PR 的模型，把第 11 节的**风险评分**与**审批判定**作为合并门禁：

```bash
# base 分支模型检出到 base/，与当前模型做语义 Diff
ifp diff base/examples/refund examples/refund \
  --github \
  --json out/semantic-gate.json \
  --audit out/audit.jsonl
```

- `ifp diff` 接受**文件或目录**；目录按相对路径逐文件匹配，变更会标注所属模型文件。
- `--json` 输出机器可读报告（风险分值、阈值、`approval_required`、变更清单），供流水线读取与归档。
- `--github` 输出 GitHub Actions Annotations，并把 `risk_score` / `approval_required` 写入作业输出（`$GITHUB_OUTPUT`）。
- `--threshold` 可覆盖默认审批阈值 40；`--fail-on-approval` 在“需强制审批”时以非零码退出，用于**硬门禁**阻断合并。
- 退出码约定：`0`=无需审批；`1`=需强制审批（仅 `--fail-on-approval` 时）；`2`=路径或用法错误；`3`=主体权限不足（指定 `--actor` 时，见 13.2）。
- 审计日志（JSONL）与门禁报告随流水线以 artifact 归档，纳入第 6 节审计取证范围。

CI 中跑通端到端的命令序列：

```bash
ifp check examples/refund/          # 类型检查 + Lint
ifp simulate examples/refund/       # 场景仿真
ifp verify examples/refund/refund.ifp   # 属性测试（退款域示例会按预期发现反例，返回非零）
ifp export examples/refund/refund.ifp --format openapi -o out/refund.openapi.json
ifp diff base/examples/refund examples/refund --fail-on-approval  # 语义门禁
```

### 13.2 权限门禁（RBAC）

13.1 的审批判定回答“要不要审批”，RBAC 回答“谁有权批”。平台区分四类角色，权限互不重叠：

| 角色 | 中文 | 权限 |
| --- | --- | --- |
| `modeler` | 建模提交 | `submit_model` |
| `reviewer` | 评审 | `review_model` |
| `approver` | 审批（含基线变更） | `approve_change`、`approve_baseline` |
| `auditor` | 只读审计 | `read_audit` |

合并判定规则：

- 变更**未触及基线**且风险分值低于阈值 → 无需审批，任何角色都可合并；
- 变更风险分值达到阈值但**未触及基线** → 需要 `approve_change`；
- 变更**触及基线**（基线不变量的增删改或基线标记变化）→ 需要 `approve_baseline`。

在 `ifp diff` 上指定主体即可启用判定：

```bash
ifp diff base/examples/refund examples/refund \
  --actor alice --actor-roles modeler \
  --audit out/audit.jsonl
```

- `--actor` 是执行合并的主体，`--actor-roles` 是逗号分隔的角色名；不指定时不启用 RBAC，行为与 13.1 一致。
- 同一主体可持有多个角色，权限取并集。
- 权限不足时以退出码 `3` 结束，并在审计日志追加一条 `rbac.decision` 记录（主体、角色、所需权限、判定结果与风险分值），纳入第 6 节审计取证范围。
- 可用 `ifp rbac` 查看角色与权限矩阵。

### 13.3 编辑器诊断（LSP）

建模大多在编辑器里进行，平台因此内置语言服务器，把同一套检查搬进 IDE：

```bash
ifp lsp --stdio
```

- 语言服务器复用 `typecheck` 与 `lint` 的诊断（IFP0001 / IFP1xxx / IFP2xxx），通过 LSP `textDocument/publishDiagnostics` 推送；
- 编辑器已打开的文档与工作区磁盘上的全部 `.ifp` 会合并成一个项目模型，跨文件引用（Context / Intent / Scenario）不因文件是否保存而失配；
- 除诊断与结构大纲（`textDocument/documentSymbol`）外，还提供**跳转定义**（`textDocument/definition`）：从 `State::Member`、字段类型、场景中的动作调用与 Intent 等引用跳到声明位置，可跨文件定位；以及**快速修复**（`textDocument/codeAction`）：为基线不变量补 `owner` / `risk`、为状态机补 `initial`、为未定义字段类型生成值对象骨架；
- 参考客户端见 [`editors/vscode`](../editors/vscode)：打开 `.ifp` 即得到语法高亮、实时诊断、跳转与快速修复；设置项 `ifp.server.command` / `ifp.server.args` 可指向虚拟环境里的 `ifp`。

---

## 14. 模型维护与版本管理

1. 模型仓库使用 Git，每个 commit 附带业务变更说明。
2. 重大版本变更写迁移说明；`Contract` 变更做版本兼容评估。
3. 定期模型巡检：清理死不变量、过期场景、废弃字段。
4. 模型和线上代码定期对齐审计，防止模型与实现脱节。

---

## 15. 常见建模踩坑清单

1. 不变量写过多非硬性约束，导致求解器爆炸、业务无法落地。
2. 聚合划分过大，单个 Intent 承载过多业务，状态组合爆炸，属性测试跑不动。
3. 用浮点数表达资金，精度丢失引发虚假反例（应统一 `Decimal`）。
4. 跨域直接读取对方 Intent 内部属性，破坏领域隔离，语义 Diff 失效。
5. 基线标记泛滥，每次微小修改都要走重审批流程，拖累迭代效率。
6. 把业务可豁免特例写入不变量；特例应使用 Rule Exception，不修改主不变量。
7. Scenario 只写正向案例，不校验非法操作拦截逻辑（应包含 `rejected`）。
8. 状态机声明了 `initial` 之外的状态却无任何动作可达（不可达状态，IFP2003）。

---

## 16. 交付物清单（建模完成必须输出）

1. `.ifp` 模型源码；
2. 模型注释文档（`description` / `note`）；
3. `Scenario` 仿真场景集合；
4. 属性测试配置文件（`property_test.yaml`）；
5. 模型评审记录（业务 + 技术评审）；
6. 导出制品（OpenAPI / Protobuf / Markdown 报表等）；
7. 基线变更审批记录（如有基线修改）。
