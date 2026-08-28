"""证明 HR 动态策略会改变下一轮行为，并可灰度与回滚。"""
from __future__ import annotations

import pytest

from app.loop.skill_executor import SkillExecutor
from app.loop.default_skills import seed_default_skills
from app.storage.store import MemoryStore


def _skill(percent: float = 1.0):
    return {
        "_id": "skill_hr_deadline", "name": "HR 截止时间", "scope": "global",
        "version": 2, "gray_percent": percent, "status": "active",
        "trigger": {"intent_patterns": ["截止"]},
        "action": {"type": "workflow", "steps": [
            {"step": 1, "action": "retrieve", "params": {"query": "{matter} 企业工作日历 截止日期", "top_k": 9}},
            {"step": 2, "action": "call_tool", "params": {"tool": "calendar_lookup"}},
        ]},
    }


@pytest.mark.asyncio
async def test_skill_treatment_changes_retrieval_plan():
    store = MemoryStore()
    executor = SkillExecutor(store, default_top_k=5)
    skill = _skill(1.0)
    await store.upsert_skill(skill)
    matched = await executor.matching("转正材料截止时间")
    plan = await executor.prepare("转正材料截止时间", ["转正材料截止时间"], matched, "session-1", "employee")
    assert plan.top_k == 9
    assert any("企业工作日历 截止日期" in q for q in plan.queries)
    assert any("企业工作日历" in text for text in plan.instructions)
    assert (await store.find("strategy_executions"))[0]["group"] == "treatment"


@pytest.mark.asyncio
async def test_default_hr_skills_are_idempotent_and_executable():
    store = MemoryStore()
    assert await seed_default_skills(store) == 3
    assert await seed_default_skills(store) == 0
    executor = SkillExecutor(store, default_top_k=5)
    matched = await executor.matching("新版考勤制度是否与研发弹性工作规定冲突", ["dept_hr"])
    assert [skill["name"] for skill in matched] == ["制度冲突分析"]
    plan = await executor.prepare(
        "新版考勤制度是否与研发弹性工作规定冲突",
        ["新版考勤制度是否与研发弹性工作规定冲突"], matched, "demo-session", "employee",
    )
    assert plan.top_k == 12
    assert plan.treatment_skills[0]["origin"] == "builtin_baseline"
    assert any("每个 material 结论" in instruction for instruction in plan.instructions)
    assert len(await store.find("strategy_versions")) == 3


@pytest.mark.asyncio
async def test_recent_traces_are_sorted_newest_first():
    store = MemoryStore()
    await store.insert_trace({"_id": "old", "created_at": "2026-01-01T00:00:00+00:00"})
    await store.insert_trace({"_id": "new", "created_at": "2026-02-01T00:00:00+00:00"})
    assert [row["_id"] for row in await store.list_recent_traces(limit=2)] == ["new", "old"]


@pytest.mark.asyncio
async def test_skill_control_keeps_baseline_plan():
    store = MemoryStore()
    executor = SkillExecutor(store, default_top_k=5)
    skill = _skill(0.0)
    plan = await executor.prepare("转正截止时间", ["转正截止时间"], [skill], "session-2", "employee")
    assert plan.top_k == 5
    assert plan.queries == ["转正截止时间"]
    assert not plan.instructions
    assert (await store.find("strategy_executions"))[0]["group"] == "control"


@pytest.mark.asyncio
async def test_department_rules_are_scoped(fresh_container):
    c = fresh_container
    await c.rule_engine.seed_defaults()
    await c.store.upsert_rule({
        "_id": "dept-rule", "name": "only-hr", "scope": "department",
        "dept_id": "dept_hr", "content": "仅 HR 部门规则", "priority": 50, "status": "active",
    })
    assert any(r["_id"] == "dept-rule" for r in await c.rule_engine.active_rules(["dept_hr"]))
    assert all(r["_id"] != "dept-rule" for r in await c.rule_engine.active_rules(["dept_finance"]))


@pytest.mark.asyncio
async def test_failed_experiment_rolls_back(fresh_container):
    c = fresh_container
    c.settings.loop_rollback_min_samples = 2
    c.settings.loop_rollback_margin = 0.1
    skill = _skill(0.5)
    await c.store.upsert_skill(skill)
    await c.store.upsert("experiments", {"_id": "exp-1", "artifact_id": skill["_id"], "status": "running"})
    for group, results in (("treatment", [False, False]), ("control", [True, True])):
        for i, success in enumerate(results):
            await c.store.upsert("strategy_executions", {
                "_id": f"{group}-{i}", "artifact_id": skill["_id"], "group": group, "success": success,
            })
    count = await c.loop_engine._rollback_failed_experiments()
    assert count == 1
    assert (await c.store.get_skill(skill["_id"]))["status"] == "deprecated"
    assert (await c.store.get("experiments", "exp-1"))["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_policy_hierarchy_feedback_has_own_root_cause(fresh_container):
    causes = fresh_container.loop_engine._heuristic_root_causes([{
        "signal": "correction",
        "detail": {"reason": "hierarchy_resolution_error", "loop_category": "policy_hierarchy"},
    }])
    assert causes == {"policy_hierarchy": 1}
