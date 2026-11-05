"""Images in a card's description: uploading one, and serving it back.

An image is uploaded to the project rather than to a card, because a new
card's description is written before the card exists — see
:mod:`app.models.image`. Anyone who may add or edit cards on the project may
upload one; anyone on the project may see one.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.card_deps import WRITE_CARDS
from app.api.deps import resolved_project
from app.auth.dependencies import current_principal, require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core import multipart
from app.core.body_limit import streams_upload
from app.core.errors import NotFoundError
from app.core.multipart import multipart_body
from app.core.security_headers import IMAGE_HEADERS
from app.db import SessionDependency
from app.models.image import DescriptionImage
from app.models.project import Project
from app.schemas.images import ImageRead
from app.services import images, projects
from app.storage import BlobStore, get_blob_store

router = APIRouter(tags=["images"])


def image_read(image: DescriptionImage) -> ImageRead:
    return ImageRead(
        id=image.id, url=f"/api/v1/images/{image.id}", mime=image.mime, size=image.size
    )


async def resolved_image(
    image_id: UUID,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> DescriptionImage:
    """Turn the path segment into an image, 404-ing if nothing matches — or if
    it is on a project the caller is not a member of."""
    image = await images.get(session, image_id)
    await projects.require_reachable(
        session,
        image.project_id,
        principal,
        NotFoundError("No such image.", details={"image_id": str(image_id)}),
    )
    return image


@router.post(
    "/projects/{project_ref}/images",
    response_model=ImageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image for a description",
    responses={
        413: {"description": "The image is larger than 10 MB."},
        422: {"description": "The file is not a PNG, JPEG, GIF or WebP."},
    },
    openapi_extra=multipart_body(
        {
            "file": {
                "type": "string",
                "contentMediaType": "application/octet-stream",
                "description": "The image: PNG, JPEG, GIF or WebP, at most 10 MB.",
            },
        },
        required=["file"],
    ),
)
@streams_upload
async def upload_image(
    request: Request,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(WRITE_CARDS),
    session: AsyncSession = SessionDependency,
    store: BlobStore = Depends(get_blob_store),
) -> ImageRead:
    """Store an image as `multipart/form-data`, for a card's description to show.

    The bytes go to the blob store, not the database; the description refers
    to the image by the `url` returned, as markdown. The type is read off the
    content, not the part's `Content-Type`, and anything that is not a PNG,
    JPEG, GIF or WebP is refused.
    """
    image = await images.upload(
        session, store, project, multipart.Form(request), added_by=principal.person_id
    )
    return image_read(image)


@router.get(
    "/images/{image_id}",
    response_class=FileResponse,
    summary="Get an image",
    responses={
        200: {
            "content": {"image/png": {}, "image/jpeg": {}, "image/gif": {}, "image/webp": {}},
            "description": "The image.",
        }
    },
)
async def get_image(
    image: DescriptionImage = Depends(resolved_image),
    _: Principal = Depends(require(Scope.READ)),
    store: BlobStore = Depends(get_blob_store),
) -> FileResponse:
    """The image's bytes, with no attachment disposition, so a description can draw them."""
    if not await store.exists(image.blob.path):
        raise NotFoundError(
            "This image's content is missing from the store.", details={"image_id": str(image.id)}
        )
    return FileResponse(
        store.locate(image.blob.path),
        media_type=image.mime,
        headers=IMAGE_HEADERS,
    )
