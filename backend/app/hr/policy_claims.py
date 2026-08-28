"""Policy Claim extraction inspired by the validated local claim prototype.

A Claim is the smallest auditable policy proposition.  It keeps both normalized
meaning and an exact source quote so conflict analysis never loses provenance.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.llm.client import ChatMessage, LLMClient
from app.storage.store import DataStore


CLAIM_PROMPT = """你是企业制度 Claim 抽取器。请把一个制度条款切片拆成最小、可独立判断真假的规则命题。
只输出 JSON：
{{"claims":[{{"claim_text":"规范化命题","source_quote":"必须原样来自输入文本","subject":"适用主体","action":"行为/义务","condition":"条件","exception":"例外条件"}}]}}

约束：
1. 不补充输入中不存在的规则；source_quote 必须是输入文本的连续原文。
2. 一条 Claim 只表达一个约束、权限、条件、数值、时间或例外。
3. “本制度/本办法”可在 claim_text 中替换为文档标题以消除指代，但 source_quote 保持原文。
4. 无制度性命题时返回 {{"claims":[]}}。

文档：{title}
章节：{section}
文本：
{content}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(chunk_id: str, text: str, version: str) -> str:
    return hashlib.sha256(f"{chunk_id}\0{text}\0{version}".encode("utf-8")).hexdigest()


class PolicyClaimExtractor:
    def __init__(self, store: DataStore, llm: LLMClient, embeddings) -> None:
        self.store = store
        self.llm = llm
        self.embeddings = embeddings
        self.extractor_version = f"hr-policy-claim-v1@{getattr(llm, 'model', 'unknown')}"

    async def rebuild_for_document(self, doc_id: str) -> list[dict[str, Any]]:
        doc = await self.store.get_document(doc_id)
        if not doc:
            raise KeyError(f"document not found: {doc_id}")
        chunks = await self.store.list_chunks_by_doc(doc_id)
        old = await self.store.find("policy_claims", {"doc_id": doc_id})
        for row in old:
            await self.store.delete("policy_claims", row["_id"])

        claims: list[dict[str, Any]] = []
        for chunk in sorted(chunks, key=lambda x: int(x.get("chunk_index", 0))):
            extracted = await self.extract_chunk(doc, chunk)
            claims.extend(extracted)

        if claims:
            vectors = await self.embeddings.embed([row["claim_text"] for row in claims])
            for row, vector in zip(claims, vectors, strict=True):
                row["embedding"] = [float(x) for x in vector]
                row["embedding_model"] = getattr(self.embeddings, "model", "")
                row["embedding_dimension"] = len(vector)
                await self.store.upsert("policy_claims", row)
        await self.store.update_document(doc_id, {
            "claim_count": len(claims),
            "claim_extractor_version": self.extractor_version,
            "claims_updated_at": _now(),
        })
        return claims

    async def ensure_document_claims(self, doc_id: str) -> list[dict[str, Any]]:
        rows = await self.store.find("policy_claims", {"doc_id": doc_id})
        if rows:
            return rows
        return await self.rebuild_for_document(doc_id)

    async def extract_chunk(self, doc: dict[str, Any], chunk: dict[str, Any]) -> list[dict[str, Any]]:
        content = str(chunk.get("content") or "").strip()
        if not content:
            return []
        payload: Any = None
        try:
            payload = await self.llm.complete_json([
                ChatMessage.system("你只抽取可追溯的企业制度 Claim，不得创造规则。"),
                ChatMessage.user(CLAIM_PROMPT.format(
                    title=doc.get("title", ""),
                    section=" / ".join(chunk.get("section_path") or []) or chunk.get("section_title", ""),
                    content=content[:5000],
                )),
            ], temperature=0.0, max_tokens=1600)
        except Exception:
            payload = {"claims": self._fallback_claims(content)}
        raw_claims = payload.get("claims", []) if isinstance(payload, dict) else []
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        chunk_id = str(chunk.get("_id") or chunk.get("id") or "")
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            text = re.sub(r"\s+", " ", str(item.get("claim_text") or "")).strip()
            quote = str(item.get("source_quote") or "").strip()
            if not text or not quote or quote not in content:
                continue
            fp = _fingerprint(chunk_id, text, self.extractor_version)
            if fp in seen:
                continue
            seen.add(fp)
            normalized.append({
                "_id": "claim_" + uuid.uuid4().hex,
                "doc_id": doc["_id"],
                "chunk_id": chunk_id,
                "dept_id": doc.get("dept_id", ""),
                "company_id": doc.get("company_id", "default"),
                "document_version": doc.get("version", ""),
                "policy_level": doc.get("policy_level", "company"),
                "claim_text": text,
                "source_quote": quote,
                "subject": str(item.get("subject") or ""),
                "action": str(item.get("action") or ""),
                "condition": str(item.get("condition") or ""),
                "exception": str(item.get("exception") or ""),
                "claim_status": "ACTIVE",
                "extractor_version": self.extractor_version,
                "fingerprint": fp,
                "created_at": _now(),
            })
        return normalized

    @staticmethod
    def _fallback_claims(content: str) -> list[dict[str, str]]:
        """Deterministic fallback used for offline tests/degraded mode."""
        parts = re.split(r"(?<=[。；;！？!?])\s*|\n+", content)
        rows: list[dict[str, str]] = []
        for part in parts:
            quote = part.strip()
            if len(quote) < 8:
                continue
            if not re.search(r"应当|应该|必须|不得|严禁|可以|允许|需要|禁止|适用|负责|期限|时间|标准|比例|天|次|%", quote):
                continue
            rows.append({
                "claim_text": quote,
                "source_quote": quote,
                "subject": "",
                "action": "",
                "condition": "",
                "exception": "",
            })
        return rows[:20]
