"""批量导入企业制度目录，并按路径映射到职能部门。

兼容历史脚本名；建议目录按“人力资源/财务/法务/研发/行政”组织。
用法：python -m scripts.ingest_department_files --base ../policy_files
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.config import get_settings
from app.deps import build_container

DEPT_MAP = {
    "人力资源": "dept_hr", "人事": "dept_hr", "HR": "dept_hr",
    "财务": "dept_finance",
    "法务": "dept_legal", "合规": "dept_legal",
    "研发": "dept_rd", "技术": "dept_rd",
    "行政": "dept_admin", "采购": "dept_admin",
}
SUFFIXES = {".pdf", ".docx", ".doc", ".md", ".markdown", ".txt", ".html", ".htm"}
BASE_CANDIDATES = ["../policy_files", "/app/policy_files", "policy_files"]


def resolve_base(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p.resolve()
        print(f"警告: 指定目录不存在，尝试候选路径: {explicit}")
    for cand in BASE_CANDIDATES:
        p = Path(cand)
        if p.exists():
            return p.resolve()
    return Path(explicit or BASE_CANDIDATES[0]).resolve()


def resolve_dept(path: Path) -> str:
    for part in path.parts:
        for key, dept_id in DEPT_MAP.items():
            if key.lower() in part.lower():
                return dept_id
    return "dept_all"


async def main(base: str) -> None:
    base_path = resolve_base(base or None)
    if not base_path.exists():
        print(f"目录不存在: {base_path}（可显式指定 --base /app/policy_files）")
        return
    settings = get_settings()
    c = build_container(settings)
    if c.mongo is not None:
        await c.mongo.connect()
    if hasattr(c.session_store, "connect"):
        try:
            await c.session_store.connect()
        except Exception:
            pass
    files = [p for p in base_path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES]
    print(f"发现 {len(files)} 个企业制度文档待导入")
    ok = fail = 0
    for fp in files:
        dept_id = resolve_dept(fp)
        try:
            doc = await c.indexer.ingest(fp, dept_id=dept_id, uploaded_by="seed")
            await c.policy_claim_extractor.rebuild_for_document(doc["_id"])
            print(f"[ok] {fp.name} -> {dept_id} ({doc['chunk_count']} chunks, claims ready)")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {fp.name}: {exc}")
            fail += 1
    print(f"完成: 成功 {ok}，失败 {fail}")
    if c.mongo is not None:
        await c.mongo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="", help="企业制度根目录（默认自动探测 policy_files）")
    args = parser.parse_args()
    asyncio.run(main(args.base))
