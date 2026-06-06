"""Review queue routes for low-confidence extraction fields."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.schemas.review import ReviewDecisionCreate, ReviewDecisionResponse, ReviewTaskRead
from app.services.review_service import ReviewService

router = APIRouter(dependencies=[Depends(require_api_key)])
DB_DEP = Depends(db_dependency)
TENANT_DEP = Depends(get_optional_tenant)


@router.get("/pending", response_model=list[ReviewTaskRead])
def pending_reviews(db=DB_DEP, tenant_id: str | None = TENANT_DEP) -> list[ReviewTaskRead]:
    tasks = ReviewService(db).list_pending(tenant_id=tenant_id)
    return [ReviewTaskRead.model_validate(task) for task in tasks]


@router.post(
    "/{task_id}/decision",
    response_model=ReviewDecisionResponse,
    status_code=status.HTTP_200_OK,
)
def submit_review_decision(
    task_id: str,
    payload: ReviewDecisionCreate,
    db=DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> ReviewDecisionResponse:
    task = ReviewService(db).submit_decision(task_id, payload, tenant_id=tenant_id)
    corrected = payload.corrected_value
    original = task.original_value
    return ReviewDecisionResponse(
        task_id=task.id,
        status=task.status,
        corrected_value=corrected,
        value_changed=str(corrected.get("value")) != str(original.get("value")),
    )
