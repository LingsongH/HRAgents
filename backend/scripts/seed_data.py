"""Seed enterprise HR departments, glossary, baseline Rules/Hooks/Skills and interview demo data."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import get_settings
from app.deps import build_container
from app.loop.default_skills import seed_default_skills

DEPARTMENTS = [
    {"_id": "dept_hr", "name": "人力��j源部", "name_en": "Human Resources", "category": "hr"},
    {"_id": "dept_admin", "name": "行政部", "name_en": "Administration", "category": "admin"},
    {"_id": "dept_finance", "name": "财务部", "name_en": "Finance", "category": "finance"},
    {"_id": "dept_legal", "name": "法务与合规部", "name_en": "Legal & Compliance", "category": "legal"},
    {"_id": "dept_rd", "name": "研发中心", "name_en": "R&D Center", "category": "business"},
]

GLOSSARY = [
    {"canonical": "弹性工作", "synonyms": ["弹性工时", "flexible work", "灵活考勤"]},
    {"canonical": "制度冲突", "synonyms": ["规则冲突", "条款冲突", "制度矛盾"]},
    {"canonical": "员工手册", "synonyms": ["员工制度", "HR制度", "人事制度"]},
    {"canonical": "候选人画像", "synonyms": ["人才画像", "candidate profile"]},
]


async def main() -> None:
    settings = get_settings()
    c = build_container(settings)
    if c.mongo is not None:
        await c.mongo.connect()
    if hasattr(c.session_store, "connect"):
        try:
            await c.session_store.connect()
        except Exception:
            pass
    now = datetime.now(timezone.utc).isoformat()
    for dept in DEPARTMENTS:
        row = {
            **dept,
            "company_id": "default",
            "admin_users": [],
            "agent_config": {"mode": "shared_harness", "high_risk": dept["_id"] in {"dept_hr", "dept_legal"}},
            "loop_phase": "human_in_loop",
            "review_stats": {"total": 0, "correct": 0, "accuracy": 0.0},
            "created_at": now,
            "updated_at": now,
        }
        await c.store.upsert_department(row)
        print(f"[dept] {row['_id']} {row['name']}")
    for i, item in enumerate(GLOSSARY):
        await c.store.upsert_glossary({
            "_id": f"glossary_hr_{i}", **item, "dept_id": "", "created_by": "seed", "created_at": now,
        })
    await c.rule_engine.seed_defaults()
    await c.hook_engine.seed_defaults()
    print(f"[skills] created={await seed_default_skills(c.store)}")

    # Minimal bounded-ReAct interview fixtures. Production data should come from HRIS/ATS via MCP.
    await c.store.upsert("job_profiles", {
        "_id": "job_backend_ai", "company_id": "default", "role": "大模型算法工程师",
        "must_have": ["Python", "RAG", "Agent", "评测"], "nice_to_have": ["MCP", "多智能体"], "created_at": now,
    })
    await c.store.upsert("candidate_profiles", {
        "_id": "candidate_demo", "company_id": "default", "skills": ["Python", "RAG", "LoRA"],
        "projects": ["企业知识问答"], "created_at": now,
    })
    await c.store.upsert("interview_question_bank", {
        "_id": "qbank_agent_1", "role": "大模型算法工程师", "status": "active",
        "question": "请设计一个高可信企业 RAG 的事实与记忆边界。", "rubric": ["ACL", "版本", "Verifier", "Fact Plane"],
        "created_at": now,
    })
    if c.mongo is not None:
        await c.mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
