# HRAgents API 摘要

## 鉴权

`POST /api/v1/auth/login`，本地演示账号：`employee/employee123`、`hr_admin/admin123`、`admin/admin123`。

## 员工制度问答

`POST /api/v1/chat`：`dept_ids=null` 时由 Harness 自动路由；最终证据仍经过 ACL、版本与生效状态校验。

## HR Policy API

| Method | Path | 说明 |
|---|---|---|
| PUT | `/api/v1/hr/policies/{doc_id}/metadata` | 设置 company、policy level、parent policy、ACL 与生效信息 |
| POST | `/api/v1/hr/policies/{doc_id}/claims/rebuild` | 重新提取可溯源 Policy Claims |
| POST | `/api/v1/hr/conflicts/analyze` | 执行制度冲突固定 Workflow |
| GET | `/api/v1/hr/conflicts/{case_id}` | 查询冲突 case |
| POST | `/api/v1/hr/conflicts/{case_id}/review` | HR HITL：confirm/reject/revise |
| POST | `/api/v1/hr/interview/generate` | bounded ReAct 智能面试 |

## 内部接口

`/api/v1/internal/*` 仅供服务间调用，要求 `X-Internal-Token`。`/internal/calendar` 为历史兼容路径，当前语义是企业工作日历/法定假期/HR 时间节点。
