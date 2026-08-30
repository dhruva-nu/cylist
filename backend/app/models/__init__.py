"""ORM models.

Importing this package registers every table on :data:`Base.metadata`, which is
what Alembic autogenerate and the test fixtures rely on. Add new model modules
to the imports below.
"""

from app.models.activity import Activity, Channel
from app.models.api_token import ApiToken, TokenKind
from app.models.base import Base
from app.models.file import Blob, FileItem, Folder, ItemKind, ItemSource
from app.models.person import Person, PersonKind
from app.models.project import Project, ProjectMember

__all__ = [
    "Activity",
    "ApiToken",
    "Base",
    "Blob",
    "Channel",
    "FileItem",
    "Folder",
    "ItemKind",
    "ItemSource",
    "Person",
    "PersonKind",
    "Project",
    "ProjectMember",
    "TokenKind",
]
