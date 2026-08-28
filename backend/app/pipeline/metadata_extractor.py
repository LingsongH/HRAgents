"""Enterprise policy metadata extraction plus local keyword extraction."""
from __future__ import annotations

from typing import Any

from app.llm.client import ChatMessage, LLMClient
from app.utils.logging import get_logger

logger = get_logger(__name__)

EXTRACT_PROMPT = """你是企业制度元数据抽取助手，仅输出 JSON：
{{
 "effective_date":null,
 "expiry_date":null,
 "doc_type":"policy|notice|guide|form|other",
 "policy_level":"group|company|department|team",
 "keywords":[],
 "applicable_scope":["all"],
 "allowed_roles":["employee"],
 "cross_refs":[],
 "parent_policy_title":""
}}
从制度原文抽取，不确定则留空；不得猜测权限或日期。
文档标题：{title}
文档文本：{text}
"""


class MetadataExtractor:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def extract(self, title: str, text: str) -> dict[str, Any]:
        try:
            data = await self.llm.complete_json([
                ChatMessage.system("你是严谨的企业制度元数据抽取助手。"),
                ChatMessage.user(EXTRACT_PROMPT.format(title=title, text=text[:6000])),
            ], temperature=0.0)
            return self._normalize(data)
        except Exception as exc:
            logger.warning("企业制度元数据抽取失败(%s)，使用安全默认值", exc)
            return self._normalize({})

    @staticmethod
    def _normalize(data: Any) -> dict[str, Any]:
        data = data if isinstance(data, dict) else {}
        doc_type = str(data.get("doc_type") or "other")
        if doc_type not in {"policy", "notice", "guide", "form", "other", "regulation"}:
            doc_type = "other"
        level = str(data.get("policy_level") or "company")
        if level not in {"group", "company", "department", "team"}:
            level = "company"
        roles = [str(x) for x in (data.get("allowed_roles") or []) if x]
        return {
            "effective_date": data.get("effective_date") or None,
            "expiry_date": data.get("expiry_date") or None,
            "doc_type": "policy" if doc_type == "regulation" else doc_type,
            "policy_level": level,
            "keywords": [str(k) for k in (data.get("keywords") or [])][:8],
            "applicable_scope": [str(x) for x in (data.get("applicable_scope") or [])][:12] or ["all"],
            "allowed_roles": roles or ["employee", "hr", "hr_admin", "admin"],
            "cross_refs": [str(r) for r in (data.get("cross_refs") or [])],
            "parent_policy_title": str(data.get("parent_policy_title") or ""),
        }


def extract_keywords(text: str, top_k: int = 6) -> list[str]:
    try:
        import jieba.analyse
        return jieba.analyse.extract_tags(text, topK=top_k, withWeight=False)
    except Exception:
        return []
