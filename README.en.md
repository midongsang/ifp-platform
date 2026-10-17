# IFP · Intention Formal Platform

> [中文](README.md) · **English**

Business rules usually live in PRDs, chat logs and code comments, and by the time a real
contradiction surfaces it is often already in production. IFP collects them into one model
that can be written, queried and verified.

[![CI](https://github.com/midongsang/ifp-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/midongsang/ifp-platform/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-v0.1.0-blue.svg)](#status)

Repository: [github.com/midongsang/ifp-platform](https://github.com/midongsang/ifp-platform) ·
package `ifp-platform` · CLI `ifp`

## What it is

IFP (Intention Formal Platform) is a formal modeling toolkit for business domains: you describe
entities, state transitions and constraints in a readable `.ifp` language, and the platform
encodes that model as SMT constraints and automatically searches for counterexamples — cases
where the preconditions hold yet a business constraint breaks.

It does not replace business code, and it is not an online rule engine. The problem it targets is
narrow and concrete: rule conflicts, boundary gaps, and the financial or compliance risk that
used to surface only after an incident. IFP aims to catch them before merge.

## What it does

- **Modeling**: bounded contexts, Intents, value objects, state machines, invariants, pre/post conditions, cross-context contracts ([grammar.lark](src/ifp/grammar.lark))
- **Checking**: field / enum / type safety plus modeling-standard lint; money is always `Decimal` ([typecheck.py](src/ifp/typecheck.py), [lint.py](src/ifp/lint.py))
- **Verification**: SMT counterexample search that starts from the initial state and looks for the shortest path breaking an invariant, shortest step count first ([smt.py](src/ifp/smt.py))
- **Simulation**: hand-written action sequences covering the happy path, boundaries and rejected operations ([simulate.py](src/ifp/simulate.py))
- **Governance**: semantic diff, risk scoring and approval decision for baseline changes, hash-chain audit log, and role-based access control ([diff.py](src/ifp/diff.py), [baseline.py](src/ifp/baseline.py), [audit.py](src/ifp/audit.py), [rbac.py](src/ifp/rbac.py))
- **Collaboration**: a Git-compatible model repository (multi-version storage plus business-change linkage), three-way merge over business semantics, model metrics, and model-versus-code version alignment auditing ([repo.py](src/ifp/repo.py), [semantic_merge.py](src/ifp/semantic_merge.py), [metrics.py](src/ifp/metrics.py))
- **Export**: OpenAPI / Protobuf / Markdown / JSON, plus a plugin hook for custom linters and exporters ([exporter.py](src/ifp/exporter.py), [plugin.py](src/ifp/plugin.py))
- **Onboarding and updates**: `ifp doctor` self-checks the environment; `ifp update` reads releases from the source repository and updates only when you choose (showing release time and asset size); `ifp config` manages local preferences ([updater.py](src/ifp/updater.py), [doctor.py](src/ifp/doctor.py), [prefs.py](src/ifp/prefs.py))

## Getting started

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ifp check examples/refund/                    # type check + lint
ifp verify examples/refund/refund.ifp          # property test: search for counterexamples (the sample exits non-zero on purpose)
ifp simulate examples/refund/                  # scenario simulation
ifp diff base/examples/refund examples/refund --fail-on-approval   # semantic diff / CI gate
ifp export examples/refund/refund.ifp --format openapi -o out/refund.openapi.json
ifp rbac                                        # role and permission matrix
ifp metrics examples/refund/                    # model metrics: rule coverage / dead invariants / baseline count
ifp align examples/refund                       # model-versus-code version alignment audit
ifp lsp                                         # language server (stdio) for editor diagnostics / navigation / quick fixes
ifp web                                         # web editor: edit and visualize in the browser
ifp doctor                                      # environment self-check: dependencies, resources, toolchain
ifp update --check                              # check the source repo releases (shows release time and size)
```

`ifp diff` takes files or directories: `--json` writes a machine-readable report, `--github`
writes job outputs, and `--fail-on-approval` returns non-zero when approval is mandatory, so it
can serve directly as a PR gate. Adding `--actor` / `--actor-roles` enables an RBAC decision —
a baseline change requires the `approve_baseline` permission of the `approver` role, an
unauthorized actor is blocked with exit code 3, and the decision is appended to the audit log.

Model repository and semantic merge:

```bash
ifp repo init mymodels                           # initialize a Git-compatible model repository
# after placing .ifp source under mymodels/:
ifp repo commit mymodels mymodels/refund.ifp \
    -m "Refund limit becomes cumulative" --business-change "From per-refund to cumulative limit" --code-revision a1b2c3d
ifp repo log mymodels                            # commit history with linked business changes
ifp merge base/refund.ifp ours.ifp theirs.ifp    # three-way merge over business semantics (conflicts force manual review)
ifp align mymodels --code-revision a1b2c3d        # model-versus-code version alignment audit
```

Onboarding and updates:

```bash
ifp doctor                                       # environment self-check: dependencies, resources, toolchain
ifp update --check                               # check only: latest release, release time and asset size
ifp update                                       # confirm before installing; --yes skips the prompt, --json for scripts
ifp update --auto                                # enable the check on `ifp version`; --no-auto turns it off
ifp config set update.source acme/ifp            # point at your own release repository
```

`ifp update` only performs a read-only check, showing release time and asset size, and installs
only after you confirm. Offline or restricted networks produce a readable message instead of
breaking your session, and `IFP_NO_UPDATE_CHECK=1` disables the check entirely.

## A refund scenario

```text
context RefundContext {
    description: "电商退款限界上下文"

    intent RefundOrder {
        field orderPaidAmount: Decimal
        field totalRefundedAmount: Decimal = 0
        field status: RefundStatus = RefundStatus::Pending

        state RefundStatus { Pending, Approved, Refunded, Rejected }
        initial: RefundStatus::Pending

        // core money invariant: refunds must not exceed what the order paid
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

`ExecuteRefund` only checks `status` and `amount >= 0` in its precondition and forgets that a
refund must not push the cumulative total past what the order paid. Nobody has to construct that
gap by hand — `ifp verify` finds it in two steps:

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

After "approve → refund" the cumulative refund exceeds what the order paid. That is a real
financial loss that has happened in production, and IFP points it out before any code is written.
(CLI output is currently Chinese; the blocks above are pasted verbatim.)

## Directory layout

```text
ifp-platform/
├── src/ifp/            # platform core (parsing / checking / verification / governance / export)
├── examples/refund/    # refund domain sample: model + scenarios + property-test config
├── specs/              # IFP DSL grammar reference
├── docs/               # modeling standard, glossary, training and ROI docs (Chinese)
├── docs/en/            # English editions of the docs
├── tests/              # pytest suite
└── .github/workflows/  # CI pipeline
```

## Documentation

English editions:

- [IFP DSL Specification](docs/en/dsl-spec.md)
- [IFP Modeling Standard](docs/en/modeling-standard.md)
- [IFP Glossary (English–Chinese)](docs/en/glossary.md)
- [Business Training Material](docs/en/training-business.md)
- [ROI Template](docs/en/roi-template.md)
- [Roadmap](docs/en/roadmap.md)

Chinese originals: [DSL 语法规范](docs/dsl-spec.md) · [建模规范](docs/modeling-standard.md) ·
[术语表](docs/glossary.md) · [业务侧培训](docs/training-business.md) ·
[ROI 模板](docs/roi-template.md) · [路线图](docs/roadmap.md)

## Status

Currently `v0.1.0` (the first release). The `parse → check → verify → simulate → diff → export`
pipeline is wired end to end and the refund sample reproduces on the command line.

- **Verification**: SMT counterexample search expands value-object fields inline and searches
  multi-step paths (with `single` / `light` / `full` profiles and solver-timeout degradation);
  semantic diff and baseline approval decisions feed a CI gate that runs on every PR and archives
  the audit log.
- **Governance and collaboration**: four roles — modeler, reviewer, approver, auditor — where a
  baseline change needs the `approve_baseline` permission to merge and every decision lands in
  the audit log; a Git-compatible model repository links multiple versions of `.ifp` source to
  business changes and code revisions (`ifp repo`); semantic merge performs a three-way comparison
  per business-rule unit, traces conflicts to the specific rule, and forces manual review with an
  audit trail when it cannot auto-resolve (`ifp merge`); model metrics and model-versus-code
  alignment auditing come from `ifp metrics` and `ifp align`.
- **Editor and visualization**: the built-in LSP (`ifp lsp --stdio`) provides `.ifp` syntax
  highlighting, a structural outline, live diagnostics (reusing the typecheck / lint diagnostic
  codes), **go-to-definition** (`textDocument/definition`, resolving `State::Member`, field types,
  action calls in scenarios and intents, including across files) and **quick fixes**
  (`textDocument/codeAction`, such as adding the missing `owner` / `risk` to a baseline invariant,
  declaring the missing `initial` state, or scaffolding a value object for an unknown field type);
  the VS Code extension surfaces the same capabilities; and the web editor (`ifp web`) lets you
  edit online, see diagnostics in real time, view the state machine and invariants graphically,
  and trigger property testing from the browser.
- **Onboarding and updates**: `ifp update` reads releases straight from the source repository and
  shows the version, release time and asset size, letting you decide whether to update (`--check`
  inspects without installing, `--auto` controls whether `ifp version` checks automatically, and
  `ifp config` switches the update source); it relies on the standard library only and tolerates
  offline or proxied environments. Pushing a `v*` tag has CI build and publish a GitHub Release
  (with generated notes plus sdist / wheel assets); when the source repository has no releases the
  update check falls back to its tags, and a release without an asset falls back to installing
  from the tag. `ifp doctor` self-checks the Python version, dependencies, bundled resources and
  toolchain.

See the [roadmap](docs/en/roadmap.md) for what comes next.

### Versioning

Version numbers follow `0.<default-iteration>.<patch>`:

- The leading `0` marks a formal release;
- the second number is the **default iteration**, incremented as features progress;
- the third number is the **patch** number, for bug fixes or some minor issues that are not
  convenient to spell out.

`0.1.0` is the first release of this project.

## Contributing

Issues and PRs are welcome. Before you start, read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[modeling standard](docs/en/modeling-standard.md).

## License

[Apache License 2.0](LICENSE).
