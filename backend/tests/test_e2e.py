"""企业 HR 端到端冒烟：入库 → 可信检索 → 员工问答（离线 fallback）。"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_ingest_and_answer(fresh_container, tmp_path):
    c = fresh_container
    await c.store.upsert_department({
        "_id": "dept_hr", "name": "人力资源部", "company_id": "default",
        "admin_users": [], "agent_config": {"model": "deepseek-v4-flash", "temperature": 0.1},
    })
    doc_file = tmp_path / "员工请假管理办法.txt"
    doc_file.write_text("第一章 请假\n第一条 员工请假应至少提前一个工作日提交审批。", encoding="utf-8")
    doc = await c.indexer.ingest(doc_file, dept_id="dept_hr", uploaded_by="test")
    assert doc["chunk_count"] >= 1
    assert doc["vector_status"] == "ready"
    result = await c.orchestrator.answer("请假需要提前多久？", user_id="u1", dept_ids=["dept_hr"])
    assert result["answer"]
    assert result["session_id"]
    events = await c.episodic_memory.session_events(result["session_id"], "u1")
    assert [event["type"] for event in events] == ["user_message", "assistant_message"]
    summary = await c.episodic_memory.get_summary(result["session_id"], "u1")
    assert summary and summary["summary"]


@pytest.mark.asyncio
async def test_loop_cycle_offline(fresh_container):
    c = fresh_container
    await c.rule_engine.seed_defaults()
    await c.hook_engine.seed_defaults()
    await c.feedback_collector.collect_explicit("s1", "u1", "研发弹性工作与考勤制度冲突吗", "答", "down")
    report = await c.loop_engine.run_cycle()
    assert "observed" in report
