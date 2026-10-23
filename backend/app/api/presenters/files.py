"""How an uploaded file or a link is drawn in a response."""

from __future__ import annotations

from app.core import sensitivity
from app.core.sensitivity import DEFAULT_LEVEL
from app.models.file import FileItem
from app.schemas.files import ItemRead
from app.schemas.people import PersonRead


def file_item_read(item: FileItem) -> ItemRead:
    """One item as a listing shows it. Shared, because a CSV cold storage writes
    is reported in the same shape as anything uploaded."""
    person = item.added_by_person
    return ItemRead(
        id=item.id,
        folder_id=item.folder_id,
        kind=item.kind,
        name=item.name,
        sensitivity=sensitivity.parse(item.sensitivity, DEFAULT_LEVEL),
        url=item.url,
        source=item.source,
        size=item.size,
        mime=item.mime,
        added_by=PersonRead.model_validate(person) if person is not None else None,
        created_at=item.created_at,
    )
