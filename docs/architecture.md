# HRAgents 架构

## 设计目标

企业制度存在权限、版本、生效时间和层级授权关系；HR 冲突审核与智能面试又包含不同程度的 Agent 自主性。因此系统围绕三件事设计：**知识可信、执行可控、反馈可迭代**。

## 控制平面

Python Agent Harness 是唯一控制平面，负责身份/ACL、任务路由、固定 Graph Workflow、Memory Context、Verifier、HITL、策略发布与回滚。`services/pi-agent` 只承担概率性 Agent loop 和受控 tool calling，不能自行扩大权限或绕过证据校验。

## 三个 HR Skills

| Skill | 执行模式 | 主链 |
|---|---|---|
| 员工制度问答 | Graph/RAG Workflow | Intent → Rewrite → Retrieval → ACL/Version → Answer → Verify |
| 制度冲突分析 | Graph Workflow | Retrieval → Claim Alignment → Conflict → Verify → HITL |
| 智能面试 | bounded ReAct | Profile → Tool calls → Gap analysis → Questions → Rubric |

## Trusted RAG

候选 chunk 必须经过 `TrustedPolicyRAG`：company/dept/role ACL、effective/deprecated、日期、policy level、parent policy。正式制度 Fact Plane 与 HR 审核结论、FAQ、会话摘要、模型推断分层隔离。

## 制度 Claim

`PolicyClaimExtractor` 将 chunk 提炼为最小 Claim，并保存 `source_quote`、subject/action/condition/exception 和 embedding。冲突分析对 Claim 做语义对齐，再结合制度层级和授权关系判断 `conflict / exception / supplement / consistent`。

## Memory

- Working Memory：当前会话/任务
- User Memory：用户同意保存的低敏偏好
- Organizational Memory：HR 审核结论、FAQ、历史解释
- Procedural Memory：Skills/Rules/Hooks/策略
- Official Fact Plane：documents/chunks/policy_claims，独立且最高权威

## Feedback Loop

`Execute → Observe → Reflect → Adapt → Deploy`。支持 retrieval、intent、generation、knowledge_gap、policy_hierarchy、acl_version 等归因，候选策略经过历史 Replay、灰度与回滚。

## 身份

演示角色：`employee / hr / hr_admin / admin`。用户可绑定 `company_id`、`dept_id`；企业制度检索再结合文档的 allowed departments/roles 做权限过滤。
