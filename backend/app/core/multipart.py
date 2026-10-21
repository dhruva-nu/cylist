"""Reading a ``multipart/form-data`` body as it arrives, a part at a time.

Why not FastAPI's ``File()`` and ``Form()`` (CYLIST-123): FastAPI parses the
form *before* it resolves a route's dependencies, and Starlette's parser
spools every file part past its first megabyte to a temporary file. An upload
route's authentication and permission checks are dependencies, so a POST with
no credentials at all had its whole body written to the container's ``/tmp``
before it was told 401 — anyone could fill the disk without signing in.

So an upload route takes the :class:`~starlette.requests.Request` instead,
lets its dependencies run, and only then hands the body to :class:`Form`.
Nothing is read off the socket until a caller asks for the next part, and a
file part's bytes go wherever its consumer sends them — the blob store,
chunk by chunk — rather than to a spool first. The body as a whole is capped
before any of this by :class:`app.core.body_limit.BodyLimitMiddleware`.

Refusals are :class:`~fastapi.exceptions.RequestValidationError` where the
old ``File()``/``Form()`` parameters would have produced one — a missing
part, a field that is not what it should be — so a client sees the same
``validation_failed`` envelope it always did.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from fastapi.exceptions import RequestValidationError
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import Request

if TYPE_CHECKING:
    from python_multipart.multipart import MultipartCallbacks

MAX_PARTS: Final = 1000
"""How many parts one body may hold. A skill's folder is the largest honest
form — at most 200 files, each perhaps with an ``executable`` field beside
it — and a cap well above that keeps a body of a million empty parts from
being walked one at a time."""

FIELD_MAX_BYTES: Final = 64 * 1024
"""How long one text field may be. Every text field an upload takes is a
UUID, an enum, a path or a line of description, so this is a bound on
memory rather than a rule about content — each field's own rule is checked
by whoever reads it."""


def invalid_field(name: str, message: str, *, kind: str = "value_error") -> RequestValidationError:
    """The refusal a ``Form()`` parameter would have produced for this field.

    Raised by callers too, for a field that arrived but does not parse — the
    same ``validation_failed`` envelope, located at ``body.<name>``.
    """
    return RequestValidationError([{"type": kind, "loc": ("body", name), "msg": message}])


def single(fields: Mapping[str, list[str]], name: str) -> str | None:
    """A single-valued field from :meth:`Form.read`: the last one sent, as a
    ``Form()`` parameter took it. Empty is absent, which is how a form says
    it has nothing to say."""
    values = fields.get(name)
    return values[-1] if values and values[-1] else None


def multipart_body(properties: dict[str, Any], *, required: list[str]) -> dict[str, Any]:
    """The ``requestBody`` a route reading :class:`Form` itself declares.

    FastAPI documents a body from the parameters it parses, and a route that
    parses its own has none — so without this the OpenAPI document, and every
    client generated from it, would say the upload takes no body at all.
    Passed as the route's ``openapi_extra``.
    """
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {"type": "object", "properties": properties, "required": required}
                }
            },
        }
    }


def _malformed(reason: str) -> RequestValidationError:
    return RequestValidationError([{"type": "multipart_invalid", "loc": ("body",), "msg": reason}])


def _decode(value: bytes) -> str:
    """A header parameter as text: UTF-8, which is what browsers send, else
    Latin-1, which every byte string is."""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1")


@dataclass(slots=True)
class _Event:
    kind: str
    """``headers``, ``data`` or ``end``."""
    data: bytes = b""
    headers: list[tuple[bytes, bytes]] = field(default_factory=list)


class Part:
    """One part of the body: its field name, its filename if it is a file,
    and its content, which can be read once.

    The content is pulled off the request as it is iterated, so it must be
    consumed — or :meth:`skip`-ped — before the next part is asked for.
    :meth:`Form.parts` skips whatever a caller left unread.
    """

    def __init__(
        self,
        form: Form,
        *,
        name: str,
        filename: str | None,
        content_type: str | None,
    ) -> None:
        self._form = form
        self.name = name
        self.filename = filename
        """As the client sent it — which may be a path, or empty. Cleaning
        it into a name is the consumer's business."""
        self.content_type = content_type
        """The part's own ``Content-Type``, unchecked. Only ever a label."""
        self.finished = False

    @property
    def is_file(self) -> bool:
        """Whether the part came with a filename, which is what a form's file
        input sends and a text field does not."""
        return self.filename is not None

    async def chunks(self) -> AsyncIterator[bytes]:
        """The content, as it comes off the socket."""
        while not self.finished:
            event = await self._form.next_event()
            if event is None:
                raise _malformed("The body ended part-way through a part.")
            if event.kind == "end":
                self.finished = True
            elif event.kind == "data":
                if event.data:
                    yield event.data
            else:  # pragma: no cover - the parser ends a part before beginning another
                raise _malformed("A part began before the last one ended.")

    async def text(self, max_bytes: int = FIELD_MAX_BYTES) -> str:
        """The content as a text field, refused past ``max_bytes``."""
        body = bytearray()
        async for chunk in self.chunks():
            body += chunk
            if len(body) > max_bytes:
                raise invalid_field(self.name, f"must be at most {max_bytes} bytes")
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise invalid_field(self.name, "must be UTF-8 text") from exc

    async def skip(self) -> None:
        """Read past whatever of the content is left, keeping none of it."""
        async for _ in self.chunks():
            pass


