# IFP DSL 语法规范

> 适用范围：`.ifp` 模型文件的编写、解析与工具实现。
> 权威定义：本文档依据 [`src/ifp/grammar.lark`](../src/ifp/grammar.lark) 编写；两者必须保持一致。
> 相关文档：[建模规范](modeling-standard.md) · [术语表](glossary.md) · [退款域示例](../examples/refund/refund.ifp)
> 支持**值对象成员访问**语法 `field.member`（见 [§12.7](#127-值对象成员访问)）。

IFP DSL（`.ifp`）是 IFP 平台的领域建模语言，用于描述**限界上下文、值对象、聚合根（Intent）、状态机、不变量、动作与仿真场景**。
本文逐条讲解语法规则，并给出示例与语义说明。

---

## 0. 词法基础

### 0.1 注释与空白

- 注释以 `//` 开始，直到行尾；注释会被解析器忽略。
- 空白（空格、制表符、换行）全部被忽略（`%ignore WS`）。

因此，**块内条目之间不需要任何分隔符，也不需要逗号**，实践中以换行分隔、便于阅读即可。

```text
context RefundContext {
    description: "电商退款限界上下文"   // 这一行的注释会被忽略

    intent RefundOrder {
        field orderPaidAmount: Decimal
    }
}
```

### 0.2 关键字

`context`、`description`、`value`、`invariant`、`intent`、`field`、`state`、`initial`、`action`、`pre`、`post`、`transition`、`contract`、`expose`、`event`、`produced_by`、`expr`、`baseline`、`risk`、`owner`、`note`、`scenario`、`given`、`when`、`rejected`、`then`、`old`、`true`、`false`。

### 0.3 标识符与字面量

| 词法单元 | 规则 | 用途 |
| --- | --- | --- |
| `CNAME` | `/[A-Z][A-Za-z0-9_]*/`（大写字母开头） | 上下文名、Intent 名、值对象名、契约名、状态机名、状态值、枚举成员、`enum_ref` 的两部分 |
| `NAME` | `/(?!true\b|false\b)[a-z][A-Za-z0-9_]*/`（小写字母开头，且**不能是** `true`/`false`） | 字段名、参数名、不变量名、动作名 |
| `NUMBER` | `/[0-9]+(\.[0-9]+)?/` | 数值字面量（整数或小数） |
| `BOOL` | `true` \| `false` | 布尔字面量 |
| `STRING` | `/"([^"\\]|\\.)*"/` | 带转义的双引号字符串 |

> 约定：类型名（`Decimal`/`Int`/`Bool`/`String`）与自定义的类型名用大驼峰；字段/参数用小驼峰。详见[建模规范 · 命名](modeling-standard.md#51-命名)。

`NAME` 显式排除了 `true`/`false`，确保布尔字面量总是被识别为 `BOOL`。

---

## 1. 文件结构：`start`

```text
start: (context_decl | scenario_decl)*
```

一个 `.ifp` 文件是**若干 `context` 声明与 `scenario` 声明的序列**：

- 模型声明（`context_decl`）：限界上下文及其内部的值对象、Intent、契约。
- 场景声明（`scenario_decl`）：对某 Intent 的仿真用例，通常在独立文件中编写（如 [`scenarios.ifp`](../examples/refund/scenarios.ifp)）。

两类声明可以混在同一个文件里，也可以拆成多个文件；`ifp` CLI 支持传入目录，会自动加载目录下的全部 `.ifp` 文件并合并为一个项目（见[合并与跨文件引用](#11-合并与跨文件引用)）。

---

## 2. 限界上下文：`context`

```text
context_decl: "context" CNAME "{" context_item* "}"
context_item: description | value_decl | intent_decl | contract_decl | event_decl
```

语义：一个 `context` 对应一个 DDD 限界上下文（业务域边界），内部可包含一个 `description`、若干 `value` 值对象、若干 `intent` 聚合根、若干 `contract` 跨域契约、若干 `event` 领域事件。

```text
context RefundContext {
    description: "电商退款限界上下文，负责退款单生命周期与累计退款资金约束"

    value Money { ... }
    intent RefundOrder { ... }
    contract RefundContract { ... }
    event RefundExecuted { ... }
}
```

> 命名建议：上下文名以 `Context` 结尾（Lint 规则 [`IFP2006`](modeling-standard.md#62-lint-规则码ifp2001ifp2009)）。

### 2.1 描述：`description`

```text
description: "description" ":" STRING
```

`description` 是**唯一在语义 Diff 中零风险权重**的条目（仅描述文本，不影响业务规则）。可用于 `context`、`value`、`intent`、`contract`、`event`。

---

## 3. 值对象：`value`

```text
value_decl: "value" CNAME "{" value_item* "}"
value_item: description | field_decl | invariant_decl
```

值对象（ValueObject）**无唯一标识、不可变**，仅承载属性与自身约束。它只能包含 `description`、`field` 与 `invariant`——**不允许定义状态机（`state`）和业务动作（`action`）**，这一点由语法结构本身保证。

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

### 3.1 在聚合中使用值对象

当 Intent 的字段类型是值对象时（如 `field balance: Money`），该字段由**底层叶子字段**承载：

- 在表达式中通过成员访问 `balance.amount` 引用具体成员（见 [§12.7](#127-值对象成员访问)）；
- 值对象自身的不变量（如 `amount >= 0`）会**内联到聚合上**，对所有该类型的字段实例生效，无需手工展开（见[建模规范 · 值对象](modeling-standard.md#4-值对象-valueobject-规范)）；
- `Money.currency` 这类 `String` 成员可正常参与仿真与类型检查，但不参与 SMT 求解（详见 [§5.2](#52-smt-编码对值对象的处理)）。

---

## 4. 聚合根：`intent`

```text
intent_decl: "intent" CNAME "{" intent_item* "}"
intent_item: description | field_decl | state_decl | initial_decl
           | invariant_decl | action_decl
```

Intent 是 IFP 的核心模型单元，对应 DDD 聚合根，负责描述字段、状态机、不变量与可执行动作。它可包含：

- `description`：聚合说明；
- `field`：字段声明（含可选默认值）；
- `state`：状态机声明；
- `initial`：初始状态；
- `invariant`：不变量（业务硬约束）；
- `action`：业务动作（含前置/后置条件与目标状态）。

一个 Intent 内部**只允许一个状态机**参与状态流转（语法允许多个 `state` 声明，但 `initial` 与 `action.transition` 均指向某个 `enum_ref`）。

---

## 5. 字段与类型：`field` / `type_ref`

```text
field_decl: "field" NAME ":" type_ref ("=" expr)?
?type_ref: "Decimal" -> t_decimal
         | "Int"     -> t_int
         | "Bool"    -> t_bool
         | "String"  -> t_string
         | CNAME     -> t_named
```

字段声明形如 `field <名字>: <类型> [= <默认值表达式>]`：

- 字段名使用 `NAME`（小写开头）；
- 类型 `type_ref` 可以是内建类型 `Decimal`、`Int`、`Bool`、`String`，也可以是自定义类型名（`CNAME`）——**自定义类型必须指向已声明的状态机或值对象**，否则报 [`IFP1011`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。

```text
field orderPaidAmount: Decimal          // 无默认值
field totalRefundedAmount: Decimal = 0  // 默认值 0
field status: RefundStatus = RefundStatus::Pending  // 引用状态机 + 枚举默认值
```

### 5.1 资金类型统一使用 `Decimal`

**金额字段必须使用 `Decimal`，禁止使用浮点数。** 原因：浮点精度会引入虚假反例、破坏 SMT 求解的精确性（见[建模规范](modeling-standard.md#52-类型)）。平台在解析时把小数 `NUMBER` 推断为 `Decimal`，在 SMT 编码中按**实数精确编码**，导出 Protobuf 时映射为 `string` 以避免精度丢失。

### 5.2 SMT 编码对值对象的处理

当字段类型是值对象时，平台在 SMT 编码阶段**递归内联展开**其成员：

- 值对象字段 `balance: Money` 被展开为叶子键 `balance.amount`、`balance.currency` 等；
- 值对象自身的不变量按字段前缀改写后参与求解（`amount >= 0` → `balance.amount >= 0`），因此**值对象约束在聚合内自动生效**；
- 值对象内的 `String` 成员（如 `Money.currency`）不参与数值求解，被标记为忽略，但**不会**导致整个 Intent 无法验证；
- 若值对象存在循环引用，无法内联展开，报 [`IFP3003`](modeling-standard.md#63-验证--一致性码ifp3xxx)。

---

## 6. 状态机：`state` 与 `initial`

```text
state_decl: "state" CNAME "{" state_value ("," state_value)* "}"
state_value: CNAME
initial_decl: "initial" ":" enum_ref
enum_ref: CNAME "::" CNAME
```

- **状态机声明**：`state <状态机名> { 状态1, 状态2, ... }`。**状态值之间必须用逗号 `,` 分隔**（与块内其他条目的换行分隔不同）。
- **初始状态**：`initial: <状态机名>::<状态值>`，声明聚合的初始状态。
- **枚举引用**（`enum_ref`）形如 `RefundStatus::Pending`，用于 `initial`、`transition` 以及表达式中。

```text
state RefundStatus {
    Pending,
    Approved,
    Refunded,
    Rejected
}
initial: RefundStatus::Pending
```

> 若定义了状态机却缺少 `initial`，报 [`IFP2008`](modeling-standard.md#62-lint-规则码ifp2001ifp2009)；
> 若状态机中没有任何状态值，报 [`IFP2009`](modeling-standard.md#62-lint-规则码ifp2001ifp2009)（error）；
> 未被 `initial` 或任何 `action.transition` 覆盖到的状态为**不可达状态**，报 [`IFP2003`](modeling-standard.md#62-lint-规则码ifp2001ifp2009)。

---

## 7. 不变量：`invariant`

```text
invariant_decl: "invariant" NAME "{" invariant_item* "}"
invariant_item: "expr"     ":" expr   -> inv_expr
              | "baseline" ":" BOOL   -> inv_baseline
              | "risk"     ":" STRING -> inv_risk
              | "owner"    ":" STRING -> inv_owner
              | "note"     ":" STRING -> inv_note
```

不变量是**聚合在任意合法状态下都必须永久成立的业务约束**。它可出现在 `value`、`intent`、`contract` 内部。字段说明：

| 条目 | 语法 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `expr` | 布尔表达式 | 语义必需 | 约束表达式，必须是布尔类型，否则报 [`IFP1003`](modeling-standard.md#61-类型检查码ifp1001ifp1034) |
| `baseline` | `true` / `false` | 可选 | 标记为受保护的**基线不变量**，修改须走强制审批 |
| `risk` | 字符串 | 基线建议必填 | 风险描述；基线缺 `risk` 报 [`IFP2002`](modeling-standard.md#62-lint-规则码ifp2001ifp2009) |
| `owner` | 字符串 | 基线必需 | 业务负责人；基线缺 `owner` 报 [`IFP2001`](modeling-standard.md#62-lint-规则码ifp2001ifp2009)（error） |
| `note` | 字符串 | 可选 | 说明备注 |

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

## 8. 动作：`action`

```text
action_decl: "action" CNAME action_params? "{" action_item* "}"
action_params: "(" param ("," param)* ")"
param: NAME ":" type_ref
action_item: "pre"        ":" expr     -> act_pre
           | "post"       ":" expr     -> act_post
           | "transition" ":" enum_ref -> act_transition
```

动作（Action）是聚合上允许执行的业务操作。语法要点：

- **动作名**使用 `CNAME`（大写开头）；
- **参数列表**可选，形如 `(amount: Decimal, reason: String)`，参数之间用逗号分隔；
- **动作体**可包含 `pre`（前置条件）、`post`（后置条件）、`transition`（目标状态）。

```text
action ExecuteRefund(amount: Decimal) {
    transition: RefundStatus::Refunded
    pre: status == RefundStatus::Approved && amount >= 0
    post: status == RefundStatus::Refunded
          && totalRefundedAmount == old(totalRefundedAmount) + amount
}
```

### 8.1 `pre` 前置条件

执行动作前必须成立的断言；`pre` 不成立表示动作非法、不允许执行。必须是布尔表达式（否则报 [`IFP1004`](modeling-standard.md#61-类型检查码ifp1001ifp1034)）。缺少 `pre` 会得到告警 [`IFP1020`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。

### 8.2 `post` 后置条件

动作成功执行后必须成立的结果，同样要求布尔类型。缺少 `post` 会得到告警 [`IFP1021`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。

`post` 使用一种**等式赋值 + 帧语义（frame）**的特殊解释：

- 形如 `字段 == 表达式` 的顶层合取项表示**把字段更新为新值**；
- `post` 中**未提及的字段保持不变**（自动施加 frame 约束）；
- `post` 中普通字段引用表示**动作后的新状态**，`old(字段)` 表示**动作前的旧状态**。

上面的 `ExecuteRefund` 即表达：“执行后状态为 `Refunded`，累计退款额 = 旧累计退款额 + 本次退款额”。

> `old(...)` **只能出现在 `post` 中**；出现在 `pre`、不变量或其他位置会报 [`IFP1007`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。

### 8.3 `transition` 目标状态

`transition: <状态机名>::<状态值>` 声明动作完成后聚合迁移到的目标状态。`transition` 常用于状态可达性分析（哪些状态是可达的）。

---

## 9. 跨域契约：`contract`

```text
contract_decl: "contract" CNAME "{" contract_item* "}"
contract_item: description | expose_decl | invariant_decl
expose_decl: "expose" NAME ("," NAME)*
```

契约是限界上下文对外的**最小服务契约**，供其他域调用。它可包含 `description`、`expose`（暴露字段清单）以及 `invariant`。

`expose` 声明对外暴露的**字段名**，多个字段用逗号分隔：

```text
contract RefundContract {
    description: "退款域对外最小契约，供订单域/支付域调用"

    expose totalRefundedAmount, orderPaidAmount
}
```

> 若 `expose` 引用了上下文中不存在的字段，报 [`IFP1022`](modeling-standard.md#61-类型检查码ifp1001ifp1034)（error）。

---

## 10. 领域事件：`event`

```text
event_decl: "event" CNAME "{" event_item* "}"
event_item: description | field_decl | produced_by_decl
produced_by_decl: "produced_by" ":" CNAME
```

领域事件是聚合状态发生变化时产生的**事实记录**。事件可包含 `description`、若干 `field`（事件载荷字段）以及 `produced_by`（产生该事件的动作名）。

```text
event RefundExecuted {
    description: "退款资金已执行"
    field amount: Decimal
    produced_by: ExecuteRefund
}
```

- `produced_by` 引用本上下文内某个 `intent` 的动作；若引用未定义的动作，
  报 [`IFP1040`](modeling-standard.md)（error）。
- 若声明了 `produced_by`，事件字段应与对应动作的参数一一对应（名称 + 类型），
  不匹配时报 [`IFP1041`](modeling-standard.md)（warning）。

### 10.1 事件重放

`ifp event replay <model> <events.json>` 按 JSON 事件序列逐步恢复聚合状态，
并在每一步之后校验聚合不变量：

- 事件声明了 `produced_by` 时，复用对应动作的 `post` 条件推导状态
  （事件 `data` 作为动作参数）；
- 未声明 `produced_by` 时，按字段名直接把事件 `data` 写入状态。

重放不校验动作前置条件（事件是已发生的历史事实），只关注不变量是否始终成立。

---

## 11. 仿真场景：`scenario`

```text
scenario_decl: "scenario" CNAME "{" scenario_item* "}"
scenario_item: "context"  ":" CNAME                 -> s_context
             | "intent"   ":" CNAME                 -> s_intent
             | "given"    "{" field_assign* "}"     -> s_given
             | "when"     CNAME "(" named_args? ")" -> s_when
             | "rejected" CNAME "(" named_args? ")" -> s_rejected
             | "then"     expr                      -> s_then

field_assign: field_target "=" expr
field_target: NAME ("." NAME)*
named_args: named_arg ("," named_arg)*
named_arg: NAME "=" expr
```

场景声明描述对某个 Intent 的**操作序列用例**：

| 条目 | 语法 | 说明 |
| --- | --- | --- |
| `context` | `context: RefundContext` | 指定所属上下文 |
| `intent` | `intent: RefundOrder` | 指定被测 Intent（缺少则报 [`IFP1030`](modeling-standard.md#61-类型检查码ifp1001ifp1034)） |
| `given` | `given { field = expr ... }` | 初始化字段状态，块内赋值以换行分隔 |
| `when` | `when Action(a = expr, ...)` | 期望成功执行某动作 |
| `rejected` | `rejected Action(...)` | 期望该动作被前置条件拒绝 |
| `then` | `then <bool expr>` | 对当前状态的断言 |

要点：

- `given` 块内是 `字段 = 表达式`，条目之间**换行分隔、不加逗号**；`字段` 可以是值对象成员路径（如 `balance.amount`）。引用的字段必须是 Intent 已有的（叶子）字段，否则报 [`IFP1032`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。
- `when`/`rejected` 的参数用**具名参数** `名 = 表达式`，多个之间用逗号分隔；参数集合必须与动作声明的参数**完全一致**，否则报 [`IFP1034`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。
- `when`/`rejected` 调用的动作必须在 Intent 中定义，否则报 [`IFP1033`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。
- 一个场景可以包含多个 `when` 步骤，按书写顺序依次执行。

```text
scenario PartialRefundOnce {
    context: RefundContext
    intent: RefundOrder

    given {
        orderPaidAmount = 100
        totalRefundedAmount = 0
        status = RefundStatus::Pending
    }

    when Audit()
    then status == RefundStatus::Approved

    when ExecuteRefund(amount = 40)
    then status == RefundStatus::Refunded
         && totalRefundedAmount == 40
}
```

> 建议每个场景覆盖至少一个 `rejected` 步骤以校验非法操作拦截；缺失时得到提示 [`IFP2007`](modeling-standard.md#62-lint-规则码ifp2001ifp2009)（info）。完整示例见 [`examples/refund/scenarios.ifp`](../examples/refund/scenarios.ifp)。

---

## 12. 合并与跨文件引用

CLI 在接收目录或多个文件时，会把所有 `.ifp` 合并为一个**项目模型**再检查：

- `typecheck` 可跨文件解析状态机、值对象与 Intent 引用（例如场景文件引用模型文件里的 Intent）；
- 场景文件（如 `scenarios.ifp`）通常只包含 `scenario` 声明，通过 `intent: RefundOrder` 引用定义在 `refund.ifp` 中的聚合。

因此，跨文件引用无需额外 `import` 语法，只要在同一次加载的项目范围内即可。

---

## 13. 表达式语法

```text
?expr: or_expr
?or_expr: and_expr | or_expr "||" and_expr -> or_op
?and_expr: comp_expr | and_expr "&&" comp_expr -> and_op
?comp_expr: arith | arith COMPOP arith -> compare
?arith: term | arith "+" term -> add | arith "-" term -> sub
?term: unary | term "*" unary -> mul | term "/" unary -> div
?unary: "-" unary -> neg | "old" "(" expr ")" -> old | atom
?atom: NUMBER | STRING | BOOL | enum_ref
     | NAME "(" named_args? ")" -> call
     | NAME field_path -> field_access
     | NAME -> var
     | "(" expr ")"

// 值对象成员访问：balance.amount（至少一段）
field_path: "." NAME ("." NAME)*

COMPOP: "==" | "!=" | "<=" | ">=" | "<" | ">"
```

### 12.1 运算符与优先级

从低到高：

1. `||`（逻辑或）
2. `&&`（逻辑与）
3. 比较：`==`、`!=`、`<=`、`>=`、`<`、`>`
4. 加减：`+`、`-`
5. 乘除：`*`、`/`
6. 一元：负号 `-`、`old(...)`
7. 原子：数值、字符串、布尔、枚举引用、调用、变量、括号

可用括号 `( )` 显式改变优先级。

### 12.2 算术运算

`+ - * /`。要求两侧为数值类型（`Decimal` 或 `Int`），否则报 [`IFP1008`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。`Decimal` 与 `Int` 可混合运算，结果按含 `Decimal` 或除法规则推断为 `Decimal`。

```text
totalRefundedAmount == old(totalRefundedAmount) + amount
```

### 12.3 比较运算

`==`、`!=`、`<=`、`>=`、`<`、`>`，结果均为布尔。两侧类型需相容：数值之间可混合比较；大小比较（`< <= > >=`）要求数值操作数；其他类型必须一致，否则报 [`IFP1009`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。

```text
totalRefundedAmount <= orderPaidAmount
status == RefundStatus::Pending
```

### 12.4 逻辑运算

`&&`（与）、`||`（或），两侧必须是布尔表达式，否则报 [`IFP1010`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。

```text
status == RefundStatus::Approved && amount >= 0
```

### 12.5 `old()` 旧状态引用

`old(x)` 表示“动作执行前 `x` 的值”，**仅允许出现在 `post` 中**（否则报 [`IFP1007`](modeling-standard.md#61-类型检查码ifp1001ifp1034)）。

```text
post: totalRefundedAmount == old(totalRefundedAmount) + amount
```

### 12.6 布尔字面量

`true` 与 `false`，类型为 `Bool`。注意 `true`/`false` 被 `BOOL` 词法单元优先识别，不能作为普通标识符（`NAME` 已排除）。

```text
invariant alwaysTrue {
    expr: true    // 恒真：会被 Lint 判定为死不变量 IFP2004
}
```

### 12.7 值对象成员访问

当字段类型为值对象时，用 `字段.成员` 引用其成员，可连续多级（`a.b.c`）：

```text
invariant balanceNonNegative { expr: balance.amount >= 0 }
action ExecuteRefund(amount: Decimal) {
    pre: status == RefundStatus::Approved && amount >= 0
    post: totalRefundedAmount == old(totalRefundedAmount) + amount
}
```

规则：

- 成员访问的**根**必须是 Intent 中已声明的值对象字段；
- 路径中的每一段都必须存在于对应值对象的字段中，否则报 [`IFP1023`](modeling-standard.md#61-类型检查码ifp1001ifp1034)；
- 成员访问可出现在不变量、`pre`/`post`、场景 `given` 与 `then` 中；出现在 `post` 左值位置表示对值对象成员赋值（如 `balance.amount == old(balance.amount) - amount`）；
- 值对象成员访问与普通变量名同优先级（原子级）。

### 12.8 其他原子

- **数值**：`0`、`40`、`0.25`（含小数点即 `Decimal`，否则 `Int`）。
- **字符串**：`"finance-team"`（支持转义）。
- **枚举引用**：`RefundStatus::Pending`。
- **变量**：字段名、动作参数名。
- **调用**：`Name(args)` 语法上出现在场景步骤中（`when`/`rejected`）；在普通表达式内不允许调用，否则报 [`IFP1002`](modeling-standard.md#61-类型检查码ifp1001ifp1034)。

---

## 14. 完整示例

一个最小但完整的模型（含状态机、不变量、动作与场景）：

```text
context ExampleContext {
    description: "示例限界上下文"

    intent ExampleAggregate {
        description: "示例聚合根"
        field amount: Decimal = 0
        field status: ExampleStatus = ExampleStatus::Created

        state ExampleStatus {
            Created,
            Done
        }

        initial: ExampleStatus::Created

        invariant amountNonNegative {
            expr: amount >= 0
            note: "金额不得为负"
            owner: "your-name"
        }

        action Finish {
            transition: ExampleStatus::Done
            pre: status == ExampleStatus::Created
            post: status == ExampleStatus::Done
        }
    }
}
```

> 上述结构由 `ifp init <dir>` 脚手架生成雏形（脚手架模板略有差异）。真实且含资损反例的完整模型见 [`examples/refund/refund.ifp`](../examples/refund/refund.ifp)。

---

## 附：语法 → 诊断码速查

| 语法点 | 相关诊断码 |
| --- | --- |
| 文件整体解析（词法 / 语法） | IFP0001（语法错误；编辑器内由语言服务器 `ifp lsp` 以诊断形式呈现） |
| 字段/参数/变量引用 | IFP1001（未定义标识符）、IFP1011（未定义类型）、IFP1012（重复定义） |
| 值对象成员访问 | IFP1023（成员不存在 / 根不是值对象） |
| 不变量 `expr` | IFP1003（非布尔）、IFP2004（死不变量）、IFP2005（无任何不变量）、IFP3001（不变量互相矛盾） |
| 动作 `pre`/`post` | IFP1004、IFP1007（`old` 位置）、IFP1020/IFP1021（缺 pre/post）、IFP3002（动作不可触发） |
| 状态机 / `initial` | IFP2003、IFP2008、IFP2009 |
| 枚举引用 | IFP1005、IFP1006 |
| 契约 `expose` | IFP1022 |
| 场景 | IFP1030 ~ IFP1034、IFP2007 |
| 基线不变量 | IFP2001、IFP2002 |

完整的诊断码清单与处置方式见[建模规范](modeling-standard.md#6-诊断码总览)。
