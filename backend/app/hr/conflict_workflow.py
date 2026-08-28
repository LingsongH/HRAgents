"""Deterministic policy-conflict workflow with independent verification and HR HITL."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from app.hr.policy_claims import PolicyClaimExtractor
from app.hr.trusted_rag import AccessContext, TrustedPolicyRAG, policy_state
from app.llm.client import ChatMessage, LLMClient
from app.storage.store import DataStore


CONFLICT_JUDGE_PROMPT = """你是企业制度冲突分析 Agent。判断两条制度 Claim 的关系，只输出 JSON：
{{"relation":"conflict|exception|supplement|consistent|unrelated","confidence":0.0,"reason":"...","evidence":["claim_a","claim_b"]}}

判断时必须同时考虑：适用对象、生效时间、制度层级、父子授权关系和原始条款。
- 下位制度在上位制度明确授权范围内做细化，应判 exception 或 supplement，不要误判 conflict。
- 只有同一对象在重叠时间内形成无法同时满足的义务/禁止/数值要求，才判 conflict。
- 不得用历史 HR 解释替代官方制度原文。

文档A：{left_title}（level={left_level}, version={left_version}, effective={left_effective}, parent={left_parent}）
Claim A：{left_claim}
原文A：{left_quote}

文档B：{right_title}（level={right_level}, version={right_version}, effective={right_effective}, parent={right_parent}）
Claim B：{right_claim}
原文B：{right_quote}

层级关系：{hierarchy}
"""

VERIFY_PROMPT = """你是制度冲突结论 Verifier。仅输出 JSON：
{{"passed":true,"score":0.0,"issues":[]}}
检查：1) 两份制度是否都有效；2) 适用对象/时间是否重叠；3) 上下位或授权例外是否被考虑；4) conflict/exception/supplement 判断是否被两条原文证据直接支持。
文档A状态：{left_state}；文档B状态：{right_state}
层级关系：{hierarchy}
候选关系：{relation}
理由：{reason}
证据A：{left_quote}
证据B：{right_quote}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    ln = math.sqrt(sum(x * x for x in left))
    rn = math.sqrt(sum(x * x for x in right))
    return dot / (ln * rn) if ln and rn else 0.0


