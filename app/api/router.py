from fastapi import APIRouter

from app.api.v1.routes import (
    analytics, deduplication, documents, drafts,
    exports, health, po_matching, reviews, webhooks,
)

api_router = APIRouter()
api_router.include_router(health.router,         tags=["health"])
api_router.include_router(documents.router,      prefix="/documents",      tags=["documents"])
api_router.include_router(drafts.router,                                  tags=["drafts"])
api_router.include_router(reviews.router,        prefix="/reviews",        tags=["reviews"])
api_router.include_router(webhooks.router,       prefix="/webhooks",       tags=["webhooks"])
api_router.include_router(analytics.router,      prefix="/analytics",      tags=["analytics"])
api_router.include_router(exports.router,        prefix="/exports",        tags=["exports"])
api_router.include_router(po_matching.router,    prefix="/purchase-orders", tags=["purchase-orders"])
api_router.include_router(deduplication.router,  prefix="/deduplication",  tags=["deduplication"])
