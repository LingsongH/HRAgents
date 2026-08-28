# HR Feedback Loop

HRAgents 不把人工纠错只保存成日志，而是把 HR Review 作为策略改进输入。

## 生命周期

```text
Execute → Observe → HR Review → Reflect → Adapt → Replay/Evaluate → Deploy
```

## 可学习对象

- **Skill**：完整可执行策略，例如制度冲突 Workflow 或员工制度问答 Workflow。
- **Rule**：确定性约束，例如“上位制度明确授权部门自定义且未突破授权边界时，优先判断为 exception/supplement”。
- **Hook**：跨职能事件触发，例如 HR 制度问题涉及报销条款时补充财务制度检索。

## 根因分类

- retrieval：召回/切片/重排问题
- intent：任务类型或部门路由问题
- generation：答案与证据不一致
- knowledge_gap：有效官方制度没有答案
- policy_hierarchy：忽略制度上下位或授权关系
- acl_version：使用了无权限、已废止、未生效或错误版本证据

## HR 冲突案例

如果 Conflict Agent 将研发中心弹性规定判断为与公司考勤制度冲突，而 HR 确认公司制度明确授权研发部门制定弹性细则，则反馈记录：

```text
reason = hierarchy_resolution_error
loop_category = policy_hierarchy
```

Loop 可生成候选 Rule，经过历史 Case Replay 和评测后再进入灰度/发布，不允许一次人工反馈直接修改生产策略。

## 三个基线 Skill

`backend/app/loop/default_skills.py` 幂等提供：

- `skill_hr_policy_conflict`
- `skill_hr_employee_qa`
- `skill_hr_interview`

Skill 不只是 Prompt，而是 trigger + tools + retrieval parameters + workflow + constraints。
