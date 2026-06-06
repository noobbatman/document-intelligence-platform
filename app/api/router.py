from fastapi import APIRouter

from app.api.v1.routes import analytics, conflicts, documents, drafts, health, reviews

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(drafts.router, tags=["drafts"])
api_router.include_router(conflicts.router, tags=["conflicts"])
api_router.include_router(reviews.router, prefix="/reviews", tags=["reviews"])
