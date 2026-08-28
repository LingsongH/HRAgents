"""测试 Loop 层（规则/钩子引擎 + Skill Miner 聚类）。"""
from __future__ import annotations

import pytest

from app.harness.base import Intent
from app.loop.hook_engine import HookEngine
from app.loop.rule_engine import RuleEngine
from app.loop.skill_miner import SkillMiner
from app.storage.store import MemoryStore


@pytest.mark.asyncio
async def test_rule_engine_seed_and_active():
    store = MemoryStore()
    engine = RuleEngine(store)
    await engine.seed_defaults()
    rules = await engine.active_rules()
    assert any(r["name"] == "no_guess_rule" for r in rules)


@pytest.mark.asyncio
async def test_hook_engine_apply():
    store = MemoryStore()
    engine = HookEngine(store)
    await engine.seed_defaults()
    hooks = await engine.active_hooks()
    intent = Intent(type="hr_process", depts=["dept_hr"], raw={"query": "差旅报销制度需要 HR 和财务怎么协同"})
    depts = await engine.apply(hooks, intent, ["dept_hr"])
    assert "dept_finance" in depts  # HR Hook 按业务关键词扩展财务检索


def test_skill_miner_cluster():
    miner = SkillMiner(MemoryStore(), llm=None, min_cluster=2)
    queries = ["请假制度是什么", "请假怎么申请", "报销怎么办理", "报销流程是什么"]
    # 用 hash 向量近似（维度一致即可）
    vecs = [[0.1, 0.2], [0.1, 0.25], [0.9, 0.8], [0.9, 0.85]]
    clusters = miner.cluster(queries, vecs)
    assert clusters  # 至少有一个簇


def test_skill_miner_keyword_fallback():
    miner = SkillMiner(MemoryStore(), llm=None, min_cluster=2)
    queries = ["请假制度是什么", "请假怎么申请", "请假审批流程"]
    clusters = miner._keyword_group(queries)
    assert clusters
