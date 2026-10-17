# IFP v0.3.0 — 领域事件与事件重放

在 v0.2.1 的基础上，本次版本引入**领域事件（Domain Event）声明**与
**事件重放（Event Replay）**能力，为事件溯源（Event Sourcing）建模打下基础。
属于功能迭代版本（minor）。

## 新增能力

- **`event` 声明**：在 `context` 内声明领域事件，支持载荷字段（`field`）与
  `produced_by` 动作引用。事件是聚合状态变化的事实记录。
- **事件重放 `ifp event replay`**：按 JSON 事件序列逐步恢复聚合状态，并在
  每一步之后校验聚合不变量。事件若声明了 `produced_by`，则复用对应动作的
  `post` 条件推导状态；否则按字段名直接写入状态。重放不校验动作前置条件
  （事件是已发生的历史事实），只关注不变量是否始终成立。
- **类型检查增强**：新增诊断码 `IFP1040`（`produced_by` 引用未定义动作）与
  `IFP1041`（事件字段与动作参数不匹配，warning）；事件字段类型复用现有
  值对象 / 状态机类型解析。
- **语义 Diff 覆盖事件**：`ifp diff` 能识别事件的增删、`produced_by` 变更与
  字段类型变更。
- **度量与导出**：`ifp metrics` 统计事件数量；`ifp export --format json` 导出
  事件声明（含字段与 `produced_by`）。

## 事件重放 JSON 格式

```json
{
  "context": "RefundContext",
  "intent": "RefundOrder",
  "initial": {"orderPaidAmount": 100, "totalRefundedAmount": 0,
              "status": "RefundStatus::Pending"},
  "events": [
    {"type": "Audited"},
    {"type": "RefundExecuted", "data": {"amount": 40}}
  ]
}
```

## 退出码

`ifp event replay`：`0` 全部事件重放后不变量成立 / `1` 存在不变量违反或重放失败 /
`2` 输入文件不存在或格式错误。

## 安装

```bash
pip install ifp_platform-0.3.0-py3-none-any.whl
```

详见 [README](https://github.com/midongsang/ifp-platform/blob/main/README.md) 与
[路线图](https://github.com/midongsang/ifp-platform/blob/main/docs/roadmap.md)。

---

# IFP v0.2.1 — 底层修复与输入输出体验优化

在 v0.2.0 沙箱与任务调度器的基础上，本次版本聚焦底层健壮性与输入输出体验，
属于修补版本（bug 修复与可用性改进）。

## 底层修复

- **解析异常收敛**：`parser.parse` 不再用 `except Exception` 一把抓，而是区分 Lark 语法/词法错误
  与 AST 转换异常，内部 bug 不再被伪装成「解析失败」。
- **沙箱信号原因可读**：子进程被信号终止时（SIGKILL / SIGXCPU / SIGSEGV 等）不再只给负退出码，
  会附带「子进程被信号终止：SIGXCPU（超过 CPU 时间上限）」这类可读原因。
- **调度器 fail-fast 真正生效**：`ifp run --fail-fast` 改为有界滑动窗口提交，任务数多于并发度时
  确实能阻止后续任务开始；未提交的任务明确标记为 `skipped`。
- **编码与 BOM 容错**：读取 `.ifp` 文件统一使用 `utf-8-sig`，带 BOM 的文件不再被首字符污染。

## 输入优化

- **路径不存在时报错清晰**：输入路径不存在时输出「路径不存在：…」并以退出码 2 结束，不再抛
  Python traceback。
- **解析错误带行列定位**：`IFPParseError` 携带 `line` / `column`，错误信息形如
  `file:line:col: 解析失败`，CLI 还会渲染出错行与 `^` 插入符。
- **支持 stdin 与 glob**：`ifp check -` 从标准输入读取模型；`ifp check 'src/**/*.ifp'`
  自动展开 glob 模式。
- **重复输入去重**：同一文件被多次传入时按绝对路径去重，避免重复解析与重复诊断。
- **非法并发度拦截**：`ifp run -j 0` 不再被静默改成 1，而是提示并以退出码 2 结束。

## 输出优化

- **诊断带源码片段**：类型检查 / Lint 的诊断会附带出错行的源码片段与插入符，定位更直观。
- **正向反馈**：`ifp check` 无 error/warning 时输出绿色「检查通过」文案。
- **退出码约定统一**：`0 成功 / 1 检查失败 / 2 用法或输入错误 / 3 审批拒绝`，并在模块文档中声明。
- **JSON 输出纯净**：`ifp run --json` 直接写 stdout，确保管道消费方拿到的是纯 JSON。

## 安装

```bash
pip install ifp_platform-0.2.1-py3-none-any.whl
```

详见 [README](https://github.com/midongsang/ifp-platform/blob/main/README.md) 与
[路线图](https://github.com/midongsang/ifp-platform/blob/main/docs/roadmap.md)。

---

# IFP v0.2.0 — 隔离沙箱与任务调度器

第二个版本。在第一个版本（v0.1.0）把 `建模 → 检查 → 验证 → 仿真 → 治理 → 协作 → 导出`
链路跑通之后，v0.2.0 把校验与仿真任务从「一次跑一个」推进到「受控并发、隔离执行」。

## 新增能力

- **隔离沙箱**：`ifp verify` / `ifp simulate` 可加 `--sandbox` 单跑；任务在独立临时目录的子进程
  中执行，剥离环境里的令牌，并施加 POSIX 资源上限（CPU / 内存 / 单文件大小 / 句柄数）与墙钟超时，
  超时以终止整个进程组收尾，输出过长自动截断。
- **任务调度器**：新增 `ifp run`，把一批输入拆成任务（`check` 按路径、`verify` 按「文件 × Intent」、
  `simulate` 按场景）并并发调度；结果按提交顺序返回，逐任务报告 `ok` / `failed` / `timeout` /
  `blocked` / `skipped` 状态与耗时，`--fail-fast` 在首个失败后跳过剩余任务，`--json` 供脚本消费。
- **偏好与自检**：新增 `sandbox.jobs` / `sandbox.timeout` / `sandbox.cpu` / `sandbox.memory`
  偏好（`ifp config` 管理）；`ifp doctor` 追加沙箱可用性自检。

## 兼容性与约束

- 仍只依赖标准库（`subprocess` / `resource` / `tempfile` / `json` / `threading` / `concurrent.futures`）；
- 非 POSIX 平台自动退化为仅施加墙钟超时，其余行为不变；
- 沙箱、任务调度器均与既有诊断码体系、审计日志与建模规范保持一致。

## 安装

下载下方的 wheel / sdist 资产安装：

```bash
pip install ifp_platform-0.2.0-py3-none-any.whl
```

## 命令行

```text
ifp check / verify / simulate / run / diff / baseline / export / init
ifp rbac / repo / merge / metrics / align
ifp lsp / web / doctor / update / config / version
```

详见 [README](https://github.com/midongsang/ifp-platform/blob/main/README.md) 与
[路线图](https://github.com/midongsang/ifp-platform/blob/main/docs/roadmap.md)。
