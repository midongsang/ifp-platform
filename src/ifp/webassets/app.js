'use strict';

// IFP Web 编辑器前端：轻量语法高亮、实时诊断、模型可视化与属性测试。
// 无外部依赖、无构建步骤，全部逻辑集中在此文件。

const sourceEl = document.getElementById('source');
const highlightEl = document.getElementById('highlight');
const gutterEl = document.getElementById('gutter');
const diagnosticsEl = document.getElementById('diagnostics');
const modelEl = document.getElementById('model');
const counterexamplesEl = document.getElementById('counterexamples');
const verifyBtn = document.getElementById('verify');
const statusEl = document.getElementById('status');

const DEFAULT_SOURCE = `context WalletContext {
    description: "钱包限界上下文，演示状态机与资金不变量"

    intent Wallet {
        field balance: Decimal = 0
        field status: WalletStatus = WalletStatus::Active

        state WalletStatus { Active, Frozen }
        initial: WalletStatus::Active

        invariant balanceWithinLimit {
            expr: balance <= 1000
            baseline: true
            owner: "finance-team"
            note: "单账户余额上限，超过需人工复核"
        }

        action Freeze {
            transition: WalletStatus::Frozen
            pre: status == WalletStatus::Active
            post: status == WalletStatus::Frozen
        }

        action Deposit(amount: Decimal) {
            pre: amount >= 0
            post: balance == old(balance) + amount
        }
    }
}
`;

// ---------------------------------------------------------------------------
// 语法高亮
// ---------------------------------------------------------------------------

const KEYWORDS = [
  'context', 'description', 'value', 'intent', 'field', 'state', 'initial', 'invariant',
  'action', 'pre', 'post', 'transition', 'contract', 'expose', 'expr', 'baseline',
  'risk', 'owner', 'note', 'scenario', 'given', 'when', 'rejected', 'then', 'old',
  'true', 'false',
];
const TYPES = ['Decimal', 'Int', 'Bool', 'String'];

const TOKEN_RE = new RegExp(
  '(//[^\\n]*)' +                       // 1 comment
  '|("(?:[^"\\\\]|\\\\.)*")' +         // 2 string
  '|\\b(' + TYPES.join('|') + ')\\b' + // 3 type
  '|\\b(' + KEYWORDS.join('|') + ')\\b' + // 4 keyword
  '|\\b([A-Z][A-Za-z0-9_]*)\\b' +      // 5 PascalCase name
  '|(\\d+(?:\\.\\d+)?)' +               // 6 number
  '|([{}()\\[\\]:,=<>!&|+\\-*/.]+)',    // 7 operator
  'g'
);

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function highlight(code) {
  let out = '';
  let last = 0;
  let match;
  TOKEN_RE.lastIndex = 0;
  while ((match = TOKEN_RE.exec(code)) !== null) {
    out += escapeHtml(code.slice(last, match.index));
    const cls = match[1] ? 'tok-comment'
      : match[2] ? 'tok-string'
        : match[3] ? 'tok-type'
          : match[4] ? 'tok-keyword'
            : match[5] ? 'tok-name'
              : match[6] ? 'tok-number' : 'tok-operator';
    out += '<span class="' + cls + '">' + escapeHtml(match[0]) + '</span>';
    last = match.index + match[0].length;
  }
  return out + escapeHtml(code.slice(last)) + '\n';
}

// ---------------------------------------------------------------------------
// 编辑器渲染
// ---------------------------------------------------------------------------

function renderEditor() {
  const code = sourceEl.value;
  highlightEl.innerHTML = highlight(code);
  const lines = code.split('\n').length;
  let gutter = '';
  for (let i = 1; i <= lines; i += 1) {
    gutter += i + '\n';
  }
  gutterEl.textContent = gutter;
  syncScroll();
}

function syncScroll() {
  highlightEl.scrollTop = sourceEl.scrollTop;
  highlightEl.scrollLeft = sourceEl.scrollLeft;
  gutterEl.scrollTop = sourceEl.scrollTop;
}

function focusLine(line) {
  const lines = sourceEl.value.split('\n');
  const index = Math.max(0, Math.min(line - 1, lines.length - 1));
  let start = 0;
  for (let i = 0; i < index; i += 1) {
    start += lines[i].length + 1;
  }
  sourceEl.focus();
  sourceEl.setSelectionRange(start, start + lines[index].length);
}

// ---------------------------------------------------------------------------
// 诊断 / 模型 / 反例渲染
// ---------------------------------------------------------------------------

function setStatus(text) {
  statusEl.textContent = text;
}

function renderDiagnostics(diagnostics) {
  diagnosticsEl.innerHTML = '';
  if (!diagnostics.length) {
    diagnosticsEl.innerHTML = '<li class="empty">无诊断</li>';
    return;
  }
  diagnostics.forEach((diag) => {
    const li = document.createElement('li');
    li.className = 'sev-' + diag.severity;
    li.innerHTML =
      '<span class="code">' + escapeHtml(diag.code) + '</span>' +
      '<span class="loc">L' + diag.line + '</span>' +
      '<span class="msg">' + escapeHtml(diag.message) + '</span>';
    li.addEventListener('click', () => focusLine(diag.line));
    diagnosticsEl.appendChild(li);
  });
}

