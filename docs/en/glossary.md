# IFP Glossary (English–Chinese)

> Chinese version: [glossary.md](../glossary.md)

> Source: the complete set of IFP documentation (V1.1), Appendix J.
> Audience: modelers, developers, product/business analysts, auditors and reviewers.
> Related documents: [DSL Syntax Specification](dsl-spec.md) · [Modeling Standard](modeling-standard.md) · [Business Training Material](training-business.md)

The table below lists the core terms of the IFP platform with their Chinese equivalents and brief definitions. Wherever possible, the module location in which a concept appears in the code has been aligned with the implementation (see the [Implementation Index](#implementation-index) at the end of this document).

| English term | 中文 | Definition |
| --- | --- | --- |
| IFP | IFP | The name of this business modeling platform (Intention Formal Platform) |
| BoundedContext | 限界上下文 | A DDD concept that demarcates the boundary of a business domain: a set of highly cohesive entities, value objects and contracts |
| Intent | 意图/聚合根 | The core model unit of IFP, corresponding to a DDD aggregate root; describes entity state, state machine and invariants |
| ValueObject | 值对象 | Has no unique identity and is immutable; carries only attributes and its own constraints |
| Member Access | 成员访问 | References a value object's internal member in the form `field.member` (for example `balance.amount`) |
| Inline Expansion | 内联展开 | Expands a value object's fields into their underlying leaf fields, so that value object constraints can take part in SMT solving |
| Multi-step Search | 多步路径搜索 | Starting from an initial state, searches for an operation sequence made up of multiple actions in order to discover paths where an invariant is broken |
| Contract | 跨域契约 | The minimal service contract exposed by a bounded context for other domains to call |
| StateMachine | 状态机 | Defines an entity's set of legal states and the rules for state transitions |
| Invariant | 不变量 | A business constraint that must hold permanently whenever an entity is in a legal state |
| Precondition (Pre) | 前置条件 | An assertion that must hold before an interface executes; governs the legality of interface calls |
| Postcondition (Post) | 后置条件 | An assertion that the entity state must satisfy after the interface has finished executing |
| Baseline | 基线 | Marks the protected core invariants; modifying them requires a separate approval process |
| Rule Exception | 规则豁免 | An approved special case that allows a single entity to temporarily bypass an invariant, with an audit record retained |
| Scenario | 仿真场景 | A simulation test case that describes a sequence of entity operations and verifies model behavior |
| Property Test | 属性测试 | An automated search tool that generates state combinations and looks for counterexamples that violate invariants |
| Semantic Diff | 语义 Diff | Compares two model changes and identifies differences at the business-rule level rather than purely textual differences |
| Semantic Gate | 语义门禁 | Uses the Semantic Diff risk score and approval decision in CI / PR to block the merging of high-risk model changes |
| Model Lint | 模型静态检查 | Checks whether a model conforms to the modeling standard and best practices |
| Audit Log | 审计日志 | A tamper-proof, append-only record of operations, used for audit forensics |
| Sandbox | 沙箱 | An isolated runtime environment in which simulation and property testing are executed, with resource isolation |
| Exporter | 导出器 | Exports IFP models to other formats such as OpenAPI, Protobuf and reports |
| AST（Abstract Syntax Tree） | 抽象语法树 | The structured tree generated after source-code parsing, used for type checking and semantic analysis |
| SMT Solver | SMT 求解器 | The underlying constraint-solving kernel, used to find constraint contradictions and to search for counterexamples |
| Counterexample | 反例 | A set of instances that satisfy the model's preconditions but violate an invariant/property; represents a business vulnerability |
| Model Repository | 模型仓库 | Git-compatible storage that keeps multiple versions of IFP model source code |
| Semantic Merge | 语义合并 | When merging model branches, judges conflicts based on business semantics rather than merely merging text |
| Event | 事件 | A domain event that describes a business change occurring in an aggregate, used for event-sourcing modeling |
| Event Replay | 事件回放 | Replays a sequence of events in order to reconstruct aggregate state and verify invariants |
| Task Scheduler | 任务调度器 | Schedules simulation and property-testing tasks, including worker dispatch and resource control |
| Plugin SDK | 插件 SDK | A development kit for building custom Lint rules, exporters and simulation hooks |
| Risk Score | 风险评分 | A quantified risk value for model changes, used to judge approval priority |
| Model Metrics | 模型度量指标 | Model quality metrics: rule coverage, number of dead invariants, etc. |
| Dead Invariant | 死不变量 | A redundant invariant that always holds and produces no constraining effect |
| Type Check | 类型检查 | Verifies whether model expression types match, catching type errors |
| IFP DSL | 建模语言 | The IFP domain-specific modeling language, used by `.ifp` files |
| Artifact | 制品 | Products exported from a model: reports, schemas, code skeletons, etc. |
| RBAC（Role-Based Access Control） | 角色访问控制 | The platform's permission model; assigns project/platform permissions based on roles |
| API Key | API 密钥 | An authentication credential used by CI and external systems to call IFP interfaces |
| Workflow | 工作流 | The process for submitting, reviewing, approving and merging model changes |
| Model Review | 模型评审 | The review of IFP model changes at the business, syntax and rule levels |

---

## Implementation Index

The following terms already have a corresponding module in the current reference implementation (paths are relative to the repository root):

| Term | Implementation location |
| --- | --- |
| IFP DSL / AST（Abstract Syntax Tree） (抽象语法树) | [`src/ifp/grammar.lark`](../../src/ifp/grammar.lark), [`src/ifp/parser.py`](../../src/ifp/parser.py), [`src/ifp/ast.py`](../../src/ifp/ast.py) |
| Type Check | [`src/ifp/typecheck.py`](../../src/ifp/typecheck.py) |
| Model Lint (模型静态检查) | [`src/ifp/lint.py`](../../src/ifp/lint.py) |
| SMT Solver / Property Test / Counterexample (SMT 求解器 / 属性测试 / 反例) | [`src/ifp/smt.py`](../../src/ifp/smt.py) |
| Scenario | [`src/ifp/simulate.py`](../../src/ifp/simulate.py) |
| Semantic Diff (语义 Diff) | [`src/ifp/diff.py`](../../src/ifp/diff.py) |
| Semantic Gate | [`src/ifp/cli.py`](../../src/ifp/cli.py) (`ifp diff`), [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) |
| Baseline / Risk Score | [`src/ifp/baseline.py`](../../src/ifp/baseline.py) |
| Audit Log | [`src/ifp/audit.py`](../../src/ifp/audit.py) |
| RBAC（Role-Based Access Control） / Permission Point (角色访问控制 / 权限点) | [`src/ifp/rbac.py`](../../src/ifp/rbac.py) |
| Exporter / Artifact | [`src/ifp/exporter.py`](../../src/ifp/exporter.py) |
| Plugin SDK (插件 SDK) | [`src/ifp/plugin.py`](../../src/ifp/plugin.py) |
| Model Repository / Semantic Merge (模型仓库 / 语义合并) | [`src/ifp/repo.py`](../../src/ifp/repo.py), [`src/ifp/semantic_merge.py`](../../src/ifp/semantic_merge.py) |
| Sandbox / Task Scheduler (沙箱 / 任务调度器) | [`src/ifp/sandbox.py`](../../src/ifp/sandbox.py), [`src/ifp/scheduler.py`](../../src/ifp/scheduler.py) (`ifp run` / `--sandbox` in [`src/ifp/cli.py`](../../src/ifp/cli.py)) |

> Note: the terms "Event", "Event Replay", "API Key" and "Workflow" refer to capabilities that are planned for the platform; they are not yet provided by the current reference implementation. See the [Roadmap](roadmap.md) for details.
