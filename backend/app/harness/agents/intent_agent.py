"""Enterprise intent Agent: route HR knowledge vs high-risk workflow vs interview task."""
from __future__ import annotations

from typing import Any

from app.harness.base import Intent
from app.integrations.pi_runtime import PiAgentRuntimeClient
from app.llm.client import ChatMessage, LLMClient
from app.storage.store import DataStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

INTENT_PROMPT = """你是企业 HR 智能体意图识别助手，仅输出 JSON：
{{
  "type":"employee_qa|policy_conflict|interview|hr_process|complaint|chitchat|other",
  "depts":["dept_id"],
  "user_role":"employee|hr|hr_admin|admin",
  "entities":{{}},
  "needs_cross_dept":false,
  "high_risk":false,
  "execution_mode":"graph|bounded_react",
  "confidence":0.0
}}
规则：制度冲突/制度审核属于 high_risk=true 且 execution_mode=graph；面试题生成可 bounded_react；员工制度问答使用 graph/RAG。
候选部门：{departments}
用户上下文：{profile}
请求：{query}
"""


class IntentAgent:
    def __init__(self, llm: LLMClient, store: DataStore, pi_runtime: PiAgentRuntimeClient | None = None, timeout: float = 1.0) -> None:
        self.llm, self.store, self.pi_runtime, self.timeout = llm, store, pi_runtime, timeout

    async def infer(self, query: str, user_id: str = "", memory_context: str = "") -> Intent:
        departments = await self.store.list_departments()
        dept_desc = ", ".join(f"{d['_id']}({d.get('name', '')})" for d in departments) or "dept_all(全企业)"
        profile = await self.store.get_user_profile(user_id) or {}
        try:
            prompt = INTENT_PROMPT.format(departments=dept_desc, profile=(memory_context or str(profile))[:2000], query=query)
            data: Any = None
            if self.pi_runtime is not None:
                data = await self.pi_runtime.run_json("intent", "你是企业 HR 意图识别助手。", prompt, timeout_seconds=self.timeout)
            if not isinstance(data, dict):
                data = await self.llm.complete_json([ChatMessage.system("你是企业 HR 意图识别助手。"), ChatMessage.user(prompt)], temperature=0.0)
            intent = Intent.from_dict(data)
        except Exception as exc:
            logger.warning("意图识别失败(%s)，使用企业关键词回退", exc)
            intent = self._keyword_fallback(query)
        valid = {d["_id"] for d in departments}
        if not intent.depts or intent.depts == ["dept_all"]:
            intent.depts = sorted(valid) if valid else ["dept_all"]
        intent.depts = [d for d in intent.depts if d in valid or d == "dept_all"] or ["dept_all"]
        return intent

    @staticmethod
    def _keyword_fallback(query: str) -> Intent:
        q = query.lower()
        depts: list[str] = []
        if any(k in q for k in ["人事", "hr", "招聘", "考勤", "请假", "面试", "入职", "离职"]): depts.append("dept_hr")
        if any(k in q for k in ["报销", "预算", "发票", "付款", "财务"]): depts.append("dept_finance")
        if any(k in q for k in ["法务", "合规", "制度冲突", "合同"]): depts.append("dept_legal")
        if any(k in q for k in ["研发", "弹性工作"]): depts.append("dept_rd")
        if any(k in q for k in ["行政", "采购", "办公", "电脑租赁"]): depts.append("dept_admin")
        if any(k in q for k in ["冲突", "矛盾", "新版制度", "例外", "补充规定"]): intent_type = "policy_conflict"
        elif any(k in q for k in ["面试", "候选人", "面试题", "jd"]): intent_type = "interview"
        elif any(k in q for k in ["怎么办", "流程", "办理", "申请"]): intent_type = "hr_process"
        elif any(k in q for k in ["投诉", "举报"]): intent_type = "complaint"
        elif any(k in q for k in ["你好", "谢谢", "在吗"]): intent_type = "chitchat"
        else: intent_type = "employee_qa"
        raw = {
            "fallback": True,
            "high_risk": intent_type == "policy_conflict",
            "execution_mode": "bounded_react" if intent_type == "interview" else "graph",
        }
        return Intent(type=intent_type, depts=depts or ["dept_all"], needs_cross_dept=len(depts) > 1, confidence=0.65, raw=raw)
