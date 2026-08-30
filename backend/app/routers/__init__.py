"""HTTP routers.

Routers translate between HTTP and the service layer. Business rules live in
:mod:`app.services` so that a future in-process MCP server can call them
without going through HTTP.
"""

from fastapi import APIRouter

from app.routers import activity, auth, health, tokens

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(tokens.router)
api_router.include_router(activity.router)

__all__ = ["api_router"]
