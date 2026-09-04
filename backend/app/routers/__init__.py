"""HTTP routers.

Routers translate between HTTP and the service layer. Business rules live in
:mod:`app.services` so that a future in-process MCP server can call them
without going through HTTP.
"""

from fastapi import APIRouter

from app.routers import (
    activity,
    auth,
    columns,
    files,
    health,
    people,
    projects,
    reports,
    tasks,
    templates,
    tokens,
    vault,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(tokens.router)
api_router.include_router(projects.router)
# Registered after `projects` so `/projects/{project_ref}/vault/trees` resolves
# against the same `{project_ref}` the rest of that prefix uses.
api_router.include_router(vault.project_router)
api_router.include_router(reports.router)
api_router.include_router(vault.router)
api_router.include_router(people.router)
api_router.include_router(columns.router)
# After `columns`, whose columns a template's stages name.
api_router.include_router(templates.router)
api_router.include_router(tasks.router)
api_router.include_router(files.router)
api_router.include_router(activity.router)

__all__ = ["api_router"]
