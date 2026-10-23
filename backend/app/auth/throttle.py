"""How often anybody may try to sign in (CYLIST-121).

Two buckets per attempt at ``POST /auth/login``: one for the address it came
from, one for the account it named. The address bucket caps what one machine
can throw at the endpoint, whoever it is guessing at; the account bucket caps
what the whole internet can throw at one person, however many addresses it
has. ``POST /auth/accept-invite`` names no account and is limited by address
alone.

An attempt is charged before the password is checked, because the check is
the expensive part — see :mod:`app.auth.passwords` — and a limiter that only
counted failures would still let the first forty attempts in a second run.
A correct password costs one token like any other; nobody signs in forty
times a minute.

The refusal is a 429 with ``Retry-After``, which is a different answer from
the 401 a wrong password gets on purpose: a client that is being told to slow
down should not be told its password was wrong.
"""

from __future__ import annotations

from app.core.errors import TooManyRequestsError
from app.core.metrics import live
from app.core.ratelimit import TokenBuckets

ADDRESS_BURST = 30
ADDRESS_PER_MINUTE = 30
"""What one address may do: thirty attempts at once, thirty a minute after.
Enough for an office behind one NAT signing in on a Monday morning; not
enough to try a password list."""

ACCOUNT_BURST = 5
ACCOUNT_PER_MINUTE = 5
"""What may be tried against one account: five, then one every twelve
seconds. A person who has forgotten their password notices nothing; a
dictionary attack on a twelve-character password gets nowhere in a year."""

BOOTSTRAP_ACCOUNT = "\0bootstrap"
"""The account key for a login that names nobody — the bootstrap password —
so that guessing at it is limited like guessing at anyone else's."""


class SignInThrottle:
    """The buckets behind the sign-in endpoints, one set per process."""

    def __init__(
        self,
        *,
        address_burst: int = ADDRESS_BURST,
        address_per_minute: float = ADDRESS_PER_MINUTE,
        account_burst: int = ACCOUNT_BURST,
        account_per_minute: float = ACCOUNT_PER_MINUTE,
    ) -> None:
        self._by_address = TokenBuckets(burst=address_burst, per_minute=address_per_minute)
        self._by_account = TokenBuckets(burst=account_burst, per_minute=account_per_minute)

    def charge(self, *, address: str | None, account: str | None) -> None:
        """Spend one attempt, or refuse it.

        The address is charged first, and an attempt the address bucket
        refuses is not charged to the account: one machine hammering the
        endpoint should not lock the person it is naming out of their own
        account from elsewhere.

        Args:
            address: The client address, or ``None`` when there is none —
                which is charged as one shared key rather than waved through.
            account: The email named, folded; ``None`` for an attempt that
                names no account (accepting an invitation).

        Raises:
            TooManyRequestsError: with how long to wait.
        """
        wait = self._by_address.take(address or "")
        if wait is not None:
            live().rate_limit_hits["address"] += 1
            raise TooManyRequestsError(
                "Too many sign-in attempts from this address. Try again shortly.",
                retry_after=wait,
            )
        if account is None:
            return
        wait = self._by_account.take(account)
        if wait is not None:
            live().rate_limit_hits["account"] += 1
            raise TooManyRequestsError(
                "Too many sign-in attempts for this account. Try again shortly.",
                retry_after=wait,
            )


def account_key(email: str | None) -> str:
    """The bucket an attempt is charged to: the address as the login looks it
    up, or the bootstrap key for a login that names nobody."""
    if email is None:
        return BOOTSTRAP_ACCOUNT
    return email.strip().lower()
