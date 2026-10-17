// VS Code client for the IFP modeling language.
// The language server is the `ifp lsp --stdio` subprocess: it is implemented in
// Python (src/ifp/lsp.py) and ships in the same repository as this extension.

const vscode = require('vscode');
const { LanguageClient, TransportKind } = require('vscode-languageclient/node');

let client;

function serverCommand() {
  return vscode.workspace.getConfiguration('ifp').get('server.command', 'ifp');
}

// Run a CLI subcommand (`verify` / `simulate`) on the active .ifp file, showing
// its output in an integrated terminal.
function runCli(subcommand) {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== 'ifp') {
    vscode.window.showWarningMessage('IFP: open a .ifp model file first.');
    return;
  }
  const file = editor.document.uri.fsPath.replace(/"/g, '\\"');
  const terminal = vscode.window.createTerminal('IFP');
  terminal.show(true);
  terminal.sendText(`${serverCommand()} ${subcommand} "${file}"`);
}

function activate(context) {
  const configuration = vscode.workspace.getConfiguration('ifp');
  const command = configuration.get('server.command', 'ifp');
  const args = configuration.get('server.args', ['lsp', '--stdio']);

  // Launch the server as a child process running `ifp lsp --stdio`.
  const serverOptions = {
    command,
    args,
    transport: TransportKind.stdio
  };

  const clientOptions = {
    documentSelector: [{ scheme: 'file', language: 'ifp' }]
  };

  client = new LanguageClient('ifp', 'IFP Language Server', serverOptions, clientOptions);

  context.subscriptions.push(
    client.start(),
    vscode.commands.registerCommand('ifp.verify', () => runCli('verify')),
    vscode.commands.registerCommand('ifp.simulate', () => runCli('simulate'))
  );
}

function deactivate() {
  if (!client) {
    return undefined;
  }
  return client.stop();
}

module.exports = { activate, deactivate };
