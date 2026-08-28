"""Enterprise department router for HR knowledge and workflow requests."""
from __future__ import annotations

from typing import Any

from app.llm.client import ChatMessage, LLMClient
from app.storage.store import DataStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

DEPT_KEYWORDS: dict[str, list[str]] = {
    "dept_hr": ["人事", "HR", "招聘", "入职", "离职", "考勤", "请假", "绩效", "员工手册", "面试", "候选人", "劳动合同"],
    "dept_admin": ["行政", "办公", "用车", "采购", "电脑租赁", "资产", "会议室", "门禁", "差旅"],
    "dept_finance": ["财务", "报销", "发票", "预算", "付款", "费用", "借款", "差旅费"],
    "dept_legal": ["法务", "合规", "合同", "法律", "授权", "制度层级", "冲突", "风险"],
    "dept_rd": ["研发中心", "研发", "弹性工作", "技术岗位", "研发岗位"],
}

ROUTE_PROMPT = """你是企业部门路由助手。判断员工/HR 请求最应由哪些部门共同处理，仅输出 JSON：
{{"depts":["dept_id"],"confidence":0.0,"reason":"简短理由"}}
候选部门：{departments}
请求：{query}
"""


class DeptRouter:
    def __init__(self, llm: LLMClient, store: DataStore) -> None:
        self.llm = llm
        self.store = store

    async def route(self, query: str) -> dict[str, Any]:
        departments = await self.store.list_departments()
        dept_names = {d["_id"]: d.get("name", d["_id"]) for d in departments}
        valid = set(dept_names)
        matched, reasons = self._keyword_match(query, valid)
        if matched:
            return self._result(matched, "keyword", min(0.95, 0.65 + 0.08 * len(matched)), reasons, dept_names)
        try:
            desc = ", ".join(f"{d['_id']}({d.get('name', '')})" for d in departments) or "dept_all(全企业)"
            data = await self.llm.complete_json([
                ChatMessage.system("你是企业部门路由助手。"),
                ChatMessage.user(ROUTE_PROMPT.format(departments=desc, query=query)),
            ], temperature=0.0)
            depts = [d for d in (data.get("depts") or []) if d in valid]
            if depts:
                return self._result(depts, "llm", float(data.get("confidence", 0.7)), [str(data.get("reason", ""))], dept_names)
        except Exception as exc:
            logger.warning("企业部门路由 LLM 失败(%s)，回退全企业检索", exc)
        return self._result([], "all", 0.3, ["未匹配特定部门，进入权限约束下的全企业检索"], dept_names)

    @staticmethod
    def _keyword_match(query: str, valid: set[str]) -> tuple[list[str], list[str]]:
        matched, reasons = [], []
        for dept, kws in DEPT_KEYWORDS.items():
            if dept not in valid:
                continue
            hits = [k for k in kws if k.lower() in query.lower()]
            if hits:
                matched.append(dept)
                reasons.append(f"命中关键词「{hits[0]}」")
        return matched, reasons

    @staticmethod
    def _result(dept_ids: list[str], matched_by: str, confidence: float, reasons: list[str], dept_names: dict[str, str]) -> dict[str, Any]:
        return {
            "dept_ids": dept_ids,
            "dept_names": [dept_names.get(d, d) for d in dept_ids],
            "matched_by": matched_by,
            "confidence": round(float(confidence), 2),
            "reasons": reasons,
        }
