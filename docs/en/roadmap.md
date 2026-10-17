# IFP Roadmap (ROADMAP)

> Chinese version: [roadmap.md](../roadmap.md)
> This roadmap is developed based on the capabilities listed in [README · Status](../../README.en.md#status), and is organized by milestone.
> Each milestone specifies a **goal** and its **acceptance criteria**; no specific dates are preset — delivery is judged by whether the acceptance criteria are met.
> Related documents: [DSL Syntax Specification](dsl-spec.md) · [Modeling Standard](modeling-standard.md) · [Glossary](glossary.md)

Current version: **v0.1.0** (the first release), with the complete pipeline `parse → check → verify → simulate → Diff → export` already working end to end; the refund-domain example reproduces on the command line.

---

## v0.1.0 — Reference Implementation (the first release, done)

**Goal**: Establish a minimal end-to-end closed loop that covers the main flow of "business rule modeling → machine verification → governance → collaboration → export", and get the refund-domain pilot working end to end.

| Completed Capabilities | Implementation Location |
| --- | --- |
| `.ifp` DSL parsing (grammar + AST + multi-file merging) | [`grammar.lark`](../../src/ifp/grammar.lark) · [`parser.py`](../../src/ifp/parser.py) · [`ast.py`](../../src/ifp/ast.py) |
| Type checking (IFP1001~IFP1034) | [`typecheck.py`](../../src/ifp/typecheck.py) |
| Model static analysis Lint (IFP2001~IFP2009) | [`lint.py`](../../src/ifp/lint.py) |
| Property testing (counterexample search + consistency checking IFP3001~IFP3010) | [`smt.py`](../../src/ifp/smt.py) |
| Inline expansion of ValueObject fields in SMT | [`smt.py`](../../src/ifp/smt.py) · [`typecheck.py`](../../src/ifp/typecheck.py) |
| Multi-step path counterexample search (`--steps` / `--mode` / `max_steps`, with timeout degradation) | [`smt.py`](../../src/ifp/smt.py) · [`cli.py`](../../src/ifp/cli.py) · [`config.py`](../../src/ifp/config.py) |
| Scenario simulation (positive / boundary / illegal interception) | [`simulate.py`](../../src/ifp/simulate.py) |
| Semantic Diff (directory matching / `--json` / `--github` / `--threshold` / `--fail-on-approval`) | [`diff.py`](../../src/ifp/diff.py) · [`cli.py`](../../src/ifp/cli.py) |
| Baseline governance and risk scoring | [`baseline.py`](../../src/ifp/baseline.py) |
| Audit Log (Hash Chain) | [`audit.py`](../../src/ifp/audit.py) |
| RBAC (four roles / five permission points) | [`rbac.py`](../../src/ifp/rbac.py) |
| Model repository (multi-version `.ifp` storage + business-change linkage + alignment audit) | [`repo.py`](../../src/ifp/repo.py) |
| Three-way merge over business semantics | [`semantic_merge.py`](../../src/ifp/semantic_merge.py) |
| Model metrics (rule coverage / dead invariants / baseline count) | [`metrics.py`](../../src/ifp/metrics.py) |
| Built-in LSP (diagnostics / structure outline / go-to-definition / quick fixes) | [`lsp.py`](../../src/ifp/lsp.py) |
| VS Code extension (highlighting / diagnostics / navigation / quick fixes / one-click verify·simulate) | [`editors/vscode/`](../../editors/vscode) |
| Web editor (edit online / visualize the state machine and invariants / property testing) | [`web.py`](../../src/ifp/web.py) · [`webassets/`](../../src/ifp/webassets) |
| Self-update (reads source-repo releases, shows version / release time / asset size, configurable source) | [`updater.py`](../../src/ifp/updater.py) · [`prefs.py`](../../src/ifp/prefs.py) |
| Environment self-check (Python version / dependencies / bundled resources / toolchain) | [`doctor.py`](../../src/ifp/doctor.py) |
| Tag-triggered release publishing (version-consistency check + sdist / wheel assets) | [`.github/workflows/release.yml`](../../.github/workflows/release.yml) |
| CI semantic gate | [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) |
| Artifact export (JSON / OpenAPI / Protobuf / Markdown) | [`exporter.py`](../../src/ifp/exporter.py) |
| Plugin SDK (custom Lint / exporters) | [`plugin.py`](../../src/ifp/plugin.py) |
| CLI (check / verify / simulate / diff / baseline / export / init / rbac / repo / merge / metrics / align / lsp / web / doctor / update / config / version) | [`cli.py`](../../src/ifp/cli.py) |

**Acceptance criteria**: `ifp check` / `ifp simulate` pass on all refund-domain examples; `ifp verify` finds the over-refund counterexample as expected and exits with a non-zero code; every capability in the table above has unit tests (see [`tests/`](../../tests)); the CI example (see [`ci.yml`](../../.github/workflows/ci.yml)) is fully green.

> Note: the exit-code semantics of `ifp diff` / `ifp merge` / `ifp align` / `ifp update` / `ifp doctor` are documented in their test files; updating and publishing rely on the standard library only and automatically honor `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`.

### Versioning

Version numbers follow `0.<default-iteration>.<patch>`:

- The leading `0` marks a formal release;
- the second number is the **default iteration**, incremented as features progress;
- the third number is the **patch** number, for bug fixes or some minor issues that are not convenient to spell out.

`0.1.0` is the first release of this project.

---

## What Comes Next

The following capabilities are planned for the platform but are not yet provided by the current reference implementation (see the [Glossary](glossary.md)): Sandbox, Event and Event Replay, Task Scheduler, API Key, Workflow.

---

## Delivery Principles

- Each milestone is judged complete by its **acceptance criteria**; no specific time point is preset.
- Any new capability must stay consistent with the existing diagnostic code system, Audit Log, and modeling standard.
- Fine-grained tasks are governed by repository issues; this document only describes direction and acceptance criteria.
