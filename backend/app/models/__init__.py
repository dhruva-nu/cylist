"""ORM models.

Importing this package registers every table on :data:`Base.metadata`, which is
what Alembic autogenerate and the test fixtures rely on. Add new model modules
to the imports below.
"""

from app.models.activity import Activity, Channel
from app.models.api_token import ApiToken, TokenKind
from app.models.base import Base
from app.models.board import BoardColumn
from app.models.file import Blob, FileItem, Folder, ItemKind, ItemSource
from app.models.person import Person, PersonKind
from app.models.project import Project, ProjectMember
from app.models.task import (
    CommentKind,
    Task,
    TaskComment,
    TaskStatus,
    TaskType,
    TaskWaitingOn,
)
from app.models.vault import VaultNode, VaultNodeKind, VaultSecret, VaultTree

__all__ = [
    "Activity",
    "ApiToken",
    "Base",
    "Blob",
    "BoardColumn",
    "Channel",
    "CommentKind",
    "FileItem",
    "Folder",
    "ItemKind",
    "ItemSource",
    "Person",
    "PersonKind",
    "Project",
    "ProjectMember",
    "Task",
    "TaskComment",
    "TaskStatus",
    "TaskType",
    "TaskWaitingOn",
    "TokenKind",
    "VaultNode",
    "VaultNodeKind",
    "VaultSecret",
    "VaultTree",
]
