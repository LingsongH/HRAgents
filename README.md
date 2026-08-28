# HRAgents · 企业 HR 可信智能体平台

面向企业 HR 的高可信知识与业务流程场景，构建一套**可控、可验证、可迭代**的多智能体系统。项目保留企业场景本身，不把原学校项目简单换皮；复用的是已验证的 Harness、可信 RAG、Memory 与 Feedback Loop 方法，并将其重新约束到 HR 的权限、版本、制度层级与人工决策链路。

> 核心主线：**可信企业知识 RAG → Harness + Workflow 可控执行 → Agent Skills 业务协作 → Memory + HITL → Feedback Loop 持续优化**。

## 业务场景

平台上层挂载三个共享 Runtime 下的 HR Skills：

1. **员工制度问答**：基于 Permission-aware Trusted RAG，从有效官方制度回答考勤、休假、报销、入离职等问题。
2. **制度冲突分析**：比较公司级制度、部门补充规定和不同版本条款，区分 `conflict / exception / supplement / consistent`，并对高风险结论进入 HR HITL。
3. **智能面试**：岗位画像、候选人画像和题库作为受控工具，由 bounded ReAct 生成结构化面试与追问计划。

## 统一架构

```text
                    Enterprise Agent Platform
                              │
                       Agent Harness
                              │
              Intent / Task Complexity Router
                              │
        ┌─────────────────────┼─────────────────────┐
        ↓                     ↓                     ↓
 Policy Conflict         Interview Agent       Employee QA
     Skill                   Skill                 Skill
        │                     │                     │
 Graph Workflow         bounded ReAct        RAG Workflow
        │                     │                     │
        └───────────── Shared Runtime ──────────────┘
                              │
               ┌──────────────┼──────────────┐
               ↓              ↓              ↓
          Trusted RAG       Memory          MCP
               │
          ACL / Version
          Hierarchy / Time
               │
              HITL
               │
          Feedback Loop
```

### Harness + Graph Workflow + bounded ReAct

高风险业务不允许 Agent 任意规划：

```text
Policy Conflict
Intent → Retrieval → Claim/Clause Alignment → Conflict Analysis → Verify → HITL

Employee QA
Intent → Rewrite → Retrieval → Permission/Version Filter → Answer → Verify
```

开放型任务保留有限自主性：

```text
Interview
Job/Candidate Context → bounded ReAct
                     ├─ get_job_profile
                     ├─ get_candidate_profile
                     └─ get_question_bank

限制：max_steps + tool_budget + duplicate_detection + cache + timeout
```

实现位置：`backend/app/hr/`、`backend/app/harness/`、`backend/app/loop/default_skills.py`。

## Permission-aware Trusted RAG

企业制度不仅要“检索到”，还必须“有资格作为证据”。`TrustedPolicyRAG` 在候选 chunk 进入回答或冲突分析前校验：

- `company_id / dept_id / role` ACL；
- `effective / deprecated` 生命周期；
- `effective_date / expiry_date`；
- `policy_level`（集团/公司/部门/团队等层级）；
- `parent_policy_id` 授权关系；
- 官方制度、HR 审核结论、FAQ、用户输入等不同 authority level。

证据权威链保持显式隔离：

```text
有效官方制度
>
HR 审核结论
>
企业 FAQ
>
用户输入
>
会话摘要
>
模型推断
```

HR 历史判断和 FAQ 可以帮助召回或上下文构建，但**不能冒充官方制度原文**。

## Claim / Clause 级制度冲突分析

参考 `test_utils` 原型中“chunk → 最小 Claim → source_quote → embedding 对齐 → LLM 判断”的验证思路，企业版新增：

- `PolicyClaimExtractor`：从制度 chunk 提取可比较的最小规则 Claim，并要求 `source_quote` 能回溯到原文；
- `PolicyConflictWorkflow`：先校验 ACL/版本/生效期，再做 Claim 对齐；
- Conflict Agent：综合适用对象、生效时间、制度层级、父子制度授权判断冲突、例外或补充；
- 独立 Verifier：确认每个 material 结论都有双方证据；
- HR HITL：冲突或低置信结论进入人工确认。

典型链路：

