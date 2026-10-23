"""Encryption for vault secrets.

AES-256-GCM from ``cryptography``, keyed by ``CYLIST_VAULT_KEY`` and any later
versions in ``CYLIST_VAULT_KEYS`` (32 bytes each, base64url —
``python -m app.cli generate-vault-key`` prints one). GCM is an
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

**The key can be rotated.** A deployment holds a *keyring* — numbered keys,
:attr:`Settings.vault_keyring <app.config.Settings.vault_keyring>` — rather than
one key. Every ciphertext is stored with the version that produced it, that
version is authenticated on the way back, and the cipher opens each row with
the key it names. New rows are sealed with the newest key. So rotating is:
add a key with a higher number, run ``python -m app.cli vault-reseal`` to move
every existing row onto it (:mod:`app.services.reseal`), then retire the old
key. Nothing is unreadable at any point along the way.

When no key is configured, or any configured key is malformed, every
operation raises :class:`VaultUnavailableError`. Nothing here has a path that stores a secret
in the clear or accepts one it cannot verify.
"""

from __future__ import annotations

import base64
import secrets
from collections.abc import Mapping
from functools import lru_cache
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import status

from app.core.errors import AppError

VaultKeys = tuple[tuple[int, str], ...]
"""A keyring as configuration hands it over: ``(version, base64url key)`` pairs.

A tuple rather than a dict so it can key :func:`cipher_for`'s cache."""

_KEY_BYTES = 32
"""AES-256."""

_NONCE_BYTES = 12
"""96 bits — the only nonce length AES-GCM is specified for."""

_TAG_BYTES = 16
"""GCM's authentication tag, which ``cryptography`` appends to the ciphertext."""

VAULT_DOMAIN = "cylist.vault.secret"
"""Domain separator, so a ciphertext from some future use of this key cannot
be replayed into the vault even if the node ids happened to line up."""

HOOK_SECRET_DOMAIN = "cylist.hook.secret"  # noqa: S105 - a label, not a secret
"""The first such use: a hook's signing secret, sealed under the vault key and
bound to the hook's id. Its own domain, so a hook's ciphertext copied into a
``vault_secret`` row — or the other way round — fails its tag check."""


