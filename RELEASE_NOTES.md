# IFP v0.1.0 — 第一个版本

把业务规则从自然语言变成机器可校验的模型：`.ifp` 建模 → 类型检查与 Lint → SMT 属性测试 →
场景仿真 → 语义 Diff 与治理 → 协作与权限 → 导出，链路端到端跑通。

## 主要能力

- **建模与检查**：限界上下文、Intent、值对象、状态机、不变量、Pre/Post、跨域契约；字段 /
  枚举 / 类型安全与建模规范 Lint（资金统一用 `Decimal`）。
- **验证与仿真**：SMT 反例搜索（值对象字段内联展开、多步路径、`single` / `light` / `full`
  档位与超时降级）；场景仿真覆盖正向、边界与非法操作拦截。
- **治理与协作**：语义 Diff 与基线风险评分、CI 语义门禁、Hash Chain 审计日志、RBAC 角色
  访问控制、Git 兼容模型仓库与业务语义三方合并、模型度量与模型 / 代码版本对齐审计。
- **编辑与可视化**：内置 LSP（实时诊断 / 结构大纲 / **跳转定义** / **快速修复**）、
  VS Code 插件、浏览器内 Web 编辑器。
- **上手与更新**：`ifp update` 读取来源仓库的发行版（展示版本 / 发行时间 / 资产大小，由你
  决定是否更新）、`ifp doctor` 环境自检、`ifp config` 本地偏好管理。

## 安装

下载下方的 wheel / sdist 资产安装：

```bash
pip install ifp_platform-0.1.0-py3-none-any.whl
```

## 命令行

```text
ifp check / verify / simulate / diff / baseline / export / init
ifp rbac / repo / merge / metrics / align
ifp lsp / web / doctor / update / config / version
```

详见 [README](https://github.com/midongsang/ifp-platform/blob/main/README.md) 与
[路线图](https://github.com/midongsang/ifp-platform/blob/main/docs/roadmap.md)。