```text
“新版员工考勤制度和研发中心弹性工作规定有没有冲突？”
        ↓
Intent Router
        ↓
Policy Retrieval
   ┌────┴────┐
公司级制度   部门级制度
   └────┬────┘
ACL / Version / Effective Validation
        ↓
Claim / Clause Alignment
        ↓
Conflict Analysis
        ↓
Verifier
        ↓
HR HITL
```

## Skills 是可执行策略，不是 Prompt

内置三个基线 Skill：

- `skill_hr_policy_conflict`
- `skill_hr_employee_qa`
- `skill_hr_interview`

每个 Skill 同时定义 trigger、工具、检索参数、执行模式、Workflow 与 constraints。它们与自动挖掘的 Skill 共用同一套 Runtime 和策略生命周期。

## Memory 与 Fact Plane

系统把正式制度事实与记忆分开：

- **Official Knowledge Fact Plane**：`documents / chunks / policy_claims`，最高权威；
- **Working Memory**：当前任务和会话；
- **User Memory**：用户明确同意保存的低敏偏好及必要上下文；
- **Organizational Memory**：HR 审核结论、企业 FAQ、历史解释；
- **Procedural Memory**：Skills / Rules / Hooks / Workflow Strategy。

`ContextBuilder` 按用户权限、信息时效、权威级别和 Token Budget 组装上下文，避免摘要或模型推断污染正式制度证据。

## HITL + Feedback Loop

HR 人工审核不是终点，而是反馈闭环的输入：

```text
Execute → Observe → HR Review → Reflect → Adapt → Replay/Evaluate → Deploy
```

bad case 可归因为：

- `retrieval`
- `intent`
- `generation`
- `knowledge_gap`
- `policy_hierarchy`
- `acl_version`

例如部门制度在上位制度明确授权范围内时，若模型错误判成冲突，HR 可标记 `hierarchy_resolution_error`。Loop 会把它归为 `policy_hierarchy`，生成候选 Rule/Skill 调整，并通过历史 Case Replay、灰度和回滚机制后再发布。

## MCP 的定位

MCP 只承担 **Tool Integration Layer**，用于标准化接入企业云盘、HRIS、ATS、内部知识库等外部系统；权限、事实校验、Workflow 和策略发布仍由 Agent Harness 控制。

## 目录

```text
HRAgents/
├── backend/
│   └── app/
│       ├── hr/                 # Trusted RAG / Claim / Conflict / Interview
│       ├── harness/            # Agent Harness 与固定 DAG
│       ├── loop/               # Feedback Loop / Skills / Rules / Hooks
│       ├── memory/             # Working/User/Organizational/Procedural Memory
│       ├── pipeline/           # 文档解析、元数据、索引
│       └── api/routes/hr.py    # HR 业务 API
├── services/pi-agent/          # 概率性 Agent 执行层（受 Python 控制平面约束）
├── web/                        # 员工问答与管理端
├── docs/                       # 架构、API、Loop、部署说明
├── deploy/                     # Docker / K8s / Helm
└── loadtest/
```

## 关键 HR API

```text
PUT  /api/v1/hr/policies/{doc_id}/metadata
POST /api/v1/hr/policies/{doc_id}/claims/rebuild
POST /api/v1/hr/conflicts/analyze
GET  /api/v1/hr/conflicts/{case_id}
POST /api/v1/hr/conflicts/{case_id}/review
POST /api/v1/hr/interview/generate
```

原 `/api/v1/chat` 继续承担员工制度问答入口。

## 本地运行

```bash
# 后端
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export STORAGE_MODE=memory
uvicorn app.main:app --reload --port 8000

# pi Agent Runtime（另开终端）
cd services/pi-agent
npm install
npm run dev

# 前端（另开终端）
cd web
npm install
BACKEND_URL=http://localhost:8000 npm run dev
```

Docker：

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec backend python -m scripts.seed_data
```

演示账号：

```text
employee / employee123
hr_admin / admin123
admin / admin123
```

> 演示账号只用于本地环境；生产需设置 `SEED_DEMO_USERS=false` 并使用真实身份系统。

## 验证

```bash
cd backend && pytest -q
cd ../web && npm run build
cd ../services/pi-agent && npm run build
```

项目不复用学校项目的历史准确率指标。企业场景的 Recall、引用正确率、冲突分类准确率、Verifier 通过率和 HITL 修正率应使用企业制度测试集重新测量。