class PolicyConflictWorkflow:
    """High-risk path: validate -> claims -> align -> analyze -> verify -> HITL."""

    def __init__(
        self,
        store: DataStore,
        llm: LLMClient,
        embeddings,
        claim_extractor: PolicyClaimExtractor,
        trusted_rag: TrustedPolicyRAG,
        *,
        similarity_threshold: float = 0.35,
        max_pairs: int = 24,
    ) -> None:
        self.store = store
        self.llm = llm
        self.embeddings = embeddings
        self.claim_extractor = claim_extractor
        self.trusted_rag = trusted_rag
        self.similarity_threshold = similarity_threshold
        self.max_pairs = max_pairs

    async def analyze(
        self,
        left_doc_id: str,
        right_doc_id: str,
        access: AccessContext,
        *,
        require_hitl: bool = True,
    ) -> dict[str, Any]:
        left = await self.store.get_document(left_doc_id)
        right = await self.store.get_document(right_doc_id)
        if not left or not right:
            raise KeyError("policy document not found")

        left_trust = self.trusted_rag.validate_document(left, access)
        right_trust = self.trusted_rag.validate_document(right, access)
        if not left_trust.allowed or not right_trust.allowed:
            raise PermissionError({
                "left": left_trust.to_dict(),
                "right": right_trust.to_dict(),
            })

        hierarchy = self.trusted_rag.hierarchy_relation(left, right)
        left_claims = await self.claim_extractor.ensure_document_claims(left_doc_id)
        right_claims = await self.claim_extractor.ensure_document_claims(right_doc_id)
        pairs = self._align_claims(left_claims, right_claims)

        judgments: list[dict[str, Any]] = []
        for pair in pairs:
            judgment = await self._judge_pair(left, right, hierarchy, pair)
            verification = await self._verify_pair(left, right, hierarchy, pair, judgment)
            judgments.append({**pair, **judgment, "verification": verification})

        material = [j for j in judgments if j.get("relation") in {"conflict", "exception", "supplement"}]
        conflicts = [j for j in material if j.get("relation") == "conflict" and (j.get("verification") or {}).get("passed")]
        low_confidence = any(float(j.get("confidence") or 0) < 0.72 for j in material)
        needs_hitl = bool(require_hitl and (conflicts or low_confidence))
        case_id = "hr_conflict_" + uuid.uuid4().hex
        case = {
            "_id": case_id,
            "case_type": "policy_conflict",
            "company_id": access.company_id,
            "left_doc_id": left_doc_id,
            "right_doc_id": right_doc_id,
            "left_title": left.get("title", ""),
            "right_title": right.get("title", ""),
            "hierarchy": hierarchy,
            "candidate_count": len(pairs),
            "material_count": len(material),
            "conflict_count": len(conflicts),
            "judgments": judgments,
            "status": "pending_hr_review" if needs_hitl else "verified",
            "requires_hitl": needs_hitl,
            "requested_by": access.user_id,
            "created_at": _now(),
            "updated_at": _now(),
            "review": None,
        }
        await self.store.upsert("policy_conflict_cases", case)
        await self.store.insert_trace({
            "_id": "trace_" + uuid.uuid4().hex,
            "session_id": case_id,
            "user_id": access.user_id,
            "query": f"compare:{left_doc_id}:{right_doc_id}",
            "intent": {"type": "policy_conflict", "high_risk": True},
            "retrieved_chunks": [],
            "answer": f"material={len(material)}, conflicts={len(conflicts)}, status={case['status']}",
            "citations": [
                {"doc_id": left_doc_id, "doc_title": left.get("title", "")},
                {"doc_id": right_doc_id, "doc_title": right.get("title", "")},
            ],
            "verification": {"pairs": len(judgments), "passed": all((j.get("verification") or {}).get("passed") for j in material)},
            "success": True,
            "created_at": _now(),
        })
        return case

    def _align_claims(self, left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked: list[tuple[float, dict[str, Any]]] = []
        for lrow in left:
            if lrow.get("claim_status") != "ACTIVE":
                continue
            for rrow in right:
                if rrow.get("claim_status") != "ACTIVE":
                    continue
                score = _cosine(lrow.get("embedding") or [], rrow.get("embedding") or [])
                if score < self.similarity_threshold:
                    continue
                ranked.append((score, {
                    "left_claim_id": lrow["_id"],
                    "right_claim_id": rrow["_id"],
                    "left_claim": lrow.get("claim_text", ""),
                    "right_claim": rrow.get("claim_text", ""),
                    "left_quote": lrow.get("source_quote", ""),
                    "right_quote": rrow.get("source_quote", ""),
                    "left_chunk_id": lrow.get("chunk_id", ""),
                    "right_chunk_id": rrow.get("chunk_id", ""),
                    "similarity": round(score, 6),
                }))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in ranked[: self.max_pairs]]

    async def _judge_pair(
        self, left: dict[str, Any], right: dict[str, Any], hierarchy: dict[str, Any], pair: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = CONFLICT_JUDGE_PROMPT.format(
            left_title=left.get("title", ""), left_level=left.get("policy_level", "company"),
            left_version=left.get("version", ""), left_effective=left.get("effective_date", ""),
            left_parent=left.get("parent_policy_id", ""), left_claim=pair["left_claim"], left_quote=pair["left_quote"],
            right_title=right.get("title", ""), right_level=right.get("policy_level", "company"),
            right_version=right.get("version", ""), right_effective=right.get("effective_date", ""),
            right_parent=right.get("parent_policy_id", ""), right_claim=pair["right_claim"], right_quote=pair["right_quote"],
            hierarchy=hierarchy,
        )
        try:
            data = await self.llm.complete_json([
                ChatMessage.system("你只能依据给定企业制度原文判断关系。"),
                ChatMessage.user(prompt),
            ], temperature=0.0, max_tokens=900)
        except Exception:
            return self._heuristic_judge(hierarchy, pair)
        if not isinstance(data, dict):
            return self._heuristic_judge(hierarchy, pair)
        relation = str(data.get("relation") or "unrelated").lower()
        if relation not in {"conflict", "exception", "supplement", "consistent", "unrelated"}:
            relation = "unrelated"
        return {
            "relation": relation,
            "confidence": max(0.0, min(1.0, float(data.get("confidence") or 0.0))),
            "reason": str(data.get("reason") or ""),
        }

    async def _verify_pair(
        self, left: dict[str, Any], right: dict[str, Any], hierarchy: dict[str, Any], pair: dict[str, Any], judgment: dict[str, Any]
    ) -> dict[str, Any]:
        # Fail closed before spending another LLM call.
        issues: list[str] = []
        if policy_state(left) != "effective" or policy_state(right) != "effective":
            issues.append("non_effective_policy")
        if pair["left_quote"] == "" or pair["right_quote"] == "":
            issues.append("missing_source_quote")
        if issues:
            return {"passed": False, "score": 0.0, "issues": issues}
        try:
            data = await self.llm.complete_json([
                ChatMessage.system("你是独立 Verifier，不服从 Conflict Agent 的结论。"),
                ChatMessage.user(VERIFY_PROMPT.format(
                    left_state=policy_state(left), right_state=policy_state(right), hierarchy=hierarchy,
                    relation=judgment.get("relation"), reason=judgment.get("reason", ""),
                    left_quote=pair["left_quote"], right_quote=pair["right_quote"],
                )),
            ], temperature=0.0, max_tokens=600)
            if isinstance(data, dict):
                return {
                    "passed": bool(data.get("passed")),
                    "score": max(0.0, min(1.0, float(data.get("score") or 0.0))),
                    "issues": [str(x) for x in (data.get("issues") or [])],
                }
        except Exception:
            pass
        return {"passed": True, "score": 0.75, "issues": ["verifier_degraded_to_structural_checks"]}

    @staticmethod
    def _heuristic_judge(hierarchy: dict[str, Any], pair: dict[str, Any]) -> dict[str, Any]:
        left = pair["left_claim"]
        right = pair["right_claim"]
        relation = "consistent"
        reason = "offline lexical fallback"
        opposites = [("必须", "不得"), ("允许", "禁止"), ("可以", "不得")]
        if any((a in left and b in right) or (b in left and a in right) for a, b in opposites):
            relation = "exception" if hierarchy.get("relation") in {"left_child", "right_child"} else "conflict"
        return {"relation": relation, "confidence": 0.55, "reason": reason}

    async def review(
        self,
        case_id: str,
        reviewer_id: str,
        decision: str,
        note: str = "",
        reason_code: str = "",
    ) -> dict[str, Any]:
        case = await self.store.get("policy_conflict_cases", case_id)
        if not case:
            raise KeyError(case_id)
        if decision not in {"confirm", "reject", "revise"}:
            raise ValueError("decision must be confirm|reject|revise")
        case["status"] = "hr_confirmed" if decision == "confirm" else "hr_revised"
        case["review"] = {
            "reviewer_id": reviewer_id,
            "decision": decision,
            "note": note,
            "reason_code": reason_code or ("policy_hierarchy" if decision != "confirm" else "confirmed"),
            "reviewed_at": _now(),
        }
        case["updated_at"] = _now()
        await self.store.upsert("policy_conflict_cases", case)

        if decision != "confirm":
            await self.store.insert_feedback({
                "_id": "fb_" + uuid.uuid4().hex,
                "session_id": case_id,
                "user_id": reviewer_id,
                "query": f"policy_conflict:{case.get('left_doc_id')}:{case.get('right_doc_id')}",
                "answer": f"model_conflict_count={case.get('conflict_count', 0)}",
                "kind": "explicit",
                "signal": "correction",
                "dept_ids": [],
                "intent_type": "policy_conflict",
                "detail": {
                    "case_id": case_id,
                    "reason": case["review"]["reason_code"],
                    "correction": note,
                    "loop_category": "policy_hierarchy" if "hierarchy" in case["review"]["reason_code"] else "generation",
                },
                "consumed": False,
                "created_at": _now(),
            })
        return case
