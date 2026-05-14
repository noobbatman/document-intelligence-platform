from fastapi import APIRouter

from app.api.v1.routes import documents, drafts, health

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(drafts.router, tags=["drafts"])
