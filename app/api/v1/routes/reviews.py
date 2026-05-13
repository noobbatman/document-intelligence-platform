"""Review queue endpoints — list pending tasks, submit decisions, tenant-scoped."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.schemas.review import ReviewDecisionCreate, ReviewDecisionResponse, ReviewTaskRead
from app.services.review_service import ReviewService

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/pending", response_model=list[ReviewTaskRead])
@router.get("/queue", response_model=list[ReviewTaskRead])
def list_review_queue(
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> list[ReviewTaskRead]:
    """List pending review tasks — includes page_number, bbox, and validation_reason."""
    return [
        ReviewTaskRead.model_validate(t)
        for t in ReviewService(db).list_pending(tenant_id=tenant_id)
    ]


@router.get("/{task_id}", response_model=ReviewTaskRead)
def get_review_task(
    task_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> ReviewTaskRead:
    return ReviewTaskRead.model_validate(ReviewService(db).get_task(task_id, tenant_id=tenant_id))


@router.post("/{task_id}/decision", response_model=ReviewDecisionResponse)
def submit_review_decision(
    task_id: str,
    payload: ReviewDecisionCreate,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> ReviewDecisionResponse:
    svc = ReviewService(db)
    task = svc.submit_decision(task_id, payload, tenant_id=tenant_id)
    orig = task.original_value.get("value")
    corr = payload.corrected_value.get("value")
    return ReviewDecisionResponse(
        task_id=task.id,
        status=task.status,
        corrected_value=payload.corrected_value,
        value_changed=str(orig) != str(corr),
    )
