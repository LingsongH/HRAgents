"""Open-ended interview planning on top of BoundedReActRuntime."""
from __future__ import annotations

from typing import Any

from app.hr.bounded_react import BoundedReActRuntime
from app.llm.client import ChatMessage, LLMClient
from app.storage.store import DataStore


class InterviewAgent:
    def __init__(self, store: DataStore, llm: LLMClient, runtime: BoundedReActRuntime) -> None:
        self.store = store
        self.llm = llm
        self.runtime = runtime

    async def generate(self, job_id: str, candidate_id: str, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        async def get_job(args: dict[str, Any]) -> Any:
            return await self.store.get("job_profiles", str(args.get("job_id") or job_id)) or {}

        async def get_candidate(args: dict[str, Any]) -> Any:
            return await self.store.get("candidate_profiles", str(args.get("candidate_id") or candidate_id)) or {}

        async def get_question_bank(args: dict[str, Any]) -> Any:
            role = str(args.get("role") or "")
            rows = await self.store.find("interview_question_bank")
            return [r for r in rows if not role or r.get("role") in {"", role}][:20]

        async def planner(step_history: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
            if not step_history:
                return {"action": "tool", "tool": "get_job_profile", "args": {"job_id": job_id}}
            called = {row.get("tool") for row in step_history}
            if "get_candidate_profile" not in called:
                return {"action": "tool", "tool": "get_candidate_profile", "args": {"candidate_id": candidate_id}}
            if "get_question_bank" not in called:
                job = next((row.get("observation") for row in step_history if row.get("tool") == "get_job_profile"), {}) or {}
                return {"action": "tool", "tool": "get_question_bank", "args": {"role": job.get("role", "")}}

            prompt = """基于岗位画像、候选人信息、题库和历史面试，生成结构化面试计划。仅输出 JSON：
{"summary":"","gaps":[],"questions":[{"question":"","purpose":"","follow_up":"","rubric":[]}]}。
不要询问与岗位无关的敏感个人信息。\n上下文：{context}\n工具结果：{history}"""
            try:
                data = await self.llm.complete_json([
                    ChatMessage.system("你是企业招聘面试 Agent。"),
                    ChatMessage.user(prompt.format(context=context, history=step_history)),
                ], temperature=0.2, max_tokens=1600)
            except Exception:
                data = {"summary": "degraded", "gaps": [], "questions": []}
            return {"action": "finish", "answer": data}

        return await self.runtime.run(
            planner,
            {
                "get_job_profile": get_job,
                "get_candidate_profile": get_candidate,
                "get_question_bank": get_question_bank,
            },
            {"job_id": job_id, "candidate_id": candidate_id, "interview_history": history or []},
        )
