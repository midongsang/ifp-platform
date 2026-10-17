# IFP 术语表中英对照

> 来源：IFP 全套文档合集（V1.1）附录 J。
> 读者：建模师、开发、产品/业务分析师、审计与评审人员。
> 相关文档：[DSL 语法规范](dsl-spec.md) · [建模规范](modeling-standard.md) · [业务侧培训材料](training-business.md)

下表收录 IFP 平台的核心术语中英对照与释义。同一概念在代码中出现的模块位置已尽量与实现对齐（见文末[实现索引](#实现索引)）。

| 中文 | English | 释义简述 |
| --- | --- | --- |
| IFP | IFP | 本业务建模平台名称（Intention Formal Platform，意图形式化平台） |
| 限界上下文 | BoundedContext | DDD 概念，划分业务域边界，一组高内聚实体、值对象与契约 |
| 意图/聚合根 | Intent | IFP 核心模型单元，对应 DDD 聚合根，描述实体状态、状态机、不变量 |
| 值对象 | ValueObject | 无唯一标识、不可变，仅承载属性与自身约束 |
| 成员访问 | Member Access | 以 `字段.成员` 形式引用值对象内部成员（如 `balance.amount`） |
| 内联展开 | Inline Expansion | 把值对象字段展开为其底层叶子字段，使值对象约束可参与 SMT 求解 |
| 多步路径搜索 | Multi-step Search | 从初始状态出发，搜索由多个动作组成的操作序列以发现不变量被打破的路径 |
| 跨域契约 | Contract | 限界上下文对外暴露的最小服务契约，供其他域调用 |
| 状态机 | StateMachine | 定义实体合法状态集合与状态流转规则 |
| 不变量 | Invariant | 实体处于合法状态时，必须永久成立的业务约束 |
| 前置条件 | Precondition (Pre) | 接口执行前必须满足的断言，控制接口调用合法性 |
| 后置条件 | Postcondition (Post) | 接口执行完成后，实体状态必须满足的断言 |
| 基线 | Baseline | 标记受保护的核心不变量，修改需要单独审批流程 |
| 规则豁免 | Rule Exception | 审批通过的特例，允许单条实体临时绕过不变量，留存审计记录 |
| 仿真场景 | Scenario | 仿真测试用例，描述实体操作序列，验证模型行为 |
| 属性测试 | Property Test | 自动搜索工具，生成状态组合，寻找违反不变量的反例 |
| 语义 Diff | Semantic Diff | 对比两次模型变更，识别业务规则层面差异，而非纯文本差异 |
| 语义门禁 | Semantic Gate | 在 CI / PR 中用语义 Diff 的风险评分与审批判定阻断高风险模型变更合并 |
| 模型静态检查 | Model Lint | 校验模型是否符合建模规范、最佳实践 |
| 审计日志 | Audit Log | 不可篡改的追加式操作记录，用于审计取证 |
| 沙箱 | Sandbox | 隔离运行环境，仿真、属性测试在此执行，资源隔离 |
| 导出器 | Exporter | 将 IFP 模型导出为 OpenAPI、Protobuf、报表等其他格式 |
| 抽象语法树 | AST（Abstract Syntax Tree） | 源码解析后生成的结构化树，用于类型检查、语义分析 |
| SMT 求解器 | SMT Solver | 底层约束求解内核，用于查找约束矛盾、搜索反例 |
| 反例 | Counterexample | 满足模型前置条件，但违反不变量/属性的一组实例，代表业务漏洞 |
| 模型仓库 | Model Repository | Git 兼容存储，保存多版本 IFP 模型源码 |
| 语义合并 | Semantic Merge | 合并模型分支时，基于业务语义判断冲突，不只是文本合并 |
| 事件 | Event | 领域事件，描述聚合发生的业务变更，用于事件溯源建模 |
| 事件回放 | Event Replay | 按顺序重放事件序列，还原聚合状态，校验不变量 |
| 任务调度器 | Task Scheduler | 调度仿真、属性测试任务，worker 分发与资源管控 |
| 插件 SDK | Plugin SDK | 开发自定义 Lint、导出器、仿真钩子的开发套件 |
| 风险评分 | Risk Score | 模型变更的风险量化分值，用于审批优先级判断 |
| 模型度量指标 | Model Metrics | 模型质量指标：规则覆盖率、死不变量数量等 |
| 死不变量 | Dead Invariant | 恒成立的冗余不变量，不产生约束效果 |
| 类型检查 | Type Check | 校验模型表达式类型是否匹配，捕获类型错误 |
| 建模语言 | IFP DSL | IFP 专用领域建模语言，`.ifp` 文件使用 |
| 制品 | Artifact | 从模型导出的产物：报表、schema、代码骨架等 |
| 角色访问控制 | RBAC（Role-Based Access Control） | 平台权限模型，基于角色分配项目/平台权限 |
| API 密钥 | API Key | 用于 CI、外部系统调用 IFP 接口的鉴权凭证 |
| 工作流 | Workflow | 模型变更提交、评审、审批、合并的流程 |
| 模型评审 | Model Review | 对 IFP 模型变更进行业务、语法、规则层面评审 |

---

## 实现索引

下列术语在当前参考实现中已有对应模块（路径相对仓库根目录）：

| 术语 | 实现位置 |
| --- | --- |
| IFP DSL / 抽象语法树 | [`src/ifp/grammar.lark`](../src/ifp/grammar.lark)、[`src/ifp/parser.py`](../src/ifp/parser.py)、[`src/ifp/ast.py`](../src/ifp/ast.py) |
| 类型检查 | [`src/ifp/typecheck.py`](../src/ifp/typecheck.py) |
| 模型静态检查（Lint） | [`src/ifp/lint.py`](../src/ifp/lint.py) |
| SMT 求解器 / 属性测试 / 反例 | [`src/ifp/smt.py`](../src/ifp/smt.py) |
| 仿真场景 | [`src/ifp/simulate.py`](../src/ifp/simulate.py) |
| 语义 Diff | [`src/ifp/diff.py`](../src/ifp/diff.py) |
| 语义门禁 | [`src/ifp/cli.py`](../src/ifp/cli.py)（`ifp diff`）、[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) |
| 基线 / 风险评分 | [`src/ifp/baseline.py`](../src/ifp/baseline.py) |
| 审计日志 | [`src/ifp/audit.py`](../src/ifp/audit.py) |
| 角色访问控制（RBAC） / 权限点 | [`src/ifp/rbac.py`](../src/ifp/rbac.py) |
| 导出器 / 制品 | [`src/ifp/exporter.py`](../src/ifp/exporter.py) |
| 插件 SDK | [`src/ifp/plugin.py`](../src/ifp/plugin.py) |
| 模型仓库 / 语义合并 | [`src/ifp/repo.py`](../src/ifp/repo.py)、[`src/ifp/semantic_merge.py`](../src/ifp/semantic_merge.py) |

> 说明：术语“沙箱”“事件”“事件回放”“任务调度器”“API 密钥”“工作流”属于平台规划中的能力，当前参考实现尚未提供，详见[路线图](roadmap.md)。
