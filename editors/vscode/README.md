# IFP Language Support for VS Code

Syntax highlighting, live diagnostics, go-to-definition and quick fixes for IFP
(Intent Formalization Platform) model files (`.ifp`).

The extension is a thin Language Server Protocol (LSP) client. It launches the
built-in IFP language server as a subprocess and shows the diagnostics it
publishes in the editor:

```
ifp lsp --stdio
```

Diagnostics come from the same type-check and lint passes that `ifp check` runs,
so the Problems panel reports the same errors and warnings as the CLI.

## Features

| Feature | What it does |
| --- | --- |
| Syntax highlighting | TextMate grammar for the IFP DSL. |
| Live diagnostics | Type-check (IFP1xxx) and lint (IFP2xxx) results appear in the Problems panel and are re-computed on every edit (full-document sync). |
| Structure outline | `textDocument/documentSymbol` populates the Outline view with contexts, values, intents, states, actions, invariants and scenarios. |
| Go to definition | Jump from a reference (`State::Member`, a field type, an action call in a scenario, an intent) to its declaration, including across files in the same workspace. |
| Quick fixes | The lightbulb offers fixes for common findings, e.g. adding the missing `owner` / `risk` on a baseline invariant, declaring the missing `initial` state, or defining a missing field type as a value object. |

## Prerequisites

The `ifp` command must be on your `PATH`. Install the platform first, either
from a checkout of this repository:

```
pip install -e ".[dev]"
```

or from the published package:

```
pip install ifp-platform
```

## Run from source

1. Open the `editors/vscode/` directory of this repository in VS Code.
2. Install the client dependency:

   ```
   npm install
   ```

3. Press `F5` to launch the Extension Development Host, then open a `.ifp` file.

## Package as a `.vsix`

Install `vsce` on demand and package the extension:

```
npx @vscode/vsce package
```

This produces an `ifp-vscode-0.1.0.vsix` in the current directory.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `ifp.server.command` | `ifp` | Command used to start the language server. |
| `ifp.server.args` | `["lsp", "--stdio"]` | Arguments passed to the language server command. |

If `ifp` is not on your `PATH`, point the command at an interpreter or console
script directly, for example a virtualenv:

```json
{
  "ifp.server.command": "/path/to/.venv/bin/ifp",
  "ifp.server.args": ["lsp", "--stdio"]
}
```

## Verify it works

1. Open `examples/refund/refund.ifp`.
2. Introduce a typo (for example, change `Decimal` to `Decima1` on a `field`).
3. The problem appears in the Problems panel within a moment, and the file is
   re-checked on every change (full-document sync).
4. Put the cursor on a state reference such as `RefundStatus::Pending` and press
   `F12` to jump to the `state` declaration; open the lightbulb (`Ctrl+.`) on a
   finding to apply a quick fix.

## Notes

- The language server is started as `ifp lsp --stdio` and speaks LSP JSON-RPC
  over stdio. It is implemented in the Python package in this repository
  (`src/ifp/lsp.py`).
- The server provides diagnostics, the structure outline, go-to-definition and
  quick fixes; it does not provide completion or hover.
- Model files use the `.ifp` extension and the language id `ifp`.
