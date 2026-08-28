"""Seed an enterprise HR demo: policy hierarchy, conflict HITL, interview profiles and Loop bad cases."""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.deps import build_container
from scripts.seed_data import DEPARTMENTS

POLICIES = [
    {
        "dept_id": "dept_hr", "title": "员工考勤管理办法", "policy_level": "company", "parent": "",
        "content": """# 员工考勤管理办法\n\n第一条 本办法适用于公司全体员工。\n\n第二条 标准工作时间为工作日9:00至18:00。\n\n第三条 员工每月迟到超过3次进入考勤异常复核。\n\n第四条 公司允许各业务部门在不降低法定工时与考勤记录要求的前提下制定弹性工作细则。""",
    },
    {
        "dept_id": "dept_rd", "title": "研发中心弹性工作管理规定", "policy_level": "department", "parent": "员工考勤管理办法",
        "content": """# 研发中心弹性工作管理规定\n\n第一条 本规定仅适用于研发中心员工。\n\n第二条 经直属负责人批准，员工可在8:00至10:00之间弹性到岗，但每日工作时长不得减少。\n\n第三条 弹性到岗仍须完成系统考勤记录。\n\n第四条 本规定依据《员工考勤管理办法》第四条授权制定。""",
    },
    {
        "dept_id": "dept_finance", "title": "员工差旅与费用报销制度", "policy_level": "company", "parent": "",
        "content": """# 员工差旅与费用报销制度\n\n第一条 员工差旅费用须在行程结束后30日内提交报销。\n\n第二条 单笔费用超过5000元须由部门负责人和财务负责人共同审批。\n\n第三条 发票与行程证明必须真实完整。""",
    },
    {
        "dept_id": "dept_admin", "title": "电脑租赁管理办法", "policy_level": "company", "parent": "",
        "content": """# 电脑租赁管理办法\n\n第一条 本办法适用于公司及控股子公司。\n\n第二条 所有电脑租赁需求必须纳入年度或项目预算，严禁无预算租赁。\n\n第三条 各公司行政部统一归口管理电脑租赁事项。""",
    },
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    for dept in DEPARTMENTS:
        row = {**dept, "company_id": "default", "created_at": now(), "updated_at": now()}
        await c.store.upsert_department(row)

    tmp = Path(tempfile.mkdtemp(prefix="hragents_demo_"))
    by_title: dict[str, dict] = {}
    try:
        # Parent policies first.
        for spec in sorted(POLICIES, key=lambda x: bool(x["parent"])):
            existing = next((d for d in await c.store.list_documents(dept_id=spec["dept_id"]) if d.get("title") == spec["title"]), None)
            if existing:
                by_title[spec["title"]] = existing
                continue
            path = tmp / f"{spec['title']}.md"
            path.write_text(spec["content"], encoding="utf-8")
            doc = await c.indexer.ingest(path, spec["dept_id"], "seed_demo")
            parent_id = by_title.get(spec["parent"], {}).get("_id") if spec["parent"] else None
            await c.store.update_document(doc["_id"], {
                "company_id": "default", "policy_state": "effective", "policy_level": spec["policy_level"],
                "parent_policy_id": parent_id, "visibility": "company", "allowed_dept_ids": [],
                "allowed_roles": ["employee", "hr", "hr_admin", "admin"],
            })
            by_title[spec["title"]] = await c.store.get_document(doc["_id"]) or doc
            claims = await c.policy_claim_extractor.rebuild_for_document(doc["_id"])
            print(f"[policy] {spec['title']} claims={len(claims)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    await c.store.upsert("job_profiles", {
        "_id": "job_ai_agent", "company_id": "default", "role": "大模型算法工程师",
        "must_have": ["Python", "RAG", "Agent Harness", "评测"], "nice_to_have": ["MCP", "多智能体"], "created_at": now(),
    })
    await c.store.upsert("candidate_profiles", {
        "_id": "candidate_001", "company_id": "default", "skills": ["Python", "RAG", "LoRA"],
        "projects": ["知识问答系统"], "created_at": now(),
    })
    await c.store.upsert("interview_question_bank", {
        "_id": "question_agent_harness", "role": "大模型算法工程师", "status": "active",
        "question": "为什么高风险业务不应该完全交给自由 ReAct？", "rubric": ["确定性 Workflow", "Tool Budget", "Verifier", "HITL"], "created_at": now(),
    })

    # A reviewed mistake becomes a real Loop input instead of a chat log.
    await c.store.insert_feedback({
        "_id": "fb_policy_hierarchy_demo", "session_id": "demo-policy-case", "user_id": "hr_admin",
        "query": "研发中心弹性工作规定与公司考勤制度是否冲突？",
        "answer": "模型误判为冲突", "kind": "explicit", "signal": "correction",
        "dept_ids": ["dept_hr", "dept_rd"], "intent_type": "policy_conflict",
        "detail": {
            "reason": "hierarchy_resolution_error",
            "loop_category": "policy_hierarchy",
            "correction": "研发制度属于公司考勤制度明确授权下的部门例外，应判 exception 而非 conflict",
            "rule": "parent_policy 明确允许部门自定义且下位规则不突破授权边界时，优先判 exception/supplement。",
        },
        "consumed": False, "created_at": now(),
    })
    print("[feedback] policy hierarchy bad case seeded")
    if c.mongo is not None:
        await c.mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