class VaultUnavailableError(AppError):
    """No vault key is configured, or one that is cannot be used.

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


_NO_VAULT_KEY_MESSAGE = (
    "CYLIST_VAULT_KEY is not set, so vault secrets can be neither stored "
    "nor read. Generate a key with `make vault-key`."
)


class VaultCipher:
    """Seals vault secrets under the newest key, and opens them under any key held."""

    def __init__(self, keys: Mapping[int, str]) -> None:
        if not keys:
            raise VaultUnavailableError(_NO_VAULT_KEY_MESSAGE)
        # Every key is decoded now, not when a row first needs it: a typo in a
        # key nobody has used yet would otherwise surface weeks later, as one
        # unreadable secret, instead of here as a misconfiguration.
        self._aeads = {
            version: AESGCM(_decode_key(key, version)) for version, key in sorted(keys.items())
        }
        self.version = max(self._aeads)
        """The key :meth:`seal` uses, and so the ``key_version`` to store beside
        what it returns."""

    @property
    def versions(self) -> tuple[int, ...]:
        """Every key version this cipher can open, oldest first."""
        return tuple(self._aeads)

    def seal(self, plaintext: str, *, node_id: UUID, domain: str = VAULT_DOMAIN) -> bytes:
        """Encrypt a secret for one node, under :attr:`version`.

        Returns the 96-bit nonce followed by the ciphertext and its tag, ready
        to store in ``vault_secret.secret_ciphertext`` beside :attr:`version`.
        The nonce is generated here and nowhere else; see the module docstring.
        """
        nonce = secrets.token_bytes(_NONCE_BYTES)
        sealed = self._aeads[self.version].encrypt(
            nonce, plaintext.encode(), _associated_data(node_id, self.version, domain)
        )
        return nonce + sealed

    def open(
        self, sealed: bytes, *, node_id: UUID, key_version: int, domain: str = VAULT_DOMAIN
    ) -> str:
        """Decrypt a secret, or raise :class:`SecretUnreadableError`.

        Args:
            sealed: Exactly what :meth:`seal` returned.
            node_id: The node the row hangs off. A mismatch fails the tag.
            key_version: The version stored with the row. It picks the key and
                is part of the associated data, so a row whose version was
                rewritten does not open under the other key either.
            domain: What kind of secret it is — the vault's own unless given.
                Must be the one it was sealed under.
        """
        aead = self._aeads.get(key_version)
        if aead is None:
            held = ", ".join(str(version) for version in self._aeads)
            raise SecretUnreadableError(
                f"That secret was encrypted with vault key version {key_version}, "
                f"and this deployment holds only version(s) {held}. Put that key "
                "back in CYLIST_VAULT_KEYS to read it."
            )
        if len(sealed) < _NONCE_BYTES + _TAG_BYTES:
            raise SecretUnreadableError("That secret is stored truncated and cannot be read.")

        nonce, ciphertext = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
        try:
            plaintext = aead.decrypt(
                nonce, ciphertext, _associated_data(node_id, key_version, domain)
            )
        except InvalidTag as exc:
            raise SecretUnreadableError(
                "That secret failed its authentication check: it has been altered, "
                "moved between entries, or encrypted with a different key."
            ) from exc
        return plaintext.decode()


@lru_cache(maxsize=4)
def cipher_for(keys: VaultKeys) -> VaultCipher:
    """Return the cipher for a keyring, building it at most once.

    AES key expansion is not free and the vault list is fetched on every page
    load. Keying the cache on the configured value rather than holding a
    module global means a test that supplies its own keys gets its own cipher.
    """
    return VaultCipher(dict(keys))


def configured_cipher(keys: VaultKeys) -> VaultCipher | VaultUnavailableError:
    """The cipher for a keyring, or the reason there is not one.

    For building one at startup. A deployment with no key, or a malformed one,
    must still boot — the board has nothing to do with the vault — so the
    refusal is kept as a value and raised by whoever first asks for the
    cipher, with the message that says what is wrong.
    """
    try:
        return cipher_for(keys)
    except VaultUnavailableError as exc:
        return exc


def _associated_data(node_id: UUID, key_version: int, domain: str = VAULT_DOMAIN) -> bytes:
    """The context a ciphertext is bound to: this node, under this key version.

    Authenticated but not encrypted, which is exactly what is wanted — neither
    value is a secret, and both must be identical on the way back or the tag
    check fails.
    """
    return f"{domain}.v{key_version}:{node_id}".encode()


def _decode_key(encoded: str, version: int) -> bytes:
    """Turn one configured key into 32 raw bytes, or explain why it cannot.

    Version 1 is usually ``CYLIST_VAULT_KEY`` and every later one an entry in
    ``CYLIST_VAULT_KEYS``, so the message names both rather than guess.
    """
    name = "CYLIST_VAULT_KEY" if version == 1 else f"Vault key version {version}"
    if version < 1:
        raise VaultUnavailableError(
            f"Vault key versions start at 1; CYLIST_VAULT_KEYS has a version {version}."
        )
    raw = encoded.strip()
    if not raw:
        raise VaultUnavailableError(
            f"{name} is empty, so vault secrets can be neither stored nor read. "
            "Generate a key with `make vault-key`."
        )

    try:
        # validate=True, so a key with a stray character is rejected rather
        # than silently decoding to the wrong 32 bytes.
        key = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise VaultUnavailableError(
            f"{name} is not valid base64url. Generate a key with `make vault-key`."
        ) from exc

    if len(key) != _KEY_BYTES:
        raise VaultUnavailableError(
            f"{name} must decode to {_KEY_BYTES} bytes for AES-256; "
            f"this one decodes to {len(key)}. Generate a key with `make vault-key`."
        )
    return key
