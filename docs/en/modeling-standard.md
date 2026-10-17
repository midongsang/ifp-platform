# IFP Modeling Standard (Development / Modeler Edition)

> Chinese version: [modeling-standard.md](../modeling-standard.md)

> Goal: unify modeling style, reduce SMT solving overhead, ensure model readability, and guarantee model-business alignment.
> Scope: writing `.ifp` model files, model review, static Lint / type checking, simulation and property-test configuration.
> Intended readers: modeling engineers, backend developers, platform maintenance and review staff.
>
> Related documents: [DSL Specification](dsl-spec.md) · [Glossary](glossary.md) · [Business-side Training Material](training-business.md)

---

## 1. Should You Model: Pre-assessment

Assess before modeling; do not model blindly:

| Conclusion | Typical scenarios |
| --- | --- |
| ✅ Suitable for modeling | High-risk businesses such as fund settlement, refunds, billing, risk control, and compliance approval; complex rules with multiple overlapping scenarios; once an error occurs it causes financial loss / compliance penalty |
| ❌ Not recommended | Simple CRUD, one-off campaigns, low-risk businesses; the modeling maintenance cost outweighs the benefit |

> Pre-assessment output: a business domain boundary document that clarifies the BoundedContext scope and identifies external dependencies (other domains' `Contract`).

---

## 2. BoundedContext Standard

1. One BoundedContext corresponds to one independent business domain; do not merge across large business modules.
2. High cohesion inside a context; cross-domain interaction can only go through `Contract`, and **directly referencing the internal properties of another domain's Intent is forbidden**.
3. Naming: UpperCamelCase, with clear business semantics, e.g. `RefundContext`, `SettlementContext`; meaningless abbreviations are forbidden.
4. The first line of each `context` should be `description` (or a comment), explaining the domain's responsibility, external dependencies, and risk level.
5. It is recommended that a context name ends with `Context`; otherwise Lint reports [`IFP2006`](#62-lint-rule-codes-ifp2001ifp2009) (warning).

```text
context RefundContext {
    description: "电商退款限界上下文，负责退款单生命周期与累计退款资金约束"
    // ...
}
```

---

## 3. Intent (Aggregate Root) Modeling Standard

1. One Intent represents one aggregate and follows DDD aggregate design principles: **the aggregate root is responsible for maintaining all its own Invariants**.
2. Entities and ValueObjects inside the aggregate are managed uniformly by the aggregate root; the outside can only operate through the aggregate root and cannot directly modify internal sub-objects.
3. Naming: UpperCamelCase, the business entity name, e.g. `RefundOrder`.
4. The StateMachine must be defined inside the Intent; state enum names should be semantic; meaningless names such as `Status1`/`Status2` are forbidden.
5. Action: the business actions allowed on the aggregate; every Action should define `pre` and `post`.
   - `pre`: must hold before execution; if it does not hold, the action is illegal and must not be executed.
   - `post`: the result that must be guaranteed after the action executes successfully.
6. Invariants are written at the top level of the Intent; **only the constraints that "must hold permanently in any legal state of the aggregate"** are written as `invariant`.
   - Hard constraints such as total fund amount, limits, and quantity caps should be prioritized for the `baseline` mark.
   - Temporary business special cases, and rules that can be exempted by manual approval, **must not be written into `invariant`**; they follow the "Rule Exception" process.
7. Forbidden: writing temporary business switches or dynamically configurable policies into Invariants (policy-like things belong in a `Contract` or a field, not hard-coded in `invariant`).

> Constraint association: if an Intent has **no Invariant at all**, Lint reports [`IFP2005`](#62-lint-rule-codes-ifp2001ifp2009) (warning).

---

## 4. ValueObject Standard

1. A ValueObject is immutable, has no independent ID, and its lifecycle depends on the aggregate root.
2. Suitable for: amounts, addresses, line items, rates, identity document information, etc.
3. A ValueObject can define its own Invariants internally (for example, amount `>= 0`).
4. Naming: UpperCamelCase.
5. A ValueObject **must not define a StateMachine or business Action** (this is unsupported at the DSL syntax level; see [DSL Specification · value](dsl-spec.md#3-valueobject-value)).

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

### 4.1 Using ValueObjects in an Aggregate

1. An Intent field can be declared directly with a ValueObject type, e.g. `field balance: Money`.
2. Referencing its members uses **Member Access**: `balance.amount` (DSL Specification [§12.7](dsl-spec.md#127-valueobject-member-access)).
3. A ValueObject's own Invariants are **automatically Inline Expanded** onto the aggregate (`amount >= 0` → `balance.amount >= 0`); when modeling you do **not** need to manually copy member constraints into the Intent.
4. A `String` member inside a ValueObject (such as `Money.currency`) does not participate in SMT numeric solving, but it does not affect validation of the Intent's other fields.

```text
intent Wallet {
    field balance: Money              // ValueObject field

    invariant balanceWithinLimit {
        expr: balance.amount <= 1000  // direct member reference
        note: "单账户余额上限"
        owner: "finance-team"
    }

    action Deposit(amount: Decimal) {
        pre: amount >= 0
        post: balance.amount == old(balance.amount) + amount   // assign to member
    }
}
```

> After a ValueObject is Inline Expanded, its Invariants appear in Counterexamples under **prefixed names** such as `balance.nonNegative`, making it easy to locate which field instance was broken.

---

## 5. Expression and DSL Coding Standard

### 5.1 Naming

| Object | Style | Example |
| --- | --- | --- |
| Intent / ValueObject / Contract | UpperCamelCase | `OrderAmount` |
| Property / field / local variable | lowerCamelCase | `totalRefundAmount` |
| State enum | UpperCamelCase, enum values uppercase | `RefundStatus::AuditPass` |

### 5.2 Types

1. **The fund type must uniformly use `Decimal`; floating point is forbidden** (floating-point precision issues cause SMT solving false positives).
2. Booleans, integers, and strings are used as needed; for strings, prefer enums over free text.
3. Types must be declared explicitly; do not use implicit type inference.

### 5.3 Expression Style

1. Split expressions, one constraint per line; do not cram a lot of logic onto a single line, so it is easier to read and to do Semantic Diff on.
2. Combine conditions with `&&`, `||`, and add parentheses to make precedence explicit.
3. Avoid overly deep nested boolean expressions; split complex conditions into intermediate boolean properties.
4. Complex business constraints must be commented, stating the business source, to facilitate business review.

### 5.4 Performance Constraints (Key Focus for SMT Solving Optimization)

1. Avoid extremely large unbounded integers; give reasonable lower/upper bounds to reduce the solver's search space (corresponds to VerifyConfig's `lower` / `upper`).
2. Avoid multi-layer nesting and full traversal of large collections, which significantly increases Property Test time.
3. Set a reasonable upper limit on collection size; boundaries can be configured during simulation / Property Test.
4. If the model hits a solving timeout ([`IFP3010`](#6-diagnostic-code-overview)): simplify the Invariants, split the aggregate, and reduce the combined state space.

---

## 6. Diagnostic Code Overview

All diagnostic codes of the IFP platform fall into five categories: **parsing (IFP0001)**, **type checking (IFP1xxx)**, **Model Lint (IFP2xxx)**, **verification / consistency (IFP3xxx)**, and CLI runtime errors. All diagnostics include `file:line` and a severity level (error / warning / info).

IFP0001 means the `.ifp` source has a lexer/parser error, so no AST can be built: the CLI reports it and stops before any check runs, while editors surface it as a diagnostic through the language server `ifp lsp` (see [§13.3](#133-editor-diagnostics-lsp)).

### 6.1 Type Check Codes (IFP1001~IFP1034)

Produced by [`typecheck.py`](../../src/ifp/typecheck.py), focusing on "whether types and references are legal". The main categories are:

| Code | Meaning | Level |
| --- | --- | --- |
| IFP1001 | Referenced an undefined identifier | error |
| IFP1002 | A function call appears inside an expression (calls are only allowed in a Scenario's `when`/`rejected`) | error |
| IFP1003 | Invariant expression is not a boolean type | error |
| IFP1004 | Action `pre`/`post` is not a boolean expression | error |
| IFP1005 | Referenced an undefined StateMachine | error |
| IFP1006 | The state does not exist in the StateMachine | error |
| IFP1007 | `old(...)` appears outside `post` | error |
| IFP1008 | Arithmetic operation type mismatch (non-numeric operand) | error |
| IFP1009 | Comparison operation type mismatch | error |
| IFP1010 | Logical operation operand is not boolean | error |
| IFP1011 | Undefined field type (should be a StateMachine or ValueObject) | error |
| IFP1012 | Duplicate field definition | error |
| IFP1023 | Illegal ValueObject member access (member does not exist, or the root is not a ValueObject) | error |
| IFP1020 | Action is missing the precondition `pre` | warning |
| IFP1021 | Action is missing the postcondition `post` | warning |
| IFP1022 | Contract `expose` exposes an undefined field | error |
| IFP1030 | Scenario is missing `intent` | error |
| IFP1031 | Scenario references an undefined intent | error |
| IFP1032 | Scenario `given` references an undefined field | error |
| IFP1033 | Scenario calls an undefined action | error |
| IFP1034 | Scenario action call argument mismatch | error |

### 6.2 Lint Rule Codes (IFP2001~IFP2009)

Produced by [`lint.py`](../../src/ifp/lint.py), focusing on "whether it conforms to the modeling standard and best practices".

| Code | Rule | Level | Suggested action |
| --- | --- | --- | --- |
| IFP2001 | Baseline Invariant is missing `owner` (business owner) | **error** | Add `owner: "<owner>"` to the Baseline Invariant |
| IFP2002 | Baseline Invariant is missing `risk` (risk description) | warning | Add `risk`, describing the business consequences of breaking this constraint |
| IFP2003 | Unreachable state: a state that is neither initial nor reachable via an action `transition` | warning | Add an action or remove the redundant state |
| IFP2004 | Dead Invariant: an always-true expression or one that is completely duplicated with another Invariant expression | warning | Clean up redundant/always-true Invariants |
| IFP2005 | Intent defines no Invariant | warning | Add core business constraints; a simple aggregate that genuinely has no constraints need not be modeled |
| IFP2006 | Context naming is recommended to end with `Context` | warning | Rename to conform to the naming standard |
| IFP2007 | Scenario is missing a `rejected` step (illegal-operation interception not covered) | **info** | Add an illegal-operation interception scenario |
| IFP2008 | A StateMachine is defined but `initial` is missing | warning | Add `initial: <StateMachine>::<state value>` |
| IFP2009 | StateMachine has no state value at all | **error** | Declare at least one state value |

> "Dead Invariant" currently detects two kinds: an expression that is **completely duplicated** after `render`, and **always-true comparisons** (both sides identical, e.g. `x <= x`) together with the boolean literal `true`.

### 6.3 Verification / Consistency Codes (IFP3xxx)

Produced by [`smt.py`](../../src/ifp/smt.py):

| Code | Meaning | Level |
| --- | --- | --- |
| IFP3001 | The set of Invariants contradicts itself; no legal state exists (rule contradiction) | error |
| IFP3002 | The action precondition conflicts with the Invariants and will never be triggered (action not triggerable) | error |
| IFP3003 | The Intent cannot undergo SMT encoding / consistency checking (e.g. contains a String field, or a ValueObject circular reference) | warning |
| IFP3010 | Property Test solving timeout (exceeds `timeout_ms`) | warning |

> A Counterexample itself is not a Diagnostic, but an independent object in `VerifyReport.counterexamples`, carrying baseline marks.
>
> Note: ValueObject fields (such as `Money`) are **Inline Expanded** and participate in solving, and no longer trigger IFP3003; this code is reported only when the ValueObject contains a circular reference, or an unencodable top-level `String` field appears.

---

## 7. Baseline Marking Standard

1. **Only core fund and compliance Invariants** may be marked `baseline`.
2. Modifying an `invariant` that is marked as baseline **automatically triggers a mandatory approval** process (see [§11 Semantic Diff and Change Risk](#11-semantic-diff-and-change-risk)).
3. Baseline comments must state the risk description (`risk`) and the business owner (`owner`); a missing `owner` is directly judged as error (IFP2001).
4. **Do not abuse baseline**: ordinary non-core rules should not be marked `baseline`; otherwise every tiny modification must go through re-approval, dragging down iteration.

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

## 8. Cross-domain Contract Standard

1. `Contract` is an external **minimal interface contract**; it only exposes the properties and operations needed by external domains, hiding internal implementation details.
2. Cross-domain callers depend on the `Contract`, and do not directly depend on the other party's Intent.
3. A `Contract` change triggers re-validation of dependent models; contracts are managed with versioning.
4. Preconditions/postconditions should be declared in the contract, to guarantee the legality of cross-domain interaction.
5. `expose` can only expose fields **already defined** in the context; otherwise it reports IFP1022.

---

## 9. Scenario Writing Standard

1. `Scenario` is used to verify positive and routine exception flows, with manually constructed sequences of business operations.
2. Each scenario should be commented: scenario purpose, business source.
3. Cover three kinds of flows: **normal flow, boundary positive flow, illegal-operation interception (`rejected`)**.
4. `Scenario` is not used for large-scale boundary mining; large-scale boundaries are left to `PropertyTest`.
5. Include at least one `rejected` step, otherwise Lint reports IFP2007 (info).

```text
scenario AuditRejectedWhenNotPending {
    context: RefundContext
    intent: RefundOrder
    given {
        orderPaidAmount = 100
        totalRefundedAmount = 0
        status = RefundStatus::Approved
    }
    rejected Audit()   // initiating an audit while not in the pending-review state must be intercepted
}
```

---

## 10. PropertyTest Configuration Standard

1. A Property Test automatically searches for Counterexamples, verifying that `Invariant` always holds under any legal operation sequence.
2. The search **starts from the Intent's initial state** (the `initial` declaration + field default values determine the starting point), and looks by "action step count from short to long" for the **shortest operation sequence** that **breaks an Invariant**; a Counterexample gives the full action sequence and the state before and after each step.
3. Distinguish light / full modes: the CI pipeline runs only light; local or nightly tasks run full.
4. Set resource limits: `timeout_ms`, numeric lower/upper bounds (`lower`/`upper`), to prevent the solver from occupying resources for a long time.
5. After a Counterexample is found, the modeler's first step is to distinguish three cases:
   - ① A real business loophole → modify the business rule / model;
   - ② A model expression error (DSL written wrong) → fix the model expression;
   - ③ A solver false positive / over-tight constraint → adjust the model boundary.
6. All Counterexamples must be kept on record and written into the review record.

Configuration example ([`examples/refund/property_test.yaml`](../../examples/refund/property_test.yaml)):

```yaml
property_test:
  intent: RefundOrder      # only validate this Intent; if left empty, validate all
  lower: 0                 # lower bound of numeric variables
  upper: 100000            # upper bound of numeric variables (avoid unbounded integers causing long solving time)
  timeout_ms: 5000         # single solving timeout (milliseconds)
  mode: light              # step level: single(1) | light(2) | full(3)
  max_steps: 2             # explicitly override the max step count (takes precedence over mode)
  invariants: []           # only validate the specified Invariants; empty means all
```

### 10.1 Step Levels and Timeout Degradation

| Level | Max action steps | Applicable scenario |
| --- | --- | --- |
| `single` | 1 | Fast regression, only finds shallow loopholes where "one action breaks an Invariant" |
| `light` | 2 | CI default; covers common loopholes that require "first set up the precondition state, then trigger" (e.g. audit first, then refund) |
| `full` | 3 | Local / nightly tasks; deeper reachable paths, higher time cost |

- If `max_steps` is explicitly configured, it overrides the `mode` level (taking `max(1, max_steps)`).
- The larger the step count, the more action sequences must be enumerated, and the solving time grows rapidly; it is recommended to use `light` in CI and `full` for nightly tasks.
- **Timeout degradation**: when a single solve exceeds `timeout_ms`, it is recorded as [`IFP3010`](#6-diagnostic-code-overview) (warning) and that target is skipped, **without throwing an exception or blocking the pipeline**; CI only needs to care about error-level diagnostics and actual Counterexamples.

---

## 11. Semantic Diff and Change Risk

Provided by [`diff.py`](../../src/ifp/diff.py) and [`baseline.py`](../../src/ifp/baseline.py). Semantic Diff compares the **business rule structure** (context, Intent, fields, StateMachine, Invariants, actions, contracts, scenarios), not source-code characters.

### 11.1 Risk Scoring Rules

Each change is weighted and accumulated by category, capped at 100:

| Change category | Base weight | Additional rule |
| --- | --- | --- |
| context (context added/removed) | 25 | `removed` adds another +10 |
| invariant (Invariant) | 25 | a Baseline Invariant change adds another +40 |
| contract (Contract) | 20 | `removed` adds another +10 |
| action (action, including `pre`/`post`) | 15 | `removed` adds another +10 |
| state (StateMachine) | 15 | `removed` adds another +10 |
| field (field) | 10 | `removed` adds another +10 |
| scenario (Scenario) | 5 | `removed` adds another +10 |
| description (description) | 0 | — |
| other | 5 | — |

- A change item carrying a **baseline mark** (only Invariants may carry one) adds +40 to that item.
- **Deletion-type changes** (`removed`) add another +10.

### 11.2 Mandatory Approval Determination

Meeting either of the following requires mandatory approval (the CLI outputs "mandatory approval required: yes"):

1. A **baseline change** exists (`has_baseline_change`); or
2. The **risk score ≥ 40** (`APPROVAL_THRESHOLD`).

Therefore, any modification involving a Baseline Invariant, or a change with a sufficiently high accumulated risk, must undergo manual approval and be archived.

---

## 12. Model Review Process

Model review is done in two rounds: **business review** + **technical modeling review**.

1. **Business review**: business/product confirms whether the Invariants, states, and business logic conform to the business intent (see [Business-side Training · How to Participate in Model Review](training-business.md#10-how-to-participate-in-model-review)).
2. **Technical modeling review** (modeler/developer):
   - Whether the aggregate and BoundedContext division is reasonable;
   - DSL expression correctness and type safety;
   - SMT solving performance, and whether there is a solving timeout risk;
   - Whether the `baseline` mark is reasonable and whether it is abused;
   - `Scenario` and `PropertyTest` coverage;
   - Model comment completeness.
3. Use **Semantic Diff** to review the scope of impact of this change; a baseline change automatically escalates the review level.

---

## 13. CI Pipeline Integration Standard

Refer to the example in [`ci.yml`](../../.github/workflows/ci.yml):

1. On model commit, automatically run: type checking + Lint + light Property Test + Semantic Diff.
2. A failed validation must not be merged into the model repository.
3. Artifact export: after the model passes, automatically export artifacts such as OpenAPI / Protobuf / Markdown reports.
4. Establish an association between the model version and the code version for traceability, ensuring model-code alignment.

### 13.1 Semantic Gate (PR Model Change Risk)

At the PR stage, the base branch's model is automatically compared with the PR's model, using the **risk score** and **approval determination** from Section 11 as a merge gate:

```bash
# check out the base branch model into base/, then do a Semantic Diff against the current model
ifp diff base/examples/refund examples/refund \
  --github \
  --json out/semantic-gate.json \
  --audit out/audit.jsonl
```

- `ifp diff` accepts **files or directories**; directories are matched file by file by relative path, and the change is annotated with the model file it belongs to.
- `--json` outputs a machine-readable report (risk score, threshold, `approval_required`, change list) for the pipeline to read and archive.
- `--github` outputs GitHub Actions Annotations, and writes `risk_score` / `approval_required` to the job output (`$GITHUB_OUTPUT`).
- `--threshold` can override the default approval threshold of 40; `--fail-on-approval` exits with a non-zero code when "mandatory approval required", used as a **hard gate** to block the merge.
- Exit code conventions: `0`=no approval required; `1`=mandatory approval required (only with `--fail-on-approval`); `2`=path or usage error; `3`=principal has insufficient permissions (when `--actor` is specified, see 13.2).
- The Audit Log (JSONL) and gate report are archived as artifacts along with the pipeline, and are included in the audit evidence scope of Section 6.

The command sequence to run end-to-end in CI:

```bash
ifp check examples/refund/          # type checking + Lint
ifp simulate examples/refund/       # scenario simulation
ifp verify examples/refund/refund.ifp   # Property Test (the refund-domain example will find Counterexamples as expected and return non-zero)
ifp export examples/refund/refund.ifp --format openapi -o out/refund.openapi.json
ifp diff base/examples/refund examples/refund --fail-on-approval  # semantic gate
```

### 13.2 Permission Gate (RBAC)

The approval determination in 13.1 answers "whether approval is needed"; RBAC answers "who has the right to approve". The platform distinguishes four roles with non-overlapping permissions:

| Role | Name | Permission |
| --- | --- | --- |
| `modeler` | Modeling submission | `submit_model` |
| `reviewer` | Review | `review_model` |
| `approver` | Approval (including baseline change) | `approve_change`, `approve_baseline` |
| `auditor` | Read-only audit | `read_audit` |

Merge determination rules:

- The change **does not touch the baseline** and the risk score is below the threshold → no approval required, and any role may merge;
- The change's risk score reaches the threshold but it **does not touch the baseline** → requires `approve_change`;
- The change **touches the baseline** (addition/deletion/modification of a Baseline Invariant, or a change of the baseline mark) → requires `approve_baseline`.

Specifying the principal on `ifp diff` enables the determination:

```bash
ifp diff base/examples/refund examples/refund \
  --actor alice --actor-roles modeler \
  --audit out/audit.jsonl
```

- `--actor` is the principal performing the merge, and `--actor-roles` is a comma-separated list of role names; if not specified, RBAC is not enabled and the behavior is the same as 13.1.
- The same principal may hold multiple roles, and permissions are the union.
- If permissions are insufficient, it ends with exit code `3`, and appends an `rbac.decision` record to the Audit Log (principal, roles, required permissions, determination result and risk score), included in the audit evidence scope of Section 6.
- You can use `ifp rbac` to view the role and permission matrix.

### 13.3 Editor Diagnostics (LSP)

Modeling mostly happens in an editor, so the platform ships a built-in language server that moves the same checks into the IDE:

```bash
ifp lsp --stdio
```

- The language server reuses the diagnostics of `typecheck` and `lint` (IFP0001 / IFP1xxx / IFP2xxx) and pushes them via LSP `textDocument/publishDiagnostics`;
- Documents open in the editor and every `.ifp` on disk in the workspace are merged into a single project model, so cross-file references (Context / Intent / Scenario) do not break depending on whether a file is saved;
- Beyond diagnostics and the structural outline (`textDocument/documentSymbol`), it also provides **go-to-definition** (`textDocument/definition`): jumping from references such as `State::Member`, field types, action calls in scenarios and intents to the declaration, including across files; and **quick fixes** (`textDocument/codeAction`): adding the missing `owner` / `risk` to a baseline invariant, declaring the missing `initial` state, or scaffolding a value object for an unknown field type;
- The reference client is [`editors/vscode`](../../editors/vscode): opening `.ifp` gives syntax highlighting, live diagnostics, navigation and quick fixes; the `ifp.server.command` / `ifp.server.args` settings can point at the `ifp` inside a virtualenv.

---

## 14. Model Maintenance and Version Management

1. The model repository uses Git, and each commit carries a business change description.
2. Write migration notes for major version changes; perform a version compatibility assessment for `Contract` changes.
3. Regular model inspection: clean up dead Invariants, expired scenarios, and abandoned fields.
4. Regularly audit the alignment between the model and the online code, to prevent the model from drifting away from the implementation.

---

## 15. Common Modeling Pitfalls Checklist

1. Writing too many non-hard constraints as Invariants, causing solver explosion and making the business unlandable.
2. Dividing the aggregate too large, so a single Intent carries too much business, the state combinations explode, and the Property Test cannot run.
3. Expressing funds with floating point, where precision loss causes false Counterexamples (should uniformly use `Decimal`).
4. Reading the internal properties of another domain's Intent directly across domains, breaking domain isolation and rendering Semantic Diff ineffective.
5. Baseline marks flooding, so every tiny modification must go through a re-approval process, dragging down iteration efficiency.
6. Writing business-exemptible special cases into Invariants; special cases should use Rule Exception and not modify the main Invariant.
7. Writing only positive cases in a Scenario, without validating illegal-operation interception logic (should include `rejected`).
8. A StateMachine declares states other than `initial` that no action can reach (unreachable state, IFP2003).

---

## 16. Deliverables Checklist (Mandatory Output on Modeling Completion)

1. `.ifp` model source code;
2. Model comment documentation (`description` / `note`);
3. A set of `Scenario` simulation scenarios;
4. Property Test configuration file (`property_test.yaml`);
5. Model review record (business + technical review);
6. Export artifacts (OpenAPI / Protobuf / Markdown reports, etc.);
7. Baseline change approval record (if there is any baseline modification).
