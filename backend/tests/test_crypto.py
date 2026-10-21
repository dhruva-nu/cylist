"""The vault cipher.

These are the tests that matter most in Cylist. Everything above them assumes
that a sealed secret round-trips, that a tampered one refuses to open, and
that one lifted out of another row refuses too.
"""

from __future__ import annotations

import base64

import pytest

from app.core.crypto import (
    SecretUnreadableError,
    VaultCipher,
    VaultUnavailableError,
    _associated_data,
    cipher_for,
)
from app.core.ids import uuid7
from tests.conftest import VAULT_KEY, VAULT_KEYS

NODE = uuid7()
OTHER_NODE = uuid7()
SECRET = "Tw!9xLp3#Qm"
KEY_VERSION = 1
"""What a deployment with only ``CYLIST_VAULT_KEY`` seals under."""
NEW_KEY = base64.urlsafe_b64encode(b"n" * 32).decode()
"""A second key, as a rotation adds one."""


@pytest.fixture
def cipher() -> VaultCipher:
    return VaultCipher({1: VAULT_KEY})


class TestRoundTrip:
    def test_a_sealed_secret_opens_to_what_went_in(self, cipher: VaultCipher) -> None:
        sealed = cipher.seal(SECRET, node_id=NODE)

        assert cipher.open(sealed, node_id=NODE, key_version=KEY_VERSION) == SECRET

    def test_survives_unicode(self, cipher: VaultCipher) -> None:
        """Passphrases are not ASCII, and neither are the notes beside them."""
        secret = "pässwörd — 日本語 · 🔐 · Ωμέγα"

        sealed = cipher.seal(secret, node_id=NODE)

        assert cipher.open(sealed, node_id=NODE, key_version=KEY_VERSION) == secret

    def test_survives_a_long_value(self, cipher: VaultCipher) -> None:
        """A PEM private key is several kilobytes, not a password."""
        secret = "-----BEGIN PRIVATE KEY-----\n" + ("MIIEvQIBADAN" * 400) + "\n-----END-----"

        sealed = cipher.seal(secret, node_id=NODE)

        assert cipher.open(sealed, node_id=NODE, key_version=KEY_VERSION) == secret

    def test_an_empty_string_round_trips(self, cipher: VaultCipher) -> None:
        """The API forbids one, but the cipher must not depend on that."""
        assert cipher.open(cipher.seal("", node_id=NODE), node_id=NODE, key_version=1) == ""

    def test_the_plaintext_is_nowhere_in_the_ciphertext(self, cipher: VaultCipher) -> None:
        sealed = cipher.seal("hunter2-hunter2-hunter2", node_id=NODE)

        assert b"hunter2" not in sealed


class TestFreshNonce:
    def test_the_same_secret_seals_differently_every_time(self, cipher: VaultCipher) -> None:
        """A nonce repeated under one key breaks GCM outright, so this is the
        property the whole scheme rests on."""
        first = cipher.seal("same secret", node_id=NODE)
        second = cipher.seal("same secret", node_id=NODE)

        assert first != second
        assert first[:12] != second[:12]  # the nonce itself differs
        assert cipher.open(second, node_id=NODE, key_version=KEY_VERSION) == "same secret"

    def test_nonces_do_not_repeat_across_many_seals(self, cipher: VaultCipher) -> None:
        nonces = {cipher.seal("x", node_id=NODE)[:12] for _ in range(500)}

        assert len(nonces) == 500

    def test_seal_offers_no_way_to_supply_a_nonce(self, cipher: VaultCipher) -> None:
        """Not a style point: an interface that accepts a nonce is one that
        will eventually be handed the same nonce twice."""
        with pytest.raises(TypeError):
            cipher.seal("x", node_id=NODE, nonce=b"0" * 12)


