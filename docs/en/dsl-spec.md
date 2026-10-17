# IFP DSL Grammar Specification

> Chinese version: [dsl-spec.md](../dsl-spec.md)

> Scope: authoring, parsing, and tool implementation of `.ifp` model files.
> Authoritative definition: this document is written against [`src/ifp/grammar.lark`](../../src/ifp/grammar.lark); the two must stay consistent.
> Related documents: [Modeling Standard](modeling-standard.md) · [Glossary](glossary.md) · [Refund domain example](../../examples/refund/refund.ifp)
> Supports the **ValueObject member access** syntax `field.member` (see [§12.7](#127-valueobject-member-access)).

IFP DSL (`.ifp`) is the domain modeling language of the IFP platform, used to describe **BoundedContexts, ValueObjects, aggregate roots (Intent), StateMachines, Invariants, Actions, and Scenarios**.
This document explains the grammar rules one by one, with examples and semantic notes.

---

## 0. Lexical Basics

### 0.1 Comments and Whitespace

- Comments start with `//` and run to the end of the line; comments are ignored by the parser.
- Whitespace (spaces, tabs, newlines) is entirely ignored (`%ignore WS`).

Therefore, **no separator and no comma are needed between entries within a block**; in practice, separating them with newlines for readability is enough.

```text
context RefundContext {
    description: "电商退款限界上下文"   // the comment on this line is ignored

    intent RefundOrder {
        field orderPaidAmount: Decimal
    }
}
```

### 0.2 Keywords

`context`, `description`, `value`, `invariant`, `intent`, `field`, `state`, `initial`, `action`, `pre`, `post`, `transition`, `contract`, `expose`, `expr`, `baseline`, `risk`, `owner`, `note`, `scenario`, `given`, `when`, `rejected`, `then`, `old`, `true`, `false`.

### 0.3 Identifiers and Literals

| Token | Rule | Purpose |
| --- | --- | --- |
| `CNAME` | `/[A-Z][A-Za-z0-9_]*/` (starts with an uppercase letter) | Context name, Intent name, ValueObject name, Contract name, StateMachine name, state value, enum member, both parts of `enum_ref` |
| `NAME` | `/(?!true\b|false\b)[a-z][A-Za-z0-9_]*/` (starts with a lowercase letter and **cannot be** `true`/`false`) | Field name, parameter name, Invariant name, Action name |
| `NUMBER` | `/[0-9]+(\.[0-9]+)?/` | Numeric literal (integer or decimal) |
| `BOOL` | `true` \| `false` | Boolean literal |
| `STRING` | `/"([^"\\]|\\.)*"/` | Double-quoted string with escapes |

> Convention: type names (`Decimal`/`Int`/`Bool`/`String`) and custom type names use UpperCamelCase; fields/parameters use lowerCamelCase. See [Modeling Standard · Naming](modeling-standard.md#51-naming).

`NAME` explicitly excludes `true`/`false`, ensuring boolean literals are always recognized as `BOOL`.

---

## 1. File Structure: `start`

```text
start: (context_decl | scenario_decl)*
```

A `.ifp` file is **a sequence of several `context` declarations and `scenario` declarations**:

- Model declarations (`context_decl`): BoundedContexts and their internal ValueObjects, Intents, and Contracts.
- Scenario declarations (`scenario_decl`): simulation cases for an Intent, usually written in a separate file (e.g. [`scenarios.ifp`](../../examples/refund/scenarios.ifp)).

The two kinds of declarations can be mixed in the same file or split into multiple files; the `ifp` CLI accepts a directory and automatically loads all `.ifp` files in it, merging them into a single project (see [Merging and Cross-file References](#11-merging-and-cross-file-references)).

---

## 2. BoundedContext: `context`

```text
context_decl: "context" CNAME "{" context_item* "}"
context_item: description | value_decl | intent_decl | contract_decl
```

Semantics: one `context` corresponds to one DDD BoundedContext (business domain boundary), and may internally contain one `description`, several `value` ValueObjects, several `intent` aggregate roots, and several `contract` cross-domain Contracts.

```text
context RefundContext {
    description: "电商退款限界上下文，负责退款单生命周期与累计退款资金约束"

    value Money { ... }
    intent RefundOrder { ... }
    contract RefundContract { ... }
}
```

> Naming suggestion: context names should end with `Context` (Lint rule [`IFP2006`](modeling-standard.md#62-lint-rule-codes-ifp2001ifp2009)).

### 2.1 Description: `description`

```text
description: "description" ":" STRING
```

`description` is **the only entry with zero risk weight in Semantic Diff** (it is only descriptive text and does not affect business rules). It can be used in `context`, `value`, `intent`, and `contract`.

---

## 3. ValueObject: `value`

```text
value_decl: "value" CNAME "{" value_item* "}"
value_item: description | field_decl | invariant_decl
```

A ValueObject **has no unique identity and is immutable**, carrying only attributes and its own constraints. It can contain only `description`, `field`, and `invariant` — **defining a StateMachine (`state`) or a business Action (`action`) is not allowed**, and this is guaranteed by the grammar structure itself.

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

### 3.1 Using ValueObjects in an Aggregate

When an Intent's field type is a ValueObject (e.g. `field balance: Money`), that field is carried by **leaf fields at the bottom level**:

- In expressions, refer to concrete members through Member Access `balance.amount` (see [§12.7](#127-valueobject-member-access));
- The ValueObject's own Invariants (e.g. `amount >= 0`) are **inlined into the aggregate** and take effect for every field instance of that type, with no manual expansion needed (see [Modeling Standard · ValueObject](modeling-standard.md#4-valueobject-standard));
- `String` members such as `Money.currency` can participate normally in simulation and type checking, but do not participate in SMT solving (see [§5.2](#52-smt-encoding-handling-of-valueobjects)).

---

## 4. Aggregate Root: `intent`

```text
intent_decl: "intent" CNAME "{" intent_item* "}"
intent_item: description | field_decl | state_decl | initial_decl
           | invariant_decl | action_decl
```

Intent is IFP's core model unit, corresponding to a DDD aggregate root, describing fields, StateMachines, Invariants, and executable Actions. It may contain:

- `description`: aggregate description;
- `field`: field declaration (with an optional default value);
- `state`: StateMachine declaration;
- `initial`: initial state;
- `invariant`: Invariant (hard business constraint);
- `action`: business Action (including precondition/postcondition and target state).

Within an Intent, **only one StateMachine** participates in state transitions (the grammar allows multiple `state` declarations, but both `initial` and `action.transition` point to a single `enum_ref`).

---

## 5. Fields and Types: `field` / `type_ref`

```text
field_decl: "field" NAME ":" type_ref ("=" expr)?
?type_ref: "Decimal" -> t_decimal
         | "Int"     -> t_int
         | "Bool"    -> t_bool
         | "String"  -> t_string
         | CNAME     -> t_named
```

A field declaration has the form `field <name>: <type> [= <default value expression>]`:

- Field names use `NAME` (starting with a lowercase letter);
- The type `type_ref` can be a built-in type `Decimal`, `Int`, `Bool`, `String`, or a custom type name (`CNAME`) — **a custom type must point to a declared StateMachine or ValueObject**, otherwise [`IFP1011`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported.

```text
field orderPaidAmount: Decimal          // no default value
field totalRefundedAmount: Decimal = 0  // default value 0
field status: RefundStatus = RefundStatus::Pending  // references a StateMachine + enum default value
```

### 5.1 Money Types Uniformly Use `Decimal`

**Amount fields must use `Decimal`; floating-point numbers are forbidden.** The reason: floating-point precision introduces spurious Counterexamples and breaks the exactness of SMT solving (see [Modeling Standard](modeling-standard.md#52-types)). When parsing, the platform infers a decimal `NUMBER` as `Decimal`, encodes it as an **exact real number** in SMT encoding, and maps it to `string` when exporting Protobuf to avoid precision loss.

### 5.2 SMT Encoding Handling of ValueObjects

When a field type is a ValueObject, the platform **recursively Inline Expands** its members during the SMT encoding stage:

- The ValueObject field `balance: Money` is expanded into leaf keys such as `balance.amount`, `balance.currency`, etc.;
- The ValueObject's own Invariants are rewritten with the field prefix and then participate in solving (`amount >= 0` → `balance.amount >= 0`), so **ValueObject constraints take effect automatically within the aggregate**;
- `String` members inside a ValueObject (such as `Money.currency`) do not participate in numeric solving and are marked as ignored, but they do **not** make the whole Intent unverifiable;
- If a ValueObject has a cyclic reference and cannot be Inline Expanded, [`IFP3003`](modeling-standard.md#63-verification--consistency-codes-ifp3xxx) is reported.

---

## 6. StateMachine: `state` and `initial`

```text
state_decl: "state" CNAME "{" state_value ("," state_value)* "}"
state_value: CNAME
initial_decl: "initial" ":" enum_ref
enum_ref: CNAME "::" CNAME
```

- **StateMachine declaration**: `state <state machine name> { state1, state2, ... }`. **State values must be separated by commas `,`** (unlike the newline separation of other entries within a block).
- **Initial state**: `initial: <state machine name>::<state value>`, declaring the aggregate's initial state.
- **Enum reference** (`enum_ref`) has the form `RefundStatus::Pending`, used in `initial`, `transition`, and expressions.

```text
state RefundStatus {
    Pending,
    Approved,
    Refunded,
    Rejected
}
initial: RefundStatus::Pending
```

> If a StateMachine is defined but `initial` is missing, [`IFP2008`](modeling-standard.md#62-lint-rule-codes-ifp2001ifp2009) is reported;
> if a StateMachine has no state value at all, [`IFP2009`](modeling-standard.md#62-lint-rule-codes-ifp2001ifp2009) is reported (error);
> a state not covered by `initial` or any `action.transition` is an **unreachable state**, and [`IFP2003`](modeling-standard.md#62-lint-rule-codes-ifp2001ifp2009) is reported.

---

## 7. Invariant: `invariant`

```text
invariant_decl: "invariant" NAME "{" invariant_item* "}"
invariant_item: "expr"     ":" expr   -> inv_expr
              | "baseline" ":" BOOL   -> inv_baseline
              | "risk"     ":" STRING -> inv_risk
              | "owner"    ":" STRING -> inv_owner
              | "note"     ":" STRING -> inv_note
```

An Invariant is **a business constraint that must hold permanently for the aggregate in any legal state**. It can appear inside `value`, `intent`, and `contract`. Field descriptions:

| Entry | Grammar | Required | Description |
| --- | --- | --- | --- |
| `expr` | Boolean expression | Semantically required | Constraint expression; must be Boolean, otherwise [`IFP1003`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported |
| `baseline` | `true` / `false` | Optional | Marks a protected **Baseline Invariant**; changes must go through mandatory approval |
| `risk` | String | Recommended for Baseline | Risk description; a Baseline missing `risk` reports [`IFP2002`](modeling-standard.md#62-lint-rule-codes-ifp2001ifp2009) |
| `owner` | String | Required for Baseline | Business owner; a Baseline missing `owner` reports [`IFP2001`](modeling-standard.md#62-lint-rule-codes-ifp2001ifp2009) (error) |
| `note` | String | Optional | Explanatory note |

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

## 8. Action: `action`

```text
action_decl: "action" CNAME action_params? "{" action_item* "}"
action_params: "(" param ("," param)* ")"
param: NAME ":" type_ref
action_item: "pre"        ":" expr     -> act_pre
           | "post"       ":" expr     -> act_post
           | "transition" ":" enum_ref -> act_transition
```

An Action is a business operation allowed on the aggregate. Grammar highlights:

- **Action name** uses `CNAME` (starting with an uppercase letter);
- **Parameter list** is optional, in the form `(amount: Decimal, reason: String)`, with parameters separated by commas;
- **Action body** may contain `pre` (precondition), `post` (postcondition), and `transition` (target state).

```text
action ExecuteRefund(amount: Decimal) {
    transition: RefundStatus::Refunded
    pre: status == RefundStatus::Approved && amount >= 0
    post: status == RefundStatus::Refunded
          && totalRefundedAmount == old(totalRefundedAmount) + amount
}
```

### 8.1 `pre` Precondition

An assertion that must hold before the Action is executed; if `pre` does not hold, the Action is illegal and is not allowed to execute. It must be a Boolean expression (otherwise [`IFP1004`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported). A missing `pre` produces the warning [`IFP1020`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034).

### 8.2 `post` Postcondition

The result that must hold after the Action executes successfully; a Boolean type is likewise required. A missing `post` produces the warning [`IFP1021`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034).

`post` uses a special interpretation of **equality assignment plus frame semantics**:

- A top-level conjunct of the form `field == expression` means **the field is updated to the new value**;
- **Fields not mentioned in `post` remain unchanged** (a frame constraint is applied automatically);
- An ordinary field reference in `post` denotes the **new state after the Action**, and `old(field)` denotes the **old state before the Action**.

The `ExecuteRefund` above therefore expresses: "the state after execution is `Refunded`, and the cumulative refunded amount = the old cumulative refunded amount + this refund's amount".

> `old(...)` **may appear only in `post`**; appearing in `pre`, an Invariant, or elsewhere reports [`IFP1007`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034).

### 8.3 `transition` Target State

`transition: <state machine name>::<state value>` declares the target state the aggregate transitions to after the Action completes. `transition` is commonly used for state reachability analysis (which states are reachable).

---

## 9. Contract: `contract`

```text
contract_decl: "contract" CNAME "{" contract_item* "}"
contract_item: description | expose_decl | invariant_decl
expose_decl: "expose" NAME ("," NAME)*
```

A Contract is the BoundedContext's **minimal service contract** exposed to the outside, for other domains to call. It may contain `description`, `expose` (a list of exposed fields), and `invariant`.

`expose` declares the **field names** exposed to the outside, with multiple fields separated by commas:

```text
contract RefundContract {
    description: "退款域对外最小契约，供订单域/支付域调用"

    expose totalRefundedAmount, orderPaidAmount
}
```

> If `expose` references a field that does not exist in the context, [`IFP1022`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported (error).

---

## 10. Scenario: `scenario`

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

A Scenario declaration describes an **operation-sequence test case** for a given Intent:

| Entry | Grammar | Description |
| --- | --- | --- |
| `context` | `context: RefundContext` | Specifies the owning context |
| `intent` | `intent: RefundOrder` | Specifies the Intent under test (missing → [`IFP1030`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported) |
| `given` | `given { field = expr ... }` | Initializes field state; assignments within the block are newline-separated |
| `when` | `when Action(a = expr, ...)` | Expects an Action to execute successfully |
| `rejected` | `rejected Action(...)` | Expects the Action to be rejected by the precondition |
| `then` | `then <bool expr>` | An assertion about the current state |

Key points:

- Inside the `given` block are `field = expression` entries, **newline-separated with no commas**; `field` can be a ValueObject member path (e.g. `balance.amount`). The referenced field must be an existing (leaf) field of the Intent, otherwise [`IFP1032`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported.
- The arguments of `when`/`rejected` use **named arguments** `name = expression`, separated by commas; the argument set must **exactly match** the Action's declared parameters, otherwise [`IFP1034`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported.
- The Action invoked by `when`/`rejected` must be defined in the Intent, otherwise [`IFP1033`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported.
- A Scenario can contain multiple `when` steps, executed in the order written.

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

> It is recommended that every Scenario cover at least one `rejected` step to verify illegal-operation interception; when missing, the hint [`IFP2007`](modeling-standard.md#62-lint-rule-codes-ifp2001ifp2009) (info) is given. For a full example, see [`examples/refund/scenarios.ifp`](../../examples/refund/scenarios.ifp).

---

## 11. Merging and Cross-file References

When the CLI receives a directory or multiple files, it merges all `.ifp` files into a single **project model** before checking:

- `typecheck` can resolve StateMachine, ValueObject, and Intent references across files (for example, a Scenario file referencing an Intent defined in a model file);
- A Scenario file (such as `scenarios.ifp`) usually contains only `scenario` declarations, referencing the aggregate defined in `refund.ifp` via `intent: RefundOrder`.

Therefore, cross-file references need no extra `import` syntax; it is enough that they fall within the project scope of the same load.

---

## 12. Expression Grammar

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

// ValueObject member access: balance.amount (at least one segment)
field_path: "." NAME ("." NAME)*

COMPOP: "==" | "!=" | "<=" | ">=" | "<" | ">"
```

### 12.1 Operators and Precedence

From lowest to highest:

1. `||` (logical OR)
2. `&&` (logical AND)
3. Comparison: `==`, `!=`, `<=`, `>=`, `<`, `>`
4. Addition/subtraction: `+`, `-`
5. Multiplication/division: `*`, `/`
6. Unary: negation `-`, `old(...)`
7. Atom: number, string, boolean, enum reference, call, variable, parentheses

Parentheses `( )` can be used to explicitly change precedence.

### 12.2 Arithmetic Operations

`+ - * /`. Both operands must be numeric types (`Decimal` or `Int`), otherwise [`IFP1008`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported. `Decimal` and `Int` can be mixed; the result is inferred as `Decimal` when `Decimal` is involved or by the division rule.

```text
totalRefundedAmount == old(totalRefundedAmount) + amount
```

### 12.3 Comparison Operations

`==`, `!=`, `<=`, `>=`, `<`, `>`; the result is always Boolean. The two sides must have compatible types: numbers can be compared in a mix; ordering comparisons (`< <= > >=`) require numeric operands; other types must be identical, otherwise [`IFP1009`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported.

```text
totalRefundedAmount <= orderPaidAmount
status == RefundStatus::Pending
```

### 12.4 Logical Operations

`&&` (AND), `||` (OR); both sides must be Boolean expressions, otherwise [`IFP1010`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported.

```text
status == RefundStatus::Approved && amount >= 0
```

### 12.5 `old()` Old-state Reference

`old(x)` denotes "the value of `x` before the Action is executed" and **is allowed only in `post`** (otherwise [`IFP1007`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported).

```text
post: totalRefundedAmount == old(totalRefundedAmount) + amount
```

### 12.6 Boolean Literals

`true` and `false`, of type `Bool`. Note that `true`/`false` are recognized first by the `BOOL` token and cannot be used as ordinary identifiers (`NAME` already excludes them).

```text
invariant alwaysTrue {
    expr: true    // always true: Model Lint judges it a dead invariant IFP2004
}
```

### 12.7 ValueObject Member Access

When a field type is a ValueObject, use `field.member` to reference its members, and multiple levels can be chained (`a.b.c`):

```text
invariant balanceNonNegative { expr: balance.amount >= 0 }
action ExecuteRefund(amount: Decimal) {
    pre: status == RefundStatus::Approved && amount >= 0
    post: totalRefundedAmount == old(totalRefundedAmount) + amount
}
```

Rules:

- The **root** of a Member Access must be a ValueObject field declared in the Intent;
- Every segment in the path must exist as a field of the corresponding ValueObject, otherwise [`IFP1023`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported;
- Member Access can appear in Invariants, `pre`/`post`, and Scenario `given` and `then`; appearing in the left-hand position of `post` means assigning to a ValueObject member (e.g. `balance.amount == old(balance.amount) - amount`);
- ValueObject Member Access has the same precedence as ordinary variable names (atom level).

### 12.8 Other Atoms

- **Numbers**: `0`, `40`, `0.25` (with a decimal point it is `Decimal`, otherwise `Int`).
- **Strings**: `"finance-team"` (escapes are supported).
- **Enum reference**: `RefundStatus::Pending`.
- **Variables**: field names, Action parameter names.
- **Calls**: `Name(args)` appears syntactically in Scenario steps (`when`/`rejected`); calls are not allowed inside ordinary expressions, otherwise [`IFP1002`](modeling-standard.md#61-type-check-codes-ifp1001ifp1034) is reported.

---

## 13. Complete Example

A minimal but complete model (with a StateMachine, an Invariant, an Action, and a Scenario):

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

> The structure above is scaffolded by `ifp init <dir>` (the scaffold template differs slightly). For a real, complete model containing a capital-loss Counterexample, see [`examples/refund/refund.ifp`](../../examples/refund/refund.ifp).

---

## Appendix: Grammar → Diagnostic Code Quick Reference

| Grammar Point | Related Diagnostic Codes |
| --- | --- |
| Whole-file parsing (lexer / parser) | IFP0001 (syntax error; surfaced as a diagnostic in editors by the language server `ifp lsp`) |
| Field/parameter/variable reference | IFP1001 (undefined identifier), IFP1011 (undefined type), IFP1012 (duplicate definition) |
| ValueObject Member Access | IFP1023 (member does not exist / root is not a ValueObject) |
| Invariant `expr` | IFP1003 (not Boolean), IFP2004 (dead invariant), IFP2005 (no Invariant at all), IFP3001 (Invariants contradict each other) |
| Action `pre`/`post` | IFP1004, IFP1007 (`old` position), IFP1020/IFP1021 (missing pre/post), IFP3002 (Action cannot be triggered) |
| StateMachine / `initial` | IFP2003, IFP2008, IFP2009 |
| Enum reference | IFP1005, IFP1006 |
| Contract `expose` | IFP1022 |
| Scenario | IFP1030 ~ IFP1034, IFP2007 |
| Baseline Invariant | IFP2001, IFP2002 |

For the complete diagnostic code list and how to handle them, see the [Modeling Standard](modeling-standard.md#6-diagnostic-code-overview).
