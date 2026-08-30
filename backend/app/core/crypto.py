"""Encryption for vault secrets.

AES-256-GCM from ``cryptography``, keyed by ``CYLIST_VAULT_KEY`` (32 bytes,
base64url — ``python -m app.cli generate-vault-key`` prints one). GCM is an
AEAD, so one primitive gives both confidentiality and an authentication tag: a
ciphertext that has been altered fails to open rather than decrypting to
plausible rubbish.

Three properties are worth spelling out, because getting any of them wrong is
the difference between encryption and the appearance of it.

**A nonce is never reused with a key.** Reusing one under AES-GCM is
catastrophic — it leaks the XOR of the two plaintexts and, worse, lets an
attacker forge tags for that key. :meth:`VaultCipher.seal` therefore takes no
nonce argument: there is no way for a caller to supply, cache or replay one.
Each call draws a fresh 96-bit nonce from the CSPRNG and prepends it to the
ciphertext it protects, so the two travel in a single column and cannot be
paired up wrongly by a partial write or a mistaken join. Random 96-bit nonces
are the construction NIST SP 800-38D gives for when a counter cannot be kept
reliably (here it could not: the app is stateless across restarts); the
collision probability stays under 2⁻³² for the first ~2³² secrets written
under one key, which a personal vault will not approach.

**A ciphertext is bound to the row it belongs to.** The node's id goes into
GCM's associated data. Associated data is authenticated but not encrypted, so
a ciphertext copied from one ``vault_secret`` row into another fails its tag
check and raises :class:`SecretUnreadableError` — rather than quietly
revealing the wrong credential under the wrong name, which is the failure that
would actually hurt.

**The key can be rotated.** Every ciphertext is stored with the
:data:`KEY_VERSION` that produced it, and that version is both honoured and
authenticated on the way back, so introducing a version 2 key later is a
migration rather than a flag day.

When the key is absent or malformed, every operation raises
:class:`VaultUnavailableError`. Nothing here has a path that stores a secret
in the clear or accepts one it cannot verify.
"""

from __future__ import annotations

import base64
import secrets
from functools import lru_cache
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import status

from app.core.errors import AppError

KEY_VERSION = 1
"""Which key ``CYLIST_VAULT_KEY`` currently holds.

Written beside every ciphertext. Rotation means adding version 2, re-sealing
existing rows in the background, and only then retiring version 1.
"""

_KEY_BYTES = 32
"""AES-256."""

_NONCE_BYTES = 12
"""96 bits — the only nonce length AES-GCM is specified for."""

_TAG_BYTES = 16
"""GCM's authentication tag, which ``cryptography`` appends to the ciphertext."""

_AAD_DOMAIN = "cylist.vault.secret"
"""Domain separator, so a ciphertext from some future use of this key cannot
be replayed into the vault even if the node ids happened to line up."""


class VaultUnavailableError(AppError):
    """``CYLIST_VAULT_KEY`` is missing or unusable.

    A 500, not a 4xx: the caller did nothing wrong, the deployment is
    misconfigured. Failing here is the point — the alternative would be
    storing credentials in the clear.
    """

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "vault_unavailable"


class SecretUnreadableError(AppError):
    """A stored ciphertext did not authenticate under the key and node it claims.

    Means one of: the value was tampered with, it was moved between rows, or
    it was written under a key this deployment no longer has. The message
    never carries the ciphertext or any part of a plaintext.
    """

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "secret_unreadable"


class VaultCipher:
    """Seals and opens vault secrets under one key."""

    def __init__(self, encoded_key: str) -> None:
        self._aead = AESGCM(_decode_key(encoded_key))

    def seal(self, plaintext: str, *, node_id: UUID) -> bytes:
        """Encrypt a secret for one node.

        Returns the 96-bit nonce followed by the ciphertext and its tag, ready
        to store in ``vault_secret.secret_ciphertext``. The nonce is generated
        here and nowhere else; see the module docstring.
        """
        nonce = secrets.token_bytes(_NONCE_BYTES)
        sealed = self._aead.encrypt(
            nonce, plaintext.encode(), _associated_data(node_id, KEY_VERSION)
        )
        return nonce + sealed

    def open(self, sealed: bytes, *, node_id: UUID, key_version: int) -> str:
        """Decrypt a secret, or raise :class:`SecretUnreadableError`.

        Args:
            sealed: Exactly what :meth:`seal` returned.
            node_id: The node the row hangs off. A mismatch fails the tag.
            key_version: The version stored with the row.
        """
        if key_version != KEY_VERSION:
            raise SecretUnreadableError(
                f"That secret was encrypted with vault key version {key_version}, "
                f"and this deployment holds version {KEY_VERSION}."
            )
        if len(sealed) < _NONCE_BYTES + _TAG_BYTES:
            raise SecretUnreadableError("That secret is stored truncated and cannot be read.")

        nonce, ciphertext = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
        try:
            plaintext = self._aead.decrypt(
                nonce, ciphertext, _associated_data(node_id, key_version)
            )
        except InvalidTag as exc:
            raise SecretUnreadableError(
                "That secret failed its authentication check: it has been altered, "
                "moved between entries, or encrypted with a different key."
            ) from exc
        return plaintext.decode()


@lru_cache(maxsize=2)
def cipher_for(encoded_key: str) -> VaultCipher:
    """Return the cipher for a key, building it at most once.

    AES key expansion is not free and the vault list is fetched on every page
    load. Keying the cache on the configured value rather than holding a
    module global means a test that supplies its own key gets its own cipher.
    """
    return VaultCipher(encoded_key)


def _associated_data(node_id: UUID, key_version: int) -> bytes:
    """The context a ciphertext is bound to: this node, under this key version.

    Authenticated but not encrypted, which is exactly what is wanted — neither
    value is a secret, and both must be identical on the way back or the tag
    check fails.
    """
    return f"{_AAD_DOMAIN}.v{key_version}:{node_id}".encode()


def _decode_key(encoded: str) -> bytes:
    """Turn ``CYLIST_VAULT_KEY`` into 32 raw bytes, or explain why it cannot."""
    raw = encoded.strip()
    if not raw:
        raise VaultUnavailableError(
            "CYLIST_VAULT_KEY is not set, so vault secrets can be neither stored "
            "nor read. Generate a key with `make vault-key`."
        )

    try:
        # validate=True, so a key with a stray character is rejected rather
        # than silently decoding to the wrong 32 bytes.
        key = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise VaultUnavailableError(
            "CYLIST_VAULT_KEY is not valid base64url. Generate a key with `make vault-key`."
        ) from exc

    if len(key) != _KEY_BYTES:
        raise VaultUnavailableError(
            f"CYLIST_VAULT_KEY must decode to {_KEY_BYTES} bytes for AES-256; "
            f"this one decodes to {len(key)}. Generate a key with `make vault-key`."
        )
    return key