class TestTampering:
    def test_flipping_one_byte_of_ciphertext_is_caught(self, cipher: VaultCipher) -> None:
        sealed = bytearray(cipher.seal(SECRET, node_id=NODE))
        sealed[20] ^= 0x01

        with pytest.raises(SecretUnreadableError):
            cipher.open(bytes(sealed), node_id=NODE, key_version=KEY_VERSION)

    def test_flipping_one_byte_of_the_nonce_is_caught(self, cipher: VaultCipher) -> None:
        sealed = bytearray(cipher.seal(SECRET, node_id=NODE))
        sealed[0] ^= 0x01

        with pytest.raises(SecretUnreadableError):
            cipher.open(bytes(sealed), node_id=NODE, key_version=KEY_VERSION)

    def test_flipping_one_byte_of_the_tag_is_caught(self, cipher: VaultCipher) -> None:
        sealed = bytearray(cipher.seal(SECRET, node_id=NODE))
        sealed[-1] ^= 0x01

        with pytest.raises(SecretUnreadableError):
            cipher.open(bytes(sealed), node_id=NODE, key_version=KEY_VERSION)

    def test_every_single_byte_is_protected(self, cipher: VaultCipher) -> None:
        """Not one position in nonce, ciphertext or tag can be altered
        undetected — no partial-authentication gap anywhere in the blob."""
        sealed = cipher.seal(SECRET, node_id=NODE)

        for index in range(len(sealed)):
            altered = bytearray(sealed)
            altered[index] ^= 0x80
            with pytest.raises(SecretUnreadableError):
                cipher.open(bytes(altered), node_id=NODE, key_version=KEY_VERSION)

    def test_a_truncated_value_is_rejected_rather_than_crashing(self, cipher: VaultCipher) -> None:
        sealed = cipher.seal(SECRET, node_id=NODE)

        with pytest.raises(SecretUnreadableError):
            cipher.open(sealed[:8], node_id=NODE, key_version=KEY_VERSION)

    def test_the_failure_never_repeats_the_secret(self, cipher: VaultCipher) -> None:
        sealed = bytearray(cipher.seal(SECRET, node_id=NODE))
        sealed[20] ^= 0x01

        with pytest.raises(SecretUnreadableError) as raised:
            cipher.open(bytes(sealed), node_id=NODE, key_version=KEY_VERSION)

        assert SECRET not in str(raised.value)
        assert sealed.hex()[:16] not in str(raised.value)


class TestBindingToTheNode:
    def test_a_ciphertext_moved_to_another_node_will_not_open(self, cipher: VaultCipher) -> None:
        """Copying one row's bytes into another must fail, rather than quietly
        handing back somebody else's credential under the wrong name."""
        sealed = cipher.seal(SECRET, node_id=NODE)

        with pytest.raises(SecretUnreadableError):
            cipher.open(sealed, node_id=OTHER_NODE, key_version=KEY_VERSION)

    def test_it_still_opens_for_its_own_node(self, cipher: VaultCipher) -> None:
        sealed = cipher.seal(SECRET, node_id=NODE)

        assert cipher.open(sealed, node_id=NODE, key_version=KEY_VERSION) == SECRET

    def test_the_binding_covers_both_the_node_and_the_key_version(self) -> None:
        """What goes into the tag: change either and the tag no longer matches."""
        assert _associated_data(NODE, 1) != _associated_data(OTHER_NODE, 1)
        assert _associated_data(NODE, 1) != _associated_data(NODE, 2)
        assert str(NODE).encode() in _associated_data(NODE, 1)


class TestKeyVersioning:
    def test_a_version_this_deployment_does_not_hold_is_refused(self, cipher: VaultCipher) -> None:
        sealed = cipher.seal(SECRET, node_id=NODE)

        with pytest.raises(SecretUnreadableError) as raised:
            cipher.open(sealed, node_id=NODE, key_version=KEY_VERSION + 1)

        # Says which version the row wants and which ones are here, so the
        # operator knows which key to put back.
        assert "version 2" in str(raised.value)
        assert "version(s) 1" in str(raised.value)

    def test_seal_stamps_the_current_version(self, cipher: VaultCipher) -> None:
        sealed = cipher.seal(SECRET, node_id=NODE)

        assert cipher.version == KEY_VERSION
        assert cipher.open(sealed, node_id=NODE, key_version=KEY_VERSION) == SECRET


