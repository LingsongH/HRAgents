"""HRAgents 企业场景新增能力的安全与执行边界测试。"""
from __future__ import annotations

import pytest

from app.hr.bounded_react import BoundedReActRuntime
from app.hr.policy_claims import PolicyClaimExtractor
from app.hr.trusted_rag import AccessContext, TrustedPolicyRAG, policy_state


def _policy(**overrides):
    doc = {
        "_id": "p1", "company_id": "acme", "dept_id": "dept_hr",
        "status": "active", "policy_state": "effective", "policy_level": "company",
        "visibility": "company", "allowed_dept_ids": [], "allowed_roles": [],
        "effective_date": "2026-01-01", "expiry_date": None,
    }
    doc.update(overrides)
    return doc


def test_trusted_rag_tenant_acl_is_strict():
    denied = TrustedPolicyRAG.validate_document(
        _policy(company_id="company_a"),
        AccessContext(user_id="u", company_id="company_b", dept_ids=["dept_hr"], role="employee", as_of="2026-08-28"),
    )
    assert not denied.allowed
    assert "company_acl_denied" in denied.reasons


def test_trusted_rag_department_acl_uses_identity_not_route():
    doc = _policy(visibility="department", allowed_dept_ids=["dept_rd"])
    allowed = TrustedPolicyRAG.validate_document(
        doc, AccessContext(user_id="rd", company_id="acme", dept_ids=["dept_rd"], role="employee", as_of="2026-08-28")
    )
    denied = TrustedPolicyRAG.validate_document(
        doc, AccessContext(user_id="finance", company_id="acme", dept_ids=["dept_finance"], role="employee", as_of="2026-08-28")
    )
    assert allowed.allowed
    assert not denied.allowed and "department_acl_denied" in denied.reasons


def test_trusted_rag_rejects_deprecated_and_future_policy():
    deprecated = TrustedPolicyRAG.validate_document(
        _policy(policy_state="deprecated"),
        AccessContext(company_id="acme", dept_ids=["dept_hr"], role="employee", as_of="2026-08-28"),
    )
    future = TrustedPolicyRAG.validate_document(
        _policy(effective_date="2027-01-01"),
        AccessContext(company_id="acme", dept_ids=["dept_hr"], role="employee", as_of="2026-08-28"),
    )
    assert not deprecated.allowed and "policy_state=deprecated" in deprecated.reasons
    assert not future.allowed and "not_yet_effective" in future.reasons
    assert policy_state({"status": "archived"}) == "deprecated"


def test_policy_hierarchy_parent_relation():
    parent = _policy(_id="company_policy", policy_level="company")
    child = _policy(_id="rd_policy", policy_level="department", parent_policy_id="company_policy")
    relation = TrustedPolicyRAG.hierarchy_relation(parent, child)
    assert relation == {"relation": "right_child", "parent": "company_policy", "child": "rd_policy"}


def test_claim_fallback_keeps_auditable_source_quote():
    rows = PolicyClaimExtractor._fallback_claims("员工应当按时打卡。研发中心允许在授权范围内实行弹性工作。背景介绍。")
    assert len(rows) == 2
    assert all(row["claim_text"] == row["source_quote"] for row in rows)


@pytest.mark.asyncio
async def test_bounded_react_reuses_cache_and_stops_duplicate_loop():
    runtime = BoundedReActRuntime(max_steps=6, tool_budget=4, timeout_seconds=2)
    tool_calls = 0

    async def tool(args):
        nonlocal tool_calls
        tool_calls += 1
        return {"value": args["id"]}

    async def planner(history, context):
        # Deliberately repeat the same tool request. Runtime must not execute it twice.
        return {"action": "tool", "tool": "lookup", "args": {"id": "same"}}

    result = await runtime.run(planner, {"lookup": tool}, {})
    assert tool_calls == 1
    assert result["trace"]["duplicate_calls"] == 2
    assert result["trace"]["cache_hits"] == 2
    assert result["trace"]["stopped_reason"] == "duplicate_call_guard"


@pytest.mark.asyncio
async def test_bounded_react_enforces_tool_budget():
    runtime = BoundedReActRuntime(max_steps=6, tool_budget=2, timeout_seconds=2)

    async def tool(args):
        return args

    async def planner(history, context):
        return {"action": "tool", "tool": "lookup", "args": {"n": len(history)}}

    result = await runtime.run(planner, {"lookup": tool}, {})
    assert result["trace"]["tool_calls"] == 2
    assert result["trace"]["stopped_reason"] == "tool_budget_exhausted"