function invariantTable(invariants) {
  if (!invariants.length) {
    return '<div class="empty">无不变量</div>';
  }
  let html = '<table><thead><tr><th>不变量</th><th>表达式</th><th>基线</th><th>负责人</th></tr></thead><tbody>';
  invariants.forEach((inv) => {
    html += '<tr>' +
      '<td>' + escapeHtml(inv.name) + '</td>' +
      '<td class="expr">' + escapeHtml(inv.expr || '-') + '</td>' +
      '<td>' + (inv.baseline ? '<span class="badge">baseline</span>' : '-') + '</td>' +
      '<td>' + escapeHtml(inv.owner || '-') + '</td>' +
      '</tr>';
  });
  return html + '</tbody></table>';
}

function fieldTable(fields) {
  if (!fields.length) {
    return '';
  }
  let html = '<table><thead><tr><th>字段</th><th>类型</th><th>默认值</th></tr></thead><tbody>';
  fields.forEach((f) => {
    html += '<tr><td>' + escapeHtml(f.name) + '</td><td class="expr">' + escapeHtml(f.type) +
      '</td><td class="expr">' + escapeHtml(f.default || '-') + '</td></tr>';
  });
  return html + '</tbody></table>';
}

// 状态机：节点排成一行，迁移用正交折线连接（含自环与入口箭头）。
function renderStateMachine(machine) {
  const states = machine.values || [];
  if (!states.length) {
    return '';
  }
  const nw = 118;
  const nh = 44;
  const gap = 56;
  const x0 = 20;
  const y0 = 46;
  const bottom = y0 + nh;
  const index = new Map(states.map((s, i) => [s, i]));
  const centerX = (i) => x0 + i * (nw + gap) + nw / 2;
  const width = x0 * 2 + states.length * nw + (states.length - 1) * gap;
  const initialMember = machine.initial ? machine.initial.split('::').pop() : null;

  const transitions = machine.transitions || [];
  const ribbonBase = bottom + 24;
  const height = ribbonBase + transitions.length * 20 + 16;

  let svg = '<svg width="' + width + '" height="' + height + '" viewBox="0 0 ' + width + ' ' + height + '">';
  svg += '<defs><marker id="ifp-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" ' +
    'orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 Z" fill="#57606a"/></marker></defs>';

  states.forEach((state, i) => {
    const x = x0 + i * (nw + gap);
    const fill = state === initialMember ? '#eef2ff' : '#ffffff';
    svg += '<rect x="' + x + '" y="' + y0 + '" width="' + nw + '" height="' + nh +
      '" rx="10" fill="' + fill + '" stroke="#d8dde5"/>';
    svg += '<text x="' + (x + nw / 2) + '" y="' + (y0 + nh / 2 + 5) +
      '" text-anchor="middle" font-size="13" fill="#1f2430">' + escapeHtml(state) + '</text>';
    if (state === initialMember) {
      svg += '<text x="' + (x + nw / 2) + '" y="' + (y0 - 10) +
        '" text-anchor="middle" font-size="11" fill="#2f6fed">initial</text>';
    }
  });

  transitions.forEach((t, i) => {
    const to = index.get(t.to);
    if (to === undefined) {
      return;
    }
    const ribbon = ribbonBase + i * 20;
    const targetX = centerX(to);
    let sourceX;
    if (t.from && index.has(t.from)) {
      sourceX = centerX(index.get(t.from));
      if (sourceX === targetX) {
        // 自环：节点右侧画一个小回环
        const x = x0 + to * (nw + gap) + nw;
        svg += '<path d="M ' + x + ' ' + (y0 + 12) + ' C ' + (x + 34) + ' ' + (y0 - 6) +
          ', ' + (x + 34) + ' ' + (y0 + nh / 2) + ', ' + x + ' ' + (y0 + nh / 2) +
          '" fill="none" stroke="#57606a" marker-end="url(#ifp-arrow)"/>';
        svg += '<text x="' + (x + 38) + '" y="' + (y0 + 6) + '" font-size="11" fill="#57606a">' +
          escapeHtml(t.action) + '</text>';
        return;
      }
    } else {
      sourceX = x0 - 16;
    }
    svg += '<path d="M ' + sourceX + ' ' + ribbon + ' L ' + targetX + ' ' + ribbon +
      ' L ' + targetX + ' ' + bottom + '" fill="none" stroke="#57606a" marker-end="url(#ifp-arrow)"/>';
    const labelX = (sourceX + targetX) / 2;
    svg += '<text x="' + labelX + '" y="' + (ribbon - 5) + '" text-anchor="middle" font-size="11" ' +
      'fill="#57606a">' + escapeHtml(t.action) + '</text>';
  });

  svg += '</svg>';
  return '<div class="statemachine">' + svg + '</div>';
}

