"""A software authenticator, so the passkey tests run the real verification.

Speaks just enough WebAuthn to register and sign in: one P-256 key, ``none``
attestation, user presence and verification always set, and a counter the test
may set to anything. It is the *device*; ``py_webauthn`` in the application is
what checks it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

RP_ID = "localhost"
ORIGIN = "http://localhost:5173"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Device:
    def __init__(self, *, rp_id: str = RP_ID, origin: str = ORIGIN) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.rp_id = rp_id
        self.origin = origin
        self.counter = 0
        self.user_handle = b""

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def _auth_data(self, flags: int, extra: bytes = b"") -> bytes:
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        return rp_hash + bytes([flags]) + struct.pack(">I", self.counter) + extra

    def create(self, options: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
        """What ``navigator.credentials.create(options).toJSON()`` returns."""
        self.user_handle = unb64(options["user"]["id"])
        client_data = json.dumps(
            {
                "type": "webauthn.create",
                "challenge": options["challenge"],
                "origin": origin or self.origin,
            }
        ).encode()
        attested = (
            b"\0" * 16
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self._cose_key()
        )
        auth_data = self._auth_data(0x45, attested)  # UP | UV | AT
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "attestationObject": b64(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def get(
        self,
        options: dict[str, Any],
        *,
        origin: str | None = None,
        count: int | None = None,
        flags: int = 0x05,
    ) -> dict[str, Any]:
        """What ``navigator.credentials.get(options).toJSON()`` returns.

        ``count`` sets the counter the authenticator reports; left out, it
        counts up by one like a real one.
        """
        self.counter = self.counter + 1 if count is None else count
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": options["challenge"],
                "origin": origin or self.origin,
            }
        ).encode()
        auth_data = self._auth_data(flags)  # UP | UV
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "authenticatorData": b64(auth_data),
                "signature": b64(signature),
                "userHandle": b64(self.user_handle),
            },
            "clientExtensionResults": {},
        }