class TestRotation:
    """Adding a key: new rows go under it, old rows still open under theirs."""

    @pytest.fixture
    def rotated(self) -> VaultCipher:
        return VaultCipher({1: VAULT_KEY, 2: NEW_KEY})

    def test_the_newest_key_seals(self, rotated: VaultCipher) -> None:
        sealed = rotated.seal(SECRET, node_id=NODE)

        assert rotated.version == 2
        assert rotated.open(sealed, node_id=NODE, key_version=2) == SECRET
        # Really under the new key, not the old one with a new label.
        with pytest.raises(SecretUnreadableError):
            VaultCipher({1: VAULT_KEY}).open(sealed, node_id=NODE, key_version=1)

    def test_a_row_sealed_before_the_rotation_still_opens(
        self, cipher: VaultCipher, rotated: VaultCipher
    ) -> None:
        sealed = cipher.seal(SECRET, node_id=NODE)

        assert rotated.open(sealed, node_id=NODE, key_version=1) == SECRET

    def test_the_version_is_authenticated_not_merely_a_lookup(
        self, cipher: VaultCipher, rotated: VaultCipher
    ) -> None:
        """A version-1 row relabelled 2 fails, rather than being tried under
        whichever key happens to work."""
        sealed = cipher.seal(SECRET, node_id=NODE)

        with pytest.raises(SecretUnreadableError):
            rotated.open(sealed, node_id=NODE, key_version=2)

    def test_the_old_key_can_be_retired(self, rotated: VaultCipher) -> None:
        sealed = rotated.seal(SECRET, node_id=NODE)
        retired = VaultCipher({2: NEW_KEY})

        assert retired.versions == (2,)
        assert retired.open(sealed, node_id=NODE, key_version=2) == SECRET

    def test_a_retired_key_leaves_its_rows_clearly_unreadable(self, cipher: VaultCipher) -> None:
        sealed = cipher.seal(SECRET, node_id=NODE)

        with pytest.raises(SecretUnreadableError) as raised:
            VaultCipher({2: NEW_KEY}).open(sealed, node_id=NODE, key_version=1)

        assert "version 1" in str(raised.value)
        assert "version(s) 2" in str(raised.value)

    def test_versions_need_not_be_contiguous(self) -> None:
        cipher = VaultCipher({1: VAULT_KEY, 7: NEW_KEY})

        assert cipher.version == 7
        assert cipher.versions == (1, 7)

    def test_a_malformed_later_key_is_named_by_its_version(self) -> None:
        """Every key is checked when the cipher is built, not when a row first
        needs it."""
        with pytest.raises(VaultUnavailableError) as raised:
            VaultCipher({1: VAULT_KEY, 3: "not a key!!!"})

        assert "version 3" in str(raised.value)

    def test_a_version_below_one_is_refused(self) -> None:
        with pytest.raises(VaultUnavailableError):
            VaultCipher({0: VAULT_KEY})


class TestAnotherKey:
    def test_a_different_key_cannot_open_it(self, cipher: VaultCipher) -> None:
        other = VaultCipher({1: base64.urlsafe_b64encode(b"a" * 32).decode()})
        sealed = cipher.seal(SECRET, node_id=NODE)

        with pytest.raises(SecretUnreadableError):
            other.open(sealed, node_id=NODE, key_version=KEY_VERSION)


class TestKeyLoading:
    def test_an_unset_key_fails_loudly(self) -> None:
        """Nothing may fall back to storing plaintext because `make vault-key`
        was never run."""
        with pytest.raises(VaultUnavailableError) as raised:
            VaultCipher({})

        assert "CYLIST_VAULT_KEY" in str(raised.value)

    def test_an_empty_key_is_refused_rather_than_skipped(self) -> None:
        with pytest.raises(VaultUnavailableError) as raised:
            VaultCipher({1: ""})

        assert "CYLIST_VAULT_KEY" in str(raised.value)

    def test_whitespace_is_not_a_key(self) -> None:
        with pytest.raises(VaultUnavailableError):
            VaultCipher({1: "   \n"})

    def test_a_key_that_is_not_base64_is_refused(self) -> None:
        with pytest.raises(VaultUnavailableError) as raised:
            VaultCipher({1: "not a key!!!" * 4})

        assert "base64url" in str(raised.value)

    def test_a_key_of_the_wrong_length_is_refused(self) -> None:
        with pytest.raises(VaultUnavailableError) as raised:
            VaultCipher({1: base64.urlsafe_b64encode(b"too short").decode()})

        assert "32 bytes" in str(raised.value)

    def test_the_cli_format_is_accepted(self) -> None:
        """Whatever `python -m app.cli generate-vault-key` prints must work."""
        from app.cli import _VAULT_KEY_BYTES

        key = base64.urlsafe_b64encode(b"k" * _VAULT_KEY_BYTES).decode()

        assert VaultCipher({1: key}).seal("x", node_id=NODE)

    def test_padding_is_optional(self) -> None:
        """Copying a key out of a secret store often loses the trailing '='."""
        padded = base64.urlsafe_b64encode(b"k" * 32).decode()

        assert VaultCipher({1: padded.rstrip("=")}).seal("x", node_id=NODE)

    def test_the_urlsafe_alphabet_is_decoded_as_urlsafe(self) -> None:
        """`-` and `_` must mean 62 and 63, not be discarded as noise."""
        raw = bytes([0xFB, 0xFF, 0xBF]) * 10 + b"\x00\x00"
        assert "-" in base64.urlsafe_b64encode(raw).decode()

        cipher = VaultCipher({1: base64.urlsafe_b64encode(raw).decode()})

        assert cipher.open(cipher.seal("x", node_id=NODE), node_id=NODE, key_version=1) == "x"


class TestCipherCache:
    def test_the_same_key_returns_the_same_cipher(self) -> None:
        assert cipher_for(VAULT_KEYS) is cipher_for(VAULT_KEYS)

    def test_a_bad_key_keeps_raising_rather_than_caching_a_broken_cipher(self) -> None:
        for _ in range(2):
            with pytest.raises(VaultUnavailableError):
                cipher_for(())
