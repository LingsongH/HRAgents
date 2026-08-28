"""Enterprise HR APIs: trusted policy claims/conflicts and bounded interview agent."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_admin, require_user, scope_dept
from app.api.schemas import ApiResponse
from app.hr.trusted_rag import AccessContext

router = APIRouter(prefix="/hr", tags=["hr"])


class ConflictAnalyzeRequest(BaseModel):
    left_doc_id: str
    right_doc_id: str
    company_id: str = "default"
    as_of: str = ""
    require_hitl: bool = True


class ConflictReviewRequest(BaseModel):
    decision: str = Field(..., description="confirm | reject | revise")
    note: str = ""
    reason_code: str = ""


class PolicyMetadataUpdate(BaseModel):
    company_id: str = "default"
    policy_level: str = Field("company", description="law | group | company | department | team")
    parent_policy_id: str = ""
    visibility: str = Field("company", description="company | department")
    allowed_dept_ids: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=lambda: ["employee", "hr", "hr_admin", "admin"])
    policy_state: str = Field("effective", description="draft | review | effective | deprecated | deleted")
    effective_date: str | None = None
    expiry_date: str | None = None


class InterviewRequest(BaseModel):
    job_id: str
    candidate_id: str
    history: list[dict[str, Any]] = Field(default_factory=list)


@router.put("/policies/{doc_id}/metadata", response_model=ApiResponse)
async def update_policy_metadata(
    doc_id: str,
    body: PolicyMetadataUpdate,
    request: Request,
    user: dict = Depends(require_admin),
):
    c = request.app.state.container
    doc = await c.store.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="制度不存在")
    admin_dept = scope_dept(user)
    if admin_dept and doc.get("dept_id") != admin_dept:
        raise HTTPException(status_code=403, detail="无权修改其它部门制度")
    if body.policy_level not in {"law", "group", "company", "department", "team"}:
        raise HTTPException(status_code=400, detail="非法 policy_level")
    if body.policy_state not in {"draft", "review", "effective", "deprecated", "deleted"}:
        raise HTTPException(status_code=400, detail="非法 policy_state")
    update = body.model_dump()
    # Keep the legacy runtime status synchronized so old indexes cannot surface
    # draft/deprecated policies before TrustedPolicyRAG gets a chance to filter.
    update["status"] = "active" if body.policy_state == "effective" else "archived"
    await c.store.update_document(doc_id, update)
    return ApiResponse(data=await c.store.get_document(doc_id))


@router.post("/policies/{doc_id}/claims/rebuild", response_model=ApiResponse)
async def rebuild_policy_claims(doc_id: str, request: Request, user: dict = Depends(require_admin)):
    c = request.app.state.container
    doc = await c.store.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="制度不存在")
    admin_dept = scope_dept(user)
    if admin_dept and doc.get("dept_id") != admin_dept:
        raise HTTPException(status_code=403, detail="无权处理其它部门制度")
    claims = await c.policy_claim_extractor.rebuild_for_document(doc_id)
    return ApiResponse(data={"doc_id": doc_id, "claim_count": len(claims), "claims": claims})


@router.post("/conflicts/analyze", response_model=ApiResponse)
async def analyze_policy_conflict(
    body: ConflictAnalyzeRequest,
    request: Request,
    user: dict = Depends(require_user),
):
    c = request.app.state.container
    access = AccessContext.from_user(user, company_id=body.company_id, as_of=body.as_of)
    try:
        case = await c.policy_conflict_workflow.analyze(
            body.left_doc_id,
            body.right_doc_id,
            access,
            require_hitl=body.require_hitl,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"制度证据未通过 ACL/生效校验: {exc}") from exc
    return ApiResponse(data=case)


@router.get("/conflicts/{case_id}", response_model=ApiResponse)
async def get_policy_conflict(case_id: str, request: Request, user: dict = Depends(require_user)):
    c = request.app.state.container
    case = await c.store.get("policy_conflict_cases", case_id)
    if not case:
        raise HTTPException(status_code=404, detail="冲突分析记录不存在")
    access = AccessContext.from_user(user, company_id=str(user.get("company_id") or "default"))
    for doc_id in (case.get("left_doc_id"), case.get("right_doc_id")):
        doc = await c.store.get_document(str(doc_id or ""))
        if not doc or not c.trusted_policy_rag.validate_document(doc, access).allowed:
            raise HTTPException(status_code=403, detail="无权读取该冲突分析记录")
    return ApiResponse(data=case)


@router.post("/conflicts/{case_id}/review", response_model=ApiResponse)
async def review_policy_conflict(
    case_id: str,
    body: ConflictReviewRequest,
    request: Request,
    user: dict = Depends(require_admin),
):
    try:
        case = await request.app.state.container.policy_conflict_workflow.review(
            case_id,
            user["id"],
            body.decision,
            body.note,
            body.reason_code,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="冲突分析记录不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=case)


@router.post("/interview/generate", response_model=ApiResponse)
async def generate_interview_plan(body: InterviewRequest, request: Request, user: dict = Depends(require_admin)):
    result = await request.app.state.container.interview_agent.generate(
        body.job_id,
        body.candidate_id,
        body.history,
    )
    return ApiResponse(data=result)
