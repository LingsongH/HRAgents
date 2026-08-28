"""Bounded ReAct runtime for open-ended HR tasks such as interview planning.

High-risk policy decisions do not use this runtime.  It is intentionally limited
by steps, tool budget, duplicate detection, cache and wall-clock timeout.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


Tool = Callable[[dict[str, Any]], Awaitable[Any]]
Planner = Callable[[list[dict[str, Any]], dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class ReActTrace:
    steps: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    duplicate_calls: int = 0
    cache_hits: int = 0
    stopped_reason: str = ""


class BoundedReActRuntime:
    def __init__(self, *, max_steps: int = 6, tool_budget: int = 4, timeout_seconds: float = 20.0) -> None:
        self.max_steps = max(1, max_steps)
        self.tool_budget = max(0, tool_budget)
        self.timeout_seconds = max(1.0, timeout_seconds)

    async def run(
        self,
        planner: Planner,
        tools: dict[str, Tool],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.wait_for(self._run(planner, tools, context), timeout=self.timeout_seconds)

    async def _run(self, planner: Planner, tools: dict[str, Tool], context: dict[str, Any]) -> dict[str, Any]:
        history: list[dict[str, Any]] = []
        trace = ReActTrace()
        cache: dict[str, Any] = {}
        seen: set[str] = set()
        started = time.perf_counter()
        final: Any = None

        for step_no in range(1, self.max_steps + 1):
            decision = await planner(history, context)
            action = str(decision.get("action") or "finish")
            if action == "finish":
                final = decision.get("answer")
                trace.stopped_reason = "planner_finish"
                break
            if action != "tool":
                trace.stopped_reason = "invalid_action"
                break
            if trace.tool_calls >= self.tool_budget:
                trace.stopped_reason = "tool_budget_exhausted"
                break

            tool_name = str(decision.get("tool") or "")
            args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
            if tool_name not in tools:
                history.append({"step": step_no, "error": f"tool_not_allowed:{tool_name}"})
                trace.steps.append(history[-1])
                continue
            signature = tool_name + ":" + json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
            if signature in seen:
                trace.duplicate_calls += 1
                if signature in cache:
                    trace.cache_hits += 1
                observation = cache.get(signature, {"error": "duplicate_tool_call"})
                history.append({"step": step_no, "tool": tool_name, "args": args, "observation": observation, "duplicate": True})
                trace.steps.append(history[-1])
                if trace.duplicate_calls >= 2:
                    trace.stopped_reason = "duplicate_call_guard"
                    break
                continue

            seen.add(signature)
            if signature in cache:
                observation = cache[signature]
                trace.cache_hits += 1
            else:
                observation = await tools[tool_name](args)
                cache[signature] = observation
                trace.tool_calls += 1
            history.append({"step": step_no, "tool": tool_name, "args": args, "observation": observation})
            trace.steps.append(history[-1])
        else:
            trace.stopped_reason = "max_steps"

        return {
            "answer": final,
            "history": history,
            "trace": {
                "steps": trace.steps,
                "tool_calls": trace.tool_calls,
                "duplicate_calls": trace.duplicate_calls,
                "cache_hits": trace.cache_hits,
                "stopped_reason": trace.stopped_reason or "completed",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "limits": {
                    "max_steps": self.max_steps,
                    "tool_budget": self.tool_budget,
                    "timeout_seconds": self.timeout_seconds,
                },
            },
        }