class Form:
    """A ``multipart/form-data`` request body, parsed as it is read.

    Args:
        request: The request whose body this is. Its body must not have been
            read already — which is the point: nothing else reads it.
        max_parts: See :data:`MAX_PARTS`.
    """

    def __init__(self, request: Request, *, max_parts: int = MAX_PARTS) -> None:
        self._stream = request.stream()
        self._max_parts = max_parts
        self._events: deque[_Event] = deque()
        self._ended = False
        self._exhausted = False
        self._current: Part | None = None
        self._headers: list[tuple[bytes, bytes]] = []
        self._header_name = bytearray()
        self._header_value = bytearray()

        content_type, options = parse_options_header(request.headers.get("content-type"))
        boundary = options.get(b"boundary")
        if content_type != b"multipart/form-data" or not boundary:
            raise _malformed("Send the body as multipart/form-data.")
        callbacks: MultipartCallbacks = {
            "on_part_begin": self._on_part_begin,
            "on_header_field": self._on_header_field,
            "on_header_value": self._on_header_value,
            "on_header_end": self._on_header_end,
            "on_headers_finished": self._on_headers_finished,
            "on_part_data": self._on_part_data,
            "on_part_end": self._on_part_end,
            "on_end": self._on_end,
        }
        try:
            self._parser = MultipartParser(boundary, callbacks)
        except FormParserError as exc:
            raise _malformed("The multipart boundary is not usable.") from exc

    async def parts(self) -> AsyncIterator[Part]:
        """Every part in the body, in the order the client sent them."""
        count = 0
        while True:
            if self._current is not None and not self._current.finished:
                await self._current.skip()
            event = await self.next_event()
            if event is None:
                return
            if event.kind != "headers":  # pragma: no cover - parts are skipped to their end
                raise _malformed("Content arrived outside a part.")
            count += 1
            if count > self._max_parts:
                raise _malformed(f"A form may have at most {self._max_parts} parts.")
            self._current = self._part(event.headers)
            yield self._current

    async def read(
        self,
        *,
        file_field: str,
        on_file: Callable[[Part, Mapping[str, list[str]]], Awaitable[Any]],
        text_fields: Collection[str],
    ) -> dict[str, list[str]]:
        """Walk the form, handing each file under ``file_field`` to ``on_file``
        and collecting the named text fields.

        ``on_file`` is given the text fields that came *before* the file, so
        it can check them before a byte of the file is stored — a client that
        sends its fields first gets that; one that sends them after gets them
        checked once the file is in. Anything else in the form is read past
        and ignored, as ``Form()`` parameters ignored it.

        Returns:
            Each named text field's values, in order — one for most fields,
            one per repetition for a list.
        """
        fields: dict[str, list[str]] = {}
        async for part in self.parts():
            if part.name == file_field and part.is_file:
                await on_file(part, fields)
            elif part.name in text_fields and not part.is_file:
                fields.setdefault(part.name, []).append(await part.text())
        return fields

    async def next_event(self) -> _Event | None:
        """The parser's next event, reading more of the body when it has none.

        None once the body is over. A body that stops before the closing
        boundary is malformed rather than merely short: whatever part it was
        in the middle of is incomplete.
        """
        while not self._events:
            if self._exhausted:
                return None
            try:
                chunk = await anext(self._stream)
            except StopAsyncIteration:
                self._exhausted = True
                if not self._ended:
                    raise _malformed("The body ended before the form did.") from None
                return None
            try:
                self._parser.write(chunk)
            except FormParserError as exc:
                raise _malformed("The body is not well-formed multipart.") from exc
        return self._events.popleft()

    def _part(self, headers: list[tuple[bytes, bytes]]) -> Part:
        disposition: bytes = b""
        content_type: str | None = None
        for name, value in headers:
            if name == b"content-disposition":
                disposition = value
            elif name == b"content-type":
                content_type = value.decode("latin-1")
        _, options = parse_options_header(disposition)
        if b"name" not in options:
            raise _malformed("Every part needs a Content-Disposition with a name.")
        filename = options.get(b"filename")
        return Part(
            self,
            name=_decode(options[b"name"]),
            filename=_decode(filename) if filename is not None else None,
            content_type=content_type,
        )

    # --- Parser callbacks, run synchronously inside ``parser.write`` --------

    def _on_part_begin(self) -> None:
        self._headers = []

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _on_header_end(self) -> None:
        self._headers.append((bytes(self._header_name).lower(), bytes(self._header_value)))
        self._header_name.clear()
        self._header_value.clear()

    def _on_headers_finished(self) -> None:
        self._events.append(_Event("headers", headers=self._headers))

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        self._events.append(_Event("data", data=data[start:end]))

    def _on_part_end(self) -> None:
        self._events.append(_Event("end"))

    def _on_end(self) -> None:
        self._ended = True
