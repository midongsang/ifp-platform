# IFP Business Training Material (Product / Business Analysts)

> Chinese version: [training-business.md](../training-business.md)

> Audience: business and product analysts; no need to understand underlying technologies such as SMT or AST.
> Focus: how to use IFP to solve everyday business-rule pain points, what modeling looks like, what the deliverables are, and how to take part in reviews.
> Suggested length: 60 minutes. Companion material: [Core Concepts](#6-ifp-core-concepts-in-plain-business-terms) · [refund domain example](../../examples/refund/refund.ifp) · [DSL Specification](dsl-spec.md)

---

## 1. The Business-Rule Pain Points We Face Today

- A requirements document writes a long paragraph of text, but product, engineering and QA each read the same rule differently — and problems show up once it goes live.
- Boundary cases and scenarios where many conditions stack up are hard to think through completely; many gaps only surface after launch.
- After a rule changes, nobody knows which existing logic it affects, so change risk is judged purely on human experience.
- When an audit comes, you have to hunt everywhere for the basis of a rule and its change history; assembling the materials takes a lot of time.
- Looking back at past financial-loss post-mortems, the root cause is usually ambiguous rule wording, or a missed combined boundary case.

Natural language is good for communication, but it is not suitable as the sole baseline for high-risk financial rules — and that is exactly where the problem lies.

---

## 2. Why Formalize Business Rules

Writing business rules as a structured, machine-verifiable model buys you several things:

1. **Disambiguation**: product, business, engineering and audit all use the same rule definitions.
2. **Risk up front**: before any coding, a solver engine searches for rule conflicts and boundary gaps, reducing post-launch financial loss.
3. **Controllable change**: use Semantic Diff to see clearly what actually changed at the "business-rule level"; changes to core baseline rules require approval.
4. **Lower audit cost**: change and verification processes leave traces automatically, saving manual evidence-gathering effort.
5. **More efficient testing**: boundary cases that people easily miss are filled in automatically.

---

## 3. What IFP Is (Business Perspective)

IFP stands for Intention Formal Platform.

It is not code, nor an online rule engine. It is a precise specification of your business rules, written in a form machines can read, so a machine can check it for contradictions and gaps on your behalf.

Compare it with a Word requirements document:

| | Word requirements document | IFP model |
| --- | --- | --- |
| Form of expression | Natural language | Structured business description |
| People can read it | Yes | Yes |
| Machine can automatically check rule conflicts / boundary cases | No | Yes |
| Can it serve as a trusted rule baseline | Prone to going stale and to ambiguity | Verifiable, auditable |

It mainly does three things: it writes business intent clearly as a requirements baseline, it lets machines search for gaps such as rule conflicts and excess funds, and it records rule-change history and automatically preserves audit evidence. It does not write business code, does not replace developers in writing logic, and does not go live to execute business operations.

---

## 4. What IFP Can and Cannot Do

**Can do**

1. Unify business rules and remove textual ambiguity, serving as a requirements baseline;
2. Define business entities, state transitions, and money / quantity constraints (Invariants);
3. Automatically search boundary scenarios and find gaps people would not think of;
4. Compare two rule changes to see what actually changed in the business logic (Semantic Diff);
5. Set baselines on core rules, require approval for changes, and leave a full trail;
6. Simulate business scenarios and rehearse whether a multi-step operation violates anything;
7. Automatically output audit logs, lowering evidence-gathering cost.

**Cannot do**

1. It cannot replace developers in writing business code, and it will not go live to run transactions;
2. It cannot find pure code bugs (such as database operations or network failures);
3. For simple CRUD business, the return on investment is low, so modeling is not mandatory;
4. It does not think through business logic for the business folks; it only assists with verification.

---

## 5. Modeling Delivery Process (Business Involvement Marked in Bold)

1. Business requirements analysis [**led by business/product**]: work out the business objects, state transitions, hard constraints and actions.
2. Write the IFP model [product + business analyst + modeler collaboration]: translate natural language into a structured model.
3. Model static check (automatic): the platform runs type checking + Lint to validate syntax and basic conventions.
4. Scenario simulation verification [**business review**]: run the hand-written scenarios and confirm the behavior matches business expectations.
5. Property Test automatically finds gaps (automatic): the machine walks through state combinations and outputs counterexamples; business decides whether a counterexample is a real gap.
6. Model review [**business must take part in the review**]: review whether the Invariants, StateMachines and business constraints match real business intent.
7. Submit, and baseline approval (if baseline rules are involved).
8. Merge into the repository and hook into CI; subsequent development uses the model as the requirements baseline.
9. Retain model versions; the audit log records changes automatically.

It is worth stressing: model review is the most important responsibility for business folks — confirming that the model faithfully reproduces the business rules.

---

## 6. IFP Core Concepts (In Plain Business Terms)

- **BoundedContext**: a major business module, such as the refund domain or the settlement domain, used to draw business boundaries.
- **Intent (aggregate root)**: the core business object, for example a refund order or an order; it describes the object's states and constraints.
- **ValueObject**: attached information with no independent lifecycle, such as an amount or an address.
- **StateMachine**: which states an object can have and how it may move between them (refund order: pending review → approved → refund completed).
- **Invariant (the most important)**: a hard business constraint that must never be broken. For example, "the total refund amount must not exceed the order's total paid amount"; as long as this Invariant exists, the machine will look for scenarios that break it.
- **Precondition (Pre)**: the condition that must hold before an operation is executed; if it does not hold, the operation cannot run.
- **Postcondition (Post)**: the result that must be guaranteed after the operation completes.
- **Scenario**: a hand-written business-process use case used to verify model behavior.
- **Property Test**: the machine automatically combines conditions to find counterexamples that violate an Invariant.
- **Baseline**: core high-risk Invariants whose changes require separate approval.
- **Semantic Diff**: compare model changes to see what changed at the business-rule level, not just the textual difference.

The core work for business folks is to clearly work out the entities, states, Invariants, and pre/post conditions. For a fuller glossary, see [Glossary (English–Chinese)](glossary.md).

---

## 7. How to Read an `.ifp` Model

An `.ifp` file writes business rules in a structure close to natural language. Taking the refund domain as an example, you can read it as a layered specification:

```text
context RefundContext {              // ← major business module: the refund domain
    description: "电商退款限界上下文……"

    intent RefundOrder {             // ← core business object: the refund aggregate
        field orderPaidAmount: Decimal        // order paid amount
        field totalRefundedAmount: Decimal = 0 // cumulative refunded amount, defaults to 0
        field status: RefundStatus = RefundStatus::Pending // current state: pending review

        state RefundStatus {         // ← StateMachine: which states a refund order has
            Pending, Approved, Refunded, Rejected
        }
        initial: RefundStatus::Pending   // ← initial state: pending review

        invariant totalRefundNotExceedPaid {  // ← Invariant: a hard constraint
            expr: totalRefundedAmount <= orderPaidAmount
            baseline: true                    // marked as baseline: changes require approval
            risk: "累计退款超过订单实付金额，造成超额退款资金损失"
            owner: "finance-team"
        }

        action ExecuteRefund(amount: Decimal) {   // ← business action: execute refund
            transition: RefundStatus::Refunded
            pre: status == RefundStatus::Approved && amount >= 0  // precondition
            post: status == RefundStatus::Refunded                // postcondition
                  && totalRefundedAmount == old(totalRefundedAmount) + amount
        }
    }
}
```

When business folks read a model, it is enough to focus on four things:

1. **Entities and fields**: what business information does this object carry?
2. **StateMachine**: which states are there, and how may it move between them?
3. **Invariant**: which hard constraints must never be broken? (the key one)
4. **Action Pre/Post**: what must hold before each operation, and what is guaranteed after it?

Leave syntax details such as commas, parentheses and expression style to the modeler and the platform's static check (see [DSL Specification](dsl-spec.md)).

---

## 8. Case Study: The Full Refund-Domain Modeling Process (Business Perspective)

Background: an order supports multiple partial refunds, and historically the stacking of multiple partial refunds has caused over-refund financial loss.

There are three business constraints:

1. The order's cumulative refund amount ≤ the order's paid amount (core Invariant, set as a baseline).
2. Only a refund order in "pending review" status can start the review operation.
3. Only after approval can the money-refund action be executed.

Modeling outputs:

| Output | Content |
| --- | --- |
| Intent | the refund aggregate (order + multiple refund orders) `RefundOrder` |
| StateMachine | refund-order state transitions `Pending → Approved → Refunded` (illegal transitions are blocked) |
| Invariant | cumulative refund total ≤ order paid amount (baseline) |
| Pre | review-operation precondition: refund-order status = pending review |
| Scenario | one partial refund, boundary full refund, illegal-operation-blocked scenario |

The result of the platform running the Property Test — this is exactly the financial-loss scenario that really happened in the past:

```text
反例 ExecuteRefund（2 步）
    不变量 totalRefundNotExceedPaid [BASELINE] 可被多步操作序列打破：
        步骤 1: Audit()
            动作前状态: orderPaidAmount=1, totalRefundedAmount=0, status=Pending
            动作后状态: orderPaidAmount=1, totalRefundedAmount=0, status=Approved
        步骤 2: ExecuteRefund(amount=2)
            动作前状态: orderPaidAmount=1, totalRefundedAmount=0, status=Approved
            动作后状态: orderPaidAmount=1, totalRefundedAmount=2, status=Refunded
        => 第 2 步执行后不变量 totalRefundNotExceedPaid 不成立
```

(CLI output is currently Chinese; the block above is pasted verbatim.)

Business identifies this as a gap and fixes it during the requirements stage, rather than waiting to step on it after launch.

---

## 9. How to Run It from the Command Line (`ifp check` / `verify` / `simulate`)

After installation (see [README Getting started](../../README.en.md#getting-started)), run these from the repository root:

```bash
# ① type check + Lint: is the syntax and modeling standard valid
ifp check examples/refund/

# ② property test: automatically search for counterexamples that violate invariants
ifp verify examples/refund/refund.ifp

# ③ scenario simulation: run the hand-written business use cases
ifp simulate examples/refund/
```

How to read the results:

| Command | What the output means | What business folks care about |
| --- | --- | --- |
| `ifp check` | Lists each `error` / `warning` (with a diagnostic code such as `IFP2001`) and totals the counts | Whether there are convention problems such as a rule missing an owner or a risk description |
| `ifp verify` | Prints the **counterexamples** it finds (pre-action state → action executed → post-action state; a full path when there are multiple steps) | **Is a counterexample a real business gap**: if so, change the requirements/model |
| `ifp simulate` | Outputs `[PASS]` / `[FAIL]` per scenario along with the failure reason | Whether the business expectations for the happy path / boundaries / illegal-operation blocking match the model |

Additional commands:

- `ifp simulate examples/refund/ --name PartialRefundOnce`: run only the named scenario.
- `ifp verify examples/refund/refund.ifp --intent RefundOrder`: verify only the named Intent.
- `ifp baseline examples/refund/refund.ifp`: list all baseline Invariants (who owns them, what the risk is).

For the refund-domain example, `ifp verify` is expected to find counterexamples and exit with a non-zero code — which is exactly where the platform's value lies: it finds the gap before any coding. `ifp check` and `ifp simulate` should all pass.

---

## 10. How to Take Part in Model Review

Model review checklist (business edition): focus only on the items below; no need to look at the underlying expression syntax:

1. Do the business entity and object definitions match the real business?
2. Are the state set and state-transition rules correct? Are any allowed/forbidden transitions missing?
3. **Are the Invariants complete — is any core money/compliance constraint missing?** (the most important)
4. Does each operation's precondition and postcondition meet the business requirements?
5. Do the Scenarios cover the happy path, ordinary exceptions, and illegal-operation blocking?
6. Are baseline markings correct: have high-risk money constraints been marked as baselines?
7. Do the rules conflict with other business domains? Are the cross-domain Contracts aligned?

Don't fuss over whether the DSL syntax is right or wrong; syntax issues are handled by the modelers and the platform's static check. Business review answers only one question: does the business intent the model describes match the real business rules.

(For review-process details, see [Modeling Standard · Model Review Process](modeling-standard.md#12-model-review-process).)

---

## 11. Common Misconceptions & Pitfall Checklist

| Misconception | Correction |
| --- | --- |
| Once the IFP model is written, no requirements document is needed | The IFP model is a **trusted rule baseline**; the requirements document still stays, and the two complement each other |
| All business rules must be modeled | Prioritize only **high-risk domains such as money and compliance**; modeling simple low-risk business is not recommended |
| Modeling is the developers' job; business just needs to raise requirements | Invariants and business constraints can only come from business/product, and business is the final owner of rule truthfulness |
| As long as the model passes checking, nothing will ever go wrong in production | IFP only guarantees there are no gaps at the **business-rule intent level**; code bugs and infrastructure failures are out of scope |
| The more Invariants, the better | Write only hard constraints that must never be broken; exemptible special cases go through a "Rule Exception" approval instead of being written as Invariants |

---

## 12. Training Summary

1. The essence of IFP: a precise, machine-verifiable model of business rules, used to find financial-loss and compliance gaps up front.
2. Business folks' core outputs: business objects, states, Invariants, and pre/post constraints.
3. Business's core responsibility: work out the rules + take part in model review to confirm the model matches business intent.
4. Rollout strategy: pilot in a high-risk money domain first, start with a small case, and don't roll it out across everything at once.
5. Value: remove ambiguity, find boundary gaps up front, keep change risk under control, and lower audit cost.

For how to quantify ROI, see the [ROI Template](roi-template.md); for the capability outlook, see the [Roadmap](roadmap.md).
