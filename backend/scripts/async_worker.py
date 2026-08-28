"""Redis Stream worker：处理异步文档入库、Loop 唤醒等作业。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import get_settings
from app.deps import build_container
from app.utils.logging import setup_logging


async def process(container, job):
    payload = job.get("payload") or {}
    if job["type"] == "ingest_document":
        path = Path(payload["path"])
        try:
            doc = await container.indexer.ingest(
                path, payload["dept_id"], payload["uploaded_by"]
            )
            # 恢复原始文件名，并补齐企业制度 ACL/层级元数据。
            doc.setdefault("source", {})["file_name"] = payload.get("original_name", path.name)
            policy_meta = {
                "source": doc["source"],
                "company_id": payload.get("company_id", "default"),
                "policy_level": payload.get("policy_level", doc.get("policy_level", "company")),
                "parent_policy_id": payload.get("parent_policy_id") or doc.get("parent_policy_id"),
                "visibility": payload.get("visibility", "company"),
                "allowed_roles": payload.get("allowed_roles") or doc.get("allowed_roles") or ["employee", "hr", "hr_admin", "admin"],
                "policy_state": "effective",
            }
            await container.store.update_document(doc["_id"], policy_meta)
            doc = await container.store.get_document(doc["_id"]) or doc
            claims = await container.policy_claim_extractor.rebuild_for_document(doc["_id"])
            relations = await container.conflict_detector.run_for_document(doc)
            review = await container.review_engine.create_review_order(doc)
            return {
                "document_id": doc["_id"], "relations": len(relations),
                "policy_claims": len(claims), "review_id": (review or {}).get("_id")
            }
        finally:
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
    if job["type"] in {"feedback_received", "run_loop"}:
        async def progress(stage, detail):
            await container.job_queue.update_progress(job["_id"], {
                "stage": stage, "detail": detail,
            })

        result = await container.loop_engine.run_cycle(progress_callback=progress)
        result["memory_retention"] = await container.memory_retention.prune_expired()
        return result
    raise ValueError(f"未知作业类型: {job['type']}")


async def main():
    settings = get_settings()
    setup_logging(settings.log_level)
    container = build_container(settings)
    if container.mongo is not None:
        await container.mongo.connect()
    if hasattr(container.session_store, "connect"):
        await container.session_store.connect()
    while True:
        for job in await container.job_queue.next_jobs():
            try:
                result = await process(container, job)
                await container.job_queue.finish(job, "completed", result)
            except Exception as exc:
                await container.job_queue.finish(job, "failed", {"error": str(exc)})
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
