"""ORM models.

Importing this package registers every table on :data:`Base.metadata`, which is
what Alembic autogenerate and the test fixtures rely on. Add new model modules
to the imports below.
"""

from app.models.activity import Activity, Channel
from app.models.agent import Skill
from app.models.agent_session import (
    AgentFeedEntry,
    AgentFeedKind,
    AgentPrompt,
    AgentPromptKind,
    AgentPromptStatus,
    AgentSession,
    AgentSessionReason,
    AgentSessionState,
    SessionKey,
)
from app.models.api_token import ApiToken, TokenKind
from app.models.base import Base
from app.models.board import BoardColumn
from app.models.device import DevicePlatform, PushDevice
from app.models.doc import Doc, DocSection, DocTopic
from app.models.file import Blob, FileItem, Folder, ItemKind, ItemSource
from app.models.goal import Goal, GoalStatus
from app.models.hook import DeliveryState, Hook, HookDelivery
from app.models.permission import (
    EVERYONE_ELSE,
    ProjectPermission,
    RoleClearance,
    RoleColumnRule,
)
from app.models.person import Person, PersonKind
from app.models.project import Project, ProjectMember
from app.models.role import ProjectRole
from app.models.task import (
    ChecklistState,
    CommentKind,
    Task,
    TaskChecklistItem,
    TaskColumnDueDate,
    TaskComment,
    TaskPriority,
    TaskStatus,
    TaskType,
    TaskWaitingOn,
)
from app.models.template import TaskTemplate, TemplateStage
from app.models.today import DayPick, Todo, TodoState
from app.models.vault import VaultNode, VaultNodeKind, VaultSecret, VaultTree

__all__ = [
    "EVERYONE_ELSE",
    "Activity",
    "AgentFeedEntry",
    "AgentFeedKind",
    "AgentPrompt",
    "AgentPromptKind",
    "AgentPromptStatus",
    "AgentSession",
    "AgentSessionReason",
    "AgentSessionState",
    "ApiToken",
    "Base",
    "Blob",
    "BoardColumn",
    "Channel",
    "ChecklistState",
    "CommentKind",
    "DayPick",
    "DeliveryState",
    "DevicePlatform",
    "Doc",
    "DocSection",
    "DocTopic",
    "FileItem",
    "Folder",
    "Goal",
    "GoalStatus",
    "Hook",
    "HookDelivery",
    "ItemKind",
    "ItemSource",
    "Person",
    "PersonKind",
    "Project",
    "ProjectMember",
    "ProjectPermission",
    "ProjectRole",
    "PushDevice",
    "RoleClearance",
    "RoleColumnRule",
    "SessionKey",
    "Skill",
    "Task",
    "TaskChecklistItem",
    "TaskColumnDueDate",
    "TaskComment",
    "TaskPriority",
    "TaskStatus",
    "TaskTemplate",
    "TaskType",
    "TaskWaitingOn",
    "TemplateStage",
    "Todo",
    "TodoState",
    "TokenKind",
    "VaultNode",
    "VaultNodeKind",
    "VaultSecret",
    "VaultTree",
]
