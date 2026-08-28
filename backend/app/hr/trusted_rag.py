"""Permission-aware Trusted RAG for enterprise policy evidence.

Retrieval score alone never makes a chunk usable evidence.  Every candidate is
resolved back to its policy document and validated against tenant/company,
department/role ACL, lifecycle, effective time and document hierarchy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable

from app.storage.store import DataStore


EFFECTIVE_RUNTIME_STATUSES = {"active", "effective"}
DEPRECATED_RUNTIME_STATUSES = {"archived", "deprecated", "deleted"}
AUTHORITY_RANK = {
    "official_policy": 500,
    "hr_review": 400,
    "enterprise_faq": 300,
    "user_input": 200,
    "conversation_summary": 100,
    "model_inference": 0,
}
POLICY_LEVEL_RANK = {
    "law": 500,
    "group": 400,
    "company": 300,
    "department": 200,
    "team": 100,
}


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def policy_state(doc: dict[str, Any]) -> str:
    """Return enterprise lifecycle while preserving legacy runtime status."""
    explicit = str(doc.get("policy_state") or "").lower()
    if explicit in {"effective", "deprecated", "draft", "review", "deleted"}:
        return explicit
    status = str(doc.get("status") or "").lower()
    if status in EFFECTIVE_RUNTIME_STATUSES:
        return "effective"
    if status in {"archived", "deprecated"}:
        return "deprecated"
    return status or "draft"


@dataclass(slots=True)
class AccessContext:
    """Identity/ACL context used before evidence can enter the fact plane."""

    user_id: str = "anonymous"
    company_id: str = "default"
    dept_ids: list[str] = field(default_factory=list)
    role: str = "employee"
    as_of: str = field(default_factory=_today_iso)

    @classmethod
    def from_user(
        cls,
        user: dict[str, Any] | None,
        *,
        company_id: str = "",
        dept_ids: Iterable[str] | None = None,
        as_of: str = "",
    ) -> "AccessContext":
        user = user or {}
        user_dept = str(user.get("dept_id") or "")
        merged_depts = [str(x) for x in (dept_ids or []) if x]
        if user_dept and user_dept not in merged_depts:
            merged_depts.append(user_dept)
        return cls(
            user_id=str(user.get("id") or user.get("_id") or "anonymous"),
            company_id=company_id or str(user.get("company_id") or "default"),
            dept_ids=merged_depts,
            role=str(user.get("role") or "employee"),
            as_of=as_of or _today_iso(),
        )


@dataclass(slots=True)
class EvidenceDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    authority: str = "official_policy"
    authority_rank: int = 500
    policy_level_rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": self.reasons,
            "authority": self.authority,
            "authority_rank": self.authority_rank,
            "policy_level_rank": self.policy_level_rank,
        }


class TrustedPolicyRAG:
    """Validate and enrich retrieved chunks before they become evidence."""

    def __init__(self, store: DataStore) -> None:
        self.store = store

    @staticmethod
    def validate_document(doc: dict[str, Any], access: AccessContext) -> EvidenceDecision:
        reasons: list[str] = []
        state = policy_state(doc)
        if state != "effective":
            reasons.append(f"policy_state={state}")

        doc_company = str(doc.get("company_id") or "default")
        if access.company_id and doc_company != access.company_id:
            reasons.append("company_acl_denied")

        allowed_depts = [str(x) for x in (doc.get("allowed_dept_ids") or []) if x]
        owner_dept = str(doc.get("dept_id") or "")
        privileged = access.role in {"admin", "hr", "hr_admin"}
        if allowed_depts and not privileged:
            if not access.dept_ids or not set(allowed_depts).intersection(access.dept_ids):
                reasons.append("department_acl_denied")
        visibility = str(doc.get("visibility") or "company")
        if visibility == "department" and not privileged:
            visible_depts = set(allowed_depts or ([owner_dept] if owner_dept else []))
            if visible_depts and (not access.dept_ids or not visible_depts.intersection(access.dept_ids)):
                reasons.append("department_visibility_denied")

        allowed_roles = [str(x) for x in (doc.get("allowed_roles") or []) if x]
        if allowed_roles and access.role not in allowed_roles and access.role != "admin":
            reasons.append("role_acl_denied")

        as_of = _as_date(access.as_of) or datetime.now(timezone.utc).date()
        effective = _as_date(doc.get("effective_date"))
        expiry = _as_date(doc.get("expiry_date"))
        if effective and effective > as_of:
            reasons.append("not_yet_effective")
        if expiry and expiry < as_of:
            reasons.append("expired")

        level = str(doc.get("policy_level") or "company").lower()
        return EvidenceDecision(
            allowed=not reasons,
            reasons=reasons,
            authority="official_policy",
            authority_rank=AUTHORITY_RANK["official_policy"],
            policy_level_rank=POLICY_LEVEL_RANK.get(level, 0),
        )

    async def validate_chunk(self, chunk: dict[str, Any], access: AccessContext) -> dict[str, Any] | None:
        doc = await self.store.get_document(str(chunk.get("doc_id") or ""))
        if not doc:
            return None
        decision = self.validate_document(doc, access)
        if not decision.allowed:
            return None
        result = dict(chunk)
        result["doc_title"] = doc.get("title", "")
        result["document_version"] = doc.get("version", "")
        result["policy_state"] = policy_state(doc)
        result["policy_level"] = doc.get("policy_level", "company")
        result["company_id"] = doc.get("company_id", "default")
        result["evidence_authority"] = decision.authority
        result["evidence_rank"] = decision.authority_rank
        result["policy_level_rank"] = decision.policy_level_rank
        result["trust_validation"] = decision.to_dict()
        return result

    async def filter_chunks(self, chunks: list[dict[str, Any]], access: AccessContext) -> list[dict[str, Any]]:
        verified: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in chunks:
            chunk_id = str(candidate.get("_id") or candidate.get("id") or "")
            if not chunk_id or chunk_id in seen:
                continue
            stored = await self.store.get("chunks", chunk_id) or candidate
            trusted = await self.validate_chunk(stored, access)
            if trusted:
                trusted.update({k: v for k, v in candidate.items() if k not in trusted})
                verified.append(trusted)
                seen.add(chunk_id)
        verified.sort(
            key=lambda row: (
                int(row.get("evidence_rank", 0)),
                int(row.get("policy_level_rank", 0)),
                float(row.get("rerank_score", row.get("_rrf", row.get("score", 0.0))) or 0.0),
            ),
            reverse=True,
        )
        return verified

    async def document_snapshot(self, doc_id: str, access: AccessContext) -> dict[str, Any]:
        doc = await self.store.get_document(doc_id)
        if not doc:
            raise KeyError(f"policy document not found: {doc_id}")
        decision = self.validate_document(doc, access)
        return {**doc, "policy_state": policy_state(doc), "trust_validation": decision.to_dict()}

    @staticmethod
    def hierarchy_relation(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        left_id = str(left.get("_id") or "")
        right_id = str(right.get("_id") or "")
        left_parent = str(left.get("parent_policy_id") or "")
        right_parent = str(right.get("parent_policy_id") or "")
        if left_parent and left_parent == right_id:
            return {"relation": "left_child", "parent": right_id, "child": left_id}
        if right_parent and right_parent == left_id:
            return {"relation": "right_child", "parent": left_id, "child": right_id}
        l_rank = POLICY_LEVEL_RANK.get(str(left.get("policy_level") or "company"), 0)
        r_rank = POLICY_LEVEL_RANK.get(str(right.get("policy_level") or "company"), 0)
        if l_rank > r_rank:
            return {"relation": "left_higher", "parent": left_id, "child": right_id}
        if r_rank > l_rank:
            return {"relation": "right_higher", "parent": right_id, "child": left_id}
        return {"relation": "peer", "parent": "", "child": ""}
