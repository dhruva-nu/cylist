"""Firebase Cloud Messaging, HTTP v1: the one way a push leaves this server.

Sending needs an OAuth 2 access token for the Firebase project, which a
Google service account mints for itself: a JWT signed with the account's
private key is exchanged at the account's ``token_uri`` for an access token
good for an hour (RFC 7523, "JWT bearer"). That exchange is done here with
:mod:`cryptography` and :mod:`httpx`, both already dependencies, rather than
pulling in ``google-auth`` for two requests.

The key is a server secret: read from the file
``CYLIST_FCM_SERVICE_ACCOUNT_FILE`` names, held in memory, and never logged —
not the key, not the JWT, not the access token. Nor is a device's
registration token logged whole; :func:`redact` shortens it to a tail.

With no key configured there is no :class:`FcmSender` at all, and
:class:`NullSender` takes its place: every push is a debug line and nothing
else, which is how CI, a developer's machine and any deployment without
Firebase run.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.core.clock import epoch

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
"""The one OAuth scope sending needs."""

SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

TOKEN_LIFETIME = 3600
"""Seconds a minted access token is asked to last — Google's maximum."""

REFRESH_MARGIN = 300
"""Mint a new access token this many seconds before the old one expires, so
a push is never sent with one that runs out in flight."""

TIMEOUT = httpx.Timeout(10.0)
"""A push is a nudge, not an RPC: a slow FCM costs one notification, never a
request, because sending happens off the request path."""


class SendOutcome(StrEnum):
    """What became of one message."""

    SENT = "sent"
    UNREGISTERED = "unregistered"
    """FCM says the device token is gone — the app was uninstalled, or the
    token rotated. The device row should be forgotten."""
    FAILED = "failed"
    """Anything else. Logged, and not retried: the next state change sends a
    fresher message anyway."""


class PushSender(Protocol):
    """Sends one FCM v1 ``message`` object."""

    @property
    def enabled(self) -> bool: ...

    async def send(self, message: dict[str, Any]) -> SendOutcome: ...

    async def aclose(self) -> None:
        """Let go of whatever the sender holds open. Called once, at shutdown."""


class ServiceAccountError(ValueError):
    """The configured key is missing, unreadable or not a service account."""


@dataclass(frozen=True)
class ServiceAccount:
    """The parts of a service-account key that sending uses."""

    project_id: str
    client_email: str
    token_uri: str
    private_key: rsa.RSAPrivateKey

    def __repr__(self) -> str:
        # Never the key, even by accident in a traceback's locals.
        return f"ServiceAccount(project_id={self.project_id!r}, client_email={self.client_email!r})"

    @classmethod
    def from_file(cls, path: Path) -> ServiceAccount:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ServiceAccountError(f"cannot read {path}: {error.strerror}") from None
        return cls.from_json(raw)

    @classmethod
    def from_json(cls, raw: str) -> ServiceAccount:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            raise ServiceAccountError("the key file is not JSON") from None
        if not isinstance(info, dict) or info.get("type") != "service_account":
            raise ServiceAccountError("the key file is not a service-account key")
        missing = [
            name
            for name in ("project_id", "client_email", "private_key", "token_uri")
            if not isinstance(info.get(name), str) or not info[name]
        ]
        if missing:
            raise ServiceAccountError(f"the key file lacks {', '.join(missing)}")
        try:
            key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        except (ValueError, TypeError):
            raise ServiceAccountError("the key file's private_key is not a PEM key") from None
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ServiceAccountError("the key file's private_key is not an RSA key")
        return cls(
            project_id=info["project_id"],
            client_email=info["client_email"],
            token_uri=info["token_uri"],
            private_key=key,
        )

    def assertion(self, issued_at: int) -> str:
        """The signed JWT exchanged for an access token."""
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": self.client_email,
            "scope": SCOPE,
            "aud": self.token_uri,
            "iat": issued_at,
            "exp": issued_at + TOKEN_LIFETIME,
        }
        signing_input = f"{_b64(header)}.{_b64(claims)}".encode()
        signature = self.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input.decode()}.{_b64url(signature)}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64(value: dict[str, Any]) -> str:
    return _b64url(json.dumps(value, separators=(",", ":")).encode())


def redact(fcm_token: str) -> str:
    """A device token as a log line may show it: its last six characters."""
    return f"…{fcm_token[-6:]}"


class NullSender:
    """What sends when no key is configured: nothing, said quietly."""

    @property
    def enabled(self) -> bool:
        return False

    async def send(self, message: dict[str, Any]) -> SendOutcome:
        logger.debug(
            "Push not sent: no FCM service account is configured",
            extra={"context": {"state": message.get("data", {}).get("state")}},
        )
        return SendOutcome.FAILED

    async def aclose(self) -> None:
        return None