function renderModel(model) {
  if (!model || !model.contexts.length) {
    modelEl.innerHTML = '<div class="empty">尚无模型</div>';
    return;
  }
  let html = '';
  model.contexts.forEach((ctx) => {
    html += '<div class="context"><h3>' + escapeHtml(ctx.name) + '</h3>';
    if (ctx.description) {
      html += '<div class="empty">' + escapeHtml(ctx.description) + '</div>';
    }
    ctx.values.forEach((value) => {
      html += '<div class="intent"><h4>' + escapeHtml(value.name) + ' <span class="badge">value object</span></h4>';
      html += fieldTable(value.fields);
      html += invariantTable(value.invariants);
      html += '</div>';
    });
    ctx.intents.forEach((intent) => {
      html += '<div class="intent"><h4>' + escapeHtml(intent.name) + '</h4>';
      if (intent.description) {
        html += '<div class="empty">' + escapeHtml(intent.description) + '</div>';
      }
      if (intent.stateMachine) {
        html += renderStateMachine(intent.stateMachine);
      }
      html += fieldTable(intent.fields);
      html += invariantTable(intent.invariants);
      if (intent.actions.length) {
        html += '<table><thead><tr><th>动作</th><th>pre</th><th>post</th></tr></thead><tbody>';
        intent.actions.forEach((action) => {
          const params = action.params.map((p) => p.name + ': ' + p.type).join(', ');
          html += '<tr><td>' + escapeHtml(action.name + '(' + params + ')') +
            '</td><td class="expr">' + escapeHtml(action.pre || '-') +
            '</td><td class="expr">' + escapeHtml(action.post || '-') + '</td></tr>';
        });
        html += '</tbody></table>';
      }
      html += '</div>';
    });
    ctx.contracts.forEach((contract) => {
      html += '<div class="intent"><h4>' + escapeHtml(contract.name) +
        ' <span class="badge">contract</span></h4><div class="empty">暴露字段：' +
        escapeHtml(contract.exposes.join(', ') || '-') + '</div></div>';
    });
    html += '</div>';
  });
  if (model.scenarios.length) {
    html += '<div class="context"><h3>场景</h3><div class="empty">' +
      escapeHtml(model.scenarios.map((s) => s.name).join(', ')) + '</div></div>';
  }
  modelEl.innerHTML = html;
}

function renderCounterexamples(result) {
  counterexamplesEl.innerHTML = '';
  const items = result.counterexamples || [];
  if (!items.length) {
    counterexamplesEl.innerHTML = '<div class="empty">未发现违反不变量的反例</div>';
    return;
  }
  items.forEach((ce) => {
    const div = document.createElement('div');
    div.className = 'ce';
    div.innerHTML = '<div>' + escapeHtml(ce.intent + '.' + ce.action) +
      (ce.baseline ? ' <span class="badge">baseline</span>' : '') +
      '</div><div class="empty">突破不变量 ' + escapeHtml(ce.invariant) +
      '（' + ce.steps + ' 步）</div><pre>' + escapeHtml(ce.describe) + '</pre>';
    counterexamplesEl.appendChild(div);
  });
}

// ---------------------------------------------------------------------------
// 与后端交互
// ---------------------------------------------------------------------------

async function post(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
}

let analyzeTimer = null;

async function analyze() {
  setStatus('分析中…');
  try {
    const result = await post('/api/analyze', { source: sourceEl.value, filename: '<editor>' });
    renderDiagnostics(result.diagnostics);
    if (result.model) {
      renderModel(result.model);
    }
    setStatus(result.parseError ? '语法错误' : '诊断 ' + result.diagnostics.length + ' 条');
  } catch (error) {
    setStatus('分析请求失败');
  }
}

function scheduleAnalyze() {
  clearTimeout(analyzeTimer);
  analyzeTimer = setTimeout(analyze, 300);
}

async function runVerify() {
  verifyBtn.disabled = true;
  setStatus('属性测试运行中…');
  try {
    const result = await post('/api/verify', { source: sourceEl.value, mode: 'light' });
    renderCounterexamples(result);
    renderDiagnostics(result.diagnostics);
    setStatus('检查 ' + result.actionsChecked + ' 个动作 × ' + result.invariantsChecked + ' 条不变量');
  } catch (error) {
    setStatus('属性测试失败');
  } finally {
    verifyBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

sourceEl.addEventListener('input', () => {
  renderEditor();
  scheduleAnalyze();
});
sourceEl.addEventListener('scroll', syncScroll);
sourceEl.addEventListener('keydown', (event) => {
  if (event.key === 'Tab') {
    event.preventDefault();
    const start = sourceEl.selectionStart;
    const end = sourceEl.selectionEnd;
    sourceEl.setRangeText('    ', start, end, 'end');
    renderEditor();
    scheduleAnalyze();
  }
});
verifyBtn.addEventListener('click', runVerify);

sourceEl.value = DEFAULT_SOURCE;
renderEditor();
analyze();
