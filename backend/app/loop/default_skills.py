"""Built-in HR Skills used by the shared Agent Harness.

A Skill is an executable strategy unit: trigger + tools/retrieval parameters +
workflow + constraints.  It is not just a prompt fragment.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.storage.store import DataStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_SKILLS: list[dict[str, Any]] = [
    {
        "_id": "skill_hr_policy_conflict",
        "name": "制度冲突分析",
        "description": "对公司级制度、部门补充规定及不同版本条款执行可信检索、Clause Alignment、冲突判定、独立验证与 HR HITL。",
        "dept_id": "dept_hr", "scope": "global", "exclusive": True, "priority": 100,
        "trigger": {
            "intent_patterns": ["制度冲突", "规定冲突", "是否冲突", "新版制度", "部门规定", "例外", "补充规定"],
            "entities_required": ["policy"], "confidence_threshold": 0.8,
        },
        "action": {
            "type": "workflow",
            "execution_mode": "graph",
            "steps": [
                {"step": 1, "action": "retrieve", "params": {"query": "{policy} 制度 条款 生效 版本 适用对象", "top_k": 12}},
                {"step": 2, "action": "call_tool", "params": {"tool": "policy_version_acl_check"}},
                {"step": 3, "action": "call_tool", "params": {"tool": "policy_claim_alignment"}},
                {"step": 4, "action": "generate", "params": {"template": "冲突-例外-补充关系分析"}},
                {"step": 5, "action": "call_tool", "params": {"tool": "independent_verifier"}},
                {"step": 6, "action": "call_tool", "params": {"tool": "hr_hitl_if_required"}},
            ],
        },
        "constraints": {
            "require_effective_document": True,
            "require_source_quote": True,
            "require_version_check": True,
            "require_hierarchy_check": True,
            "require_hitl_on_conflict": True,
        },
        "unique_rules": ["下位制度在上位制度明确授权范围内的细化优先判定为 exception/supplement，不得直接判 conflict。"],
        "rubric_rules": ["每个 material 结论必须同时绑定两侧制度原文、适用对象、生效时间和层级关系。"],
    },
    {
        "_id": "skill_hr_employee_qa",
        "name": "员工制度问答",
        "description": "使用 Permission-aware Trusted RAG 回答员工制度问题，组织记忆只作召回提示，最终证据必须回源有效官方制度。",
        "dept_id": "dept_hr", "scope": "global",
        "trigger": {
            "intent_patterns": ["考勤", "请假", "休假", "报销", "福利", "入职", "离职", "制度", "员工手册", "怎么办理"],
            "entities_required": ["matter"], "confidence_threshold": 0.7,
        },
        "action": {
            "type": "workflow",
            "execution_mode": "graph",
            "steps": [
                {"step": 1, "action": "rewrite", "params": {"template": "员工制度检索改写"}},
                {"step": 2, "action": "retrieve", "params": {"query": "{matter} 条件 适用对象 生效 例外", "top_k": 8}},
                {"step": 3, "action": "call_tool", "params": {"tool": "permission_version_filter"}},
                {"step": 4, "action": "generate", "params": {"template": "结论-条件-办理步骤-来源"}},
                {"step": 5, "action": "call_tool", "params": {"tool": "independent_verifier"}},
            ],
        },
        "constraints": {"require_citation": True, "official_fact_plane_only": True},
        "unique_rules": ["HR 历史解释、FAQ、会话摘要不能冒充官方制度原文。"],
        "rubric_rules": ["涉及日期、次数、比例、金额和审批条件时必须逐字核对有效制度。"],
    },
    {
        "_id": "skill_hr_interview",
        "name": "智能面试",
        "description": "开放型面试任务允许 bounded ReAct，自主调用岗位画像、候选人画像和题库，但受 Tool Budget、重复检测、缓存、超时和最大步数约束。",
        "dept_id": "dept_hr", "scope": "department", "exclusive": True, "priority": 90,
        "trigger": {
            "intent_patterns": ["面试", "候选人", "JD", "岗位画像", "追问题", "面试题", "人才画像"],
            "entities_required": ["job"], "confidence_threshold": 0.72,
        },
        "action": {
            "type": "bounded_react",
            "execution_mode": "bounded_react",
            "limits": {"max_steps": 6, "tool_budget": 4, "timeout_seconds": 20},
            "tools": ["get_job_profile", "get_candidate_profile", "get_question_bank"],
        },
        "constraints": {"duplicate_detection": True, "tool_cache": True, "sensitive_attribute_guard": True},
        "unique_rules": ["不生成与岗位无关的敏感个人信息问题，面试评价必须回到岗位能力维度。"],
        "rubric_rules": ["问题必须覆盖岗位要求与候选人履历差距，并给出追问目的和评分 rubric。"],
    },
]


async def seed_default_skills(store: DataStore) -> int:
    created = 0
    for template in DEFAULT_SKILLS:
        if await store.get_skill(template["_id"]) is not None:
            continue
        skill = {
            **template,
            "metrics": {"trigger_count": 0, "success_count": 0, "success_rate": 0.0, "avg_latency_ms": 0, "last_triggered": ""},
            "version": 1,
            "status": "active",
            "auto_generated": False,
            "confidence": 1.0,
            "gray_percent": 1.0,
            "created_by": "system_seed",
            "origin": "builtin_baseline",
            "created_at": _now(),
        }
        await store.upsert_skill(skill)
        await store.upsert("strategy_versions", {
            "_id": f"strategy_version_{skill['_id']}_v1_seed",
            "artifact_id": skill["_id"],
            "artifact_type": "skill",
            "version": 1,
            "reason": "builtin_baseline_seeded",
            "snapshot": skill,
            "created_at": _now(),
        })
        created += 1
    return created