class FcmSender:
    """Sends through FCM HTTP v1 as one service account.

    ``transport`` is the network unless a test answers in FCM's and Google's
    place, as ``app.state.hook_transport`` does for hooks.

    One HTTP client serves every message for the sender's lifetime, so the
    connections to Google and FCM are opened once and kept alive rather than
    paying a TLS handshake per push; :meth:`aclose` shuts it at shutdown.
    """

    def __init__(
        self,
        account: ServiceAccount,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = epoch,
    ) -> None:
        self._account = account
        self._clock = clock
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._http = httpx.AsyncClient(transport=transport, timeout=TIMEOUT)

    @property
    def enabled(self) -> bool:
        return True

    @property
    def project_id(self) -> str:
        return self._account.project_id

    async def send(self, message: dict[str, Any]) -> SendOutcome:
        """Send one message, minting or refreshing the access token as needed.

        A 401 means the access token was refused — revoked, or the clock is
        off — so it is minted again and the message tried once more.
        """
        try:
            response = await self._post(message)
            if response.status_code == httpx.codes.UNAUTHORIZED:
                self._access_token = None
                response = await self._post(message)
        except (httpx.HTTPError, _TokenExchangeError) as error:
            logger.warning(
                "Push failed before FCM answered",
                extra={"context": {"error": type(error).__name__, "detail": str(error)}},
            )
            return SendOutcome.FAILED
        return _outcome(response, message)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, message: dict[str, Any]) -> httpx.Response:
        token = await self._token()
        return await self._http.post(
            SEND_URL.format(project_id=self._account.project_id),
            json={"message": message},
            headers={"Authorization": f"Bearer {token}"},
        )

    async def _token(self) -> str:
        moment = self._clock()
        if self._access_token is not None and moment < self._expires_at - REFRESH_MARGIN:
            return self._access_token
        response = await self._http.post(
            self._account.token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self._account.assertion(int(moment)),
            },
        )
        if response.status_code != httpx.codes.OK:
            raise _TokenExchangeError(f"token endpoint answered {response.status_code}")
        # A 200 that is not a JSON object — a captive portal's page, a proxy's
        # error dressed as success — is a refusal like any other, so it fails
        # this one message and not the rest of the devices it was going to.
        try:
            body = response.json()
        except ValueError:
            raise _TokenExchangeError("token endpoint answered with something not JSON") from None
        if not isinstance(body, dict):
            raise _TokenExchangeError("token endpoint answered with something not an object")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise _TokenExchangeError("token endpoint returned no access_token")
        expires_in = body.get("expires_in", TOKEN_LIFETIME)
        self._access_token = token
        self._expires_at = moment + (expires_in if isinstance(expires_in, int) else TOKEN_LIFETIME)
        return token


class _TokenExchangeError(Exception):
    """Google would not trade the assertion for an access token."""


def _outcome(response: httpx.Response, message: dict[str, Any]) -> SendOutcome:
    """Read FCM's answer. Only an explicit ``UNREGISTERED`` forgets a device.

    A 400 can mean the token is malformed, but it can as easily mean this
    server built a bad message — and forgetting every phone because of our own
    bug would be the worse failure — so it is logged and left.
    """
    if response.is_success:
        return SendOutcome.SENT
    code = _error_code(response)
    device = redact(str(message.get("token", "")))
    if response.status_code == httpx.codes.NOT_FOUND or code == "UNREGISTERED":
        logger.info(
            "FCM says a device is gone; forgetting it",
            extra={"context": {"device": device, "code": code}},
        )
        return SendOutcome.UNREGISTERED
    logger.warning(
        "FCM refused a push",
        extra={"context": {"status": response.status_code, "code": code, "device": device}},
    )
    return SendOutcome.FAILED


def _error_code(response: httpx.Response) -> str | None:
    """The ``FcmError`` code in a v1 error body, if there is one."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and isinstance(detail.get("errorCode"), str):
            code: str = detail["errorCode"]
            return code
    status = error.get("status")
    return status if isinstance(status, str) else None


def sender_for(key_file: Path | None) -> PushSender:
    """The sender a deployment's configuration asks for.

    No key: :class:`NullSender`, said once at startup. A key that cannot be
    read: :class:`NullSender` too, said as an error — a broken secret should
    cost notifications, not the whole server — naming the file and what is
    wrong with it, never its contents.
    """
    if key_file is None:
        logger.info("Push notifications are off: CYLIST_FCM_SERVICE_ACCOUNT_FILE is not set")
        return NullSender()
    try:
        account = ServiceAccount.from_file(key_file)
    except ServiceAccountError as error:
        logger.error(
            "Push notifications are off: the FCM service account could not be loaded",
            extra={"context": {"file": str(key_file), "problem": str(error)}},
        )
        return NullSender()
    logger.info(
        "Push notifications are on",
        extra={"context": {"firebase_project": account.project_id}},
    )
    return FcmSender(account)
