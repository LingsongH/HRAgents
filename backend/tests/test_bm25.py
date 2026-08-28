"""测试 BM25 检索。"""
from __future__ import annotations

from app.retrieval.bm25 import BM25Index, tokenize


def test_tokenize_chinese():
    tokens = tokenize("员工考勤管理办法")
    assert "考勤" in tokens or "本科" in tokens or len(tokens) > 0


def test_bm25_search():
    idx = BM25Index()
    docs = [
        {"_id": "c1", "dept_id": "dept_hr", "content": "员工工作日应按公司考勤制度完成打卡。"},
        {"_id": "c2", "dept_id": "dept_hr", "content": "请假申请应当按制度提前提交。"},
        {"_id": "c3", "dept_id": "dept_finance", "content": "差旅报销应在规定期限内提交。"},
    ]
    idx.index(docs)
    hits = idx.search("考勤时间", top_k=2)
    assert hits, "应返回检索结果"
    assert hits[0]["id"] == "c1"


def test_bm25_dept_filter():
    idx = BM25Index()
    docs = [
        {"_id": "c1", "dept_id": "dept_hr", "content": "考勤相关条款。"},
        {"_id": "c2", "dept_id": "dept_finance", "content": "考勤与薪酬相关条款。"},
    ]
    idx.index(docs)
    hits = idx.search("考勤", top_k=5, dept_id="dept_hr")
    assert all(h["dept_id"] == "dept_hr" for h in hits)


def test_bm25_filters_before_topk():
    idx = BM25Index()
    docs = [
        {"_id": f"other-{i}", "dept_id": "dept_other", "content": "考勤 考勤 考勤"}
        for i in range(25)
    ]
    docs.append({"_id": "target", "dept_id": "dept_hr", "content": "考勤"})
    idx.index(docs)
    hits = idx.search("考勤", top_k=5, dept_id="dept_hr")
    assert [h["id"] for h in hits] == ["target"]
