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
