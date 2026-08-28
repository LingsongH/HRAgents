"""测试企业 HR Agent 的无 LLM 回退逻辑。"""
from __future__ import annotations

import asyncio

from app.harness.base import Answer, VerificationResult
from app.harness.agents.intent_agent import IntentAgent
from app.harness.agents.query_rewriter import QueryRewriter
from app.harness.agents.verifier_agent import VerifierAgent


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    async def complete(self, *a, **kw):
        self.calls += 1
        raise RuntimeError("no llm")

    async def complete_json(self, *a, **kw):
        self.calls += 1
        raise RuntimeError("no llm")


class _MemStore:
    async def list_departments(self):
        return [
            {"_id": "dept_hr", "name": "人力资源部"},
            {"_id": "dept_legal", "name": "法务与合规部"},
            {"_id": "dept_rd", "name": "研发中心"},
        ]

    async def get_user_profile(self, user_id):
        return None

    async def list_glossary(self):
        return []


def test_intent_fallback_policy_conflict():
    agent = IntentAgent(_FakeLLM(), _MemStore())
    intent = asyncio.run(agent.infer("研发中心弹性工作规定和公司考勤制度有没有冲突？", "u1"))
    assert intent.type == "policy_conflict"
    assert "dept_rd" in intent.depts
    assert intent.raw["high_risk"] is True
    assert intent.raw["execution_mode"] == "graph"


def test_intent_fallback_interview_is_bounded_react():
    agent = IntentAgent(_FakeLLM(), _MemStore())
    intent = asyncio.run(agent.infer("帮我生成大模型算法工程师面试题", "u1"))
    assert intent.type == "interview"
    assert "dept_hr" in intent.depts
    assert intent.raw["execution_mode"] == "bounded_react"


def test_query_rewriter_fallback():
    rw = QueryRewriter(_FakeLLM(), _MemStore())
    queries = asyncio.run(rw.rewrite("差旅报销怎么办", None))
    assert queries and queries[0] == "差旅报销怎么办"


def test_verifier_heuristic():
    v = VerifierAgent(_FakeLLM())
    answer = Answer(content="根据有效制度，研发中心可在授权范围内执行弹性工作。[来源1]", citations=[])
    result = asyncio.run(v.verify("研发弹性工作规则", answer, []))
    assert isinstance(result, VerificationResult)
