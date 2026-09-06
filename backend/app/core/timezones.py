"""Naming a time zone the way callers actually name it.

Anything cut at local midnight has to be told which local — and callers name
zones loosely. A browser says ``Asia/Calcutta`` because that is the key its own
copy of the database happens to be built around; a shell says ``:Asia/Kolkata``
because that leading colon is POSIX's own; a person types ``asia/kolkata`` or
``America/New York``; an agent typing from memory drops a letter and says
``Asia/Cacutta``. Only some of those are keys :mod:`zoneinfo` will open, and
the rest used to cost a whole report a 422 over the spelling of its window.

The first thing that takes is having the whole database to look in.
``Asia/Calcutta`` is a real IANA name — a link kept in its ``backward`` file —
but the slim base image the container is built on ships only the canonical
zones, so on a deployment it was not a key at all while on a developer's
machine it was. That is why the backend depends on ``tzdata``: zoneinfo falls
back to the package for whatever it cannot find on TZPATH, and the zones a
deployment knows become the lockfile's business rather than the image's.

So the name is widened in steps, and the first step that lands wins:

1. the name as given — a key the database holds, links like ``Asia/Calcutta``
   included, never pays for any of the rest;
2. the name tidied — surrounding space, the colon ``TZ`` prefixes a filename
   with, and the spaces written where the database keeps underscores;
3. the name matched without regard to case, against the keys the database
   actually holds;
4. the nearest key to it, when that is close enough and is the only one that
   close — one letter out of ``Asia/Cacutta`` is ``Asia/Calcutta``, and no
   other zone in Asia is anywhere near it.

Step 4 is a guess, so it is kept a narrow one: a name is only matched against
its own area, the match has to be a near miss rather than a resemblance, and a
tie is refused rather than broken. Two things make that guess safe to make.
A wrong zone is visible — every report says which zone it settled on, so a
guess that went wrong reads as a wrong window rather than as a mystery. And
the alternative is not a stricter answer but no answer: a stand-up note is not
worth failing over a typo when the intended zone is unmistakable.

An unmistakable typo is the only thing guessed at. An abbreviation is not
one: ``IST`` is India, Israel and Ireland at once, and there is no honest way
to pick between them, so it comes back as the error it is.
"""

from __future__ import annotations

import difflib
import re
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from app.core.errors import UnprocessableRequestError

UTC_ZONE = ZoneInfo("UTC")

_CLOSE_ENOUGH = 0.8
"""How alike a name and a key must be for the key to be used instead.

Set where a typo lands and a different place does not: one letter wrong in
``Asia/Calcutta`` scores 0.96, while ``Asia/Kolkata`` against its neighbour
``Asia/Kathmandu`` — two real, different cities — scores 0.69.
"""

_WORTH_MENTIONING = 0.65
"""How alike it must be to be named in the error instead.

Deliberately loose, because a suggestion only has to be worth a look: nothing
is chosen on the strength of one, and a name the caller can see is wrong costs
them a glance, while the one they meant being left out costs them the trip.
"""

_SUGGESTIONS = 3

_SPACES = re.compile(r"\s+")


def zone_for(name: str | None) -> ZoneInfo:
    """The zone a caller means; UTC when the caller does not say.

    UTC as the default rather than the server's own zone: a deployment's ``TZ``
    is an accident of how the container was built, and an answer that silently
    changes shape when the image is rebuilt is worse than one that is obviously
    in UTC until a client says otherwise. A blank name is not a name, so it
    defaults the same way.

    Raises:
        UnprocessableRequestError: if no zone this server knows is close
            enough to the name to be certain of, with the nearest ones it does
            know in the message and in ``details["suggestions"]``.
    """
    if name is None or not name.strip():
        return UTC_ZONE

    tidy = _tidied(name)
    for candidate in dict.fromkeys((name, tidy)):
        opened = _opened(candidate)
        if opened is not None:
            return opened

    keyed = _keys_by_case().get(tidy.lower())
    if keyed is not None:
        return ZoneInfo(keyed)

    return _nearest_to(name, tidy)


def _tidied(name: str) -> str:
    """``name`` with what other systems wrap a zone in taken back off.

    The leading colon is ``TZ``'s: ``TZ=:Asia/Kolkata`` is POSIX for "this is a
    file to read, not a rule to parse", and it reaches us on anything that
    passes ``TZ`` along. Whitespace becomes the underscore the database uses,
    because "America/New York" is how that zone is written everywhere but in
    the database. Hyphens are left alone — ``America/Port-au-Prince`` means
    them.
    """
    return _SPACES.sub("_", name.strip().lstrip(":").strip())


def _opened(key: str) -> ZoneInfo | None:
    """The zone ``key`` names, or None if the database holds no such key."""
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        # ValueError is what a key that is not even a key raises — an absolute
        # path, or one climbing out of the database's directory.
        return None


def _nearest_to(given: str, tidy: str) -> ZoneInfo:
    """The one key close enough to ``tidy`` to have been meant, or an error."""
    ranked = _ranked(tidy)
    closest = [key for key, score in ranked if score == ranked[0][1]] if ranked else []

    if ranked and ranked[0][1] >= _CLOSE_ENOUGH:
        if len(closest) == 1:
            return ZoneInfo(closest[0])
        raise UnprocessableRequestError(
            f"{given!r} is as close to {_listed(closest)} as to each other, so "
            "this server will not choose between them. Give the full IANA name "
            "of the one you mean.",
            details={"timezone": given, "suggestions": closest},
        )

    suggestions = [key for key, _ in ranked[:_SUGGESTIONS]]
    nearest = f" The closest it has is {_listed(suggestions)}." if suggestions else ""
    raise UnprocessableRequestError(
        f"{given!r} is not a time zone this server knows. Give an IANA name, "
        f"such as 'Asia/Kolkata' or 'UTC'.{nearest}",
        details={"timezone": given, "suggestions": suggestions},
    )


def _ranked(tidy: str) -> list[tuple[str, float]]:
    """Keys worth mentioning against ``tidy``, likeliest first.

    Ranked within the name's own area when it names a real one, so a mangled
    city is only ever matched against cities on its own continent and
    ``Asia/Cacutta`` cannot come back as somewhere in America. A name whose
    area is itself wrong has nothing narrower to go on, so it is ranked against
    the whole database.
    """
    area, slash, _ = tidy.partition("/")
    keys = _keys_by_area().get(area.lower()) if slash else None
    scored = (
        (key, difflib.SequenceMatcher(None, tidy.lower(), key.lower()).ratio())
        for key in (keys if keys is not None else _all_keys())
    )
    worth = [pair for pair in scored if pair[1] >= _WORTH_MENTIONING]
    # Key as well as score, so a tie ranks by name and the pick is the same
    # every time rather than however the set iterated.
    return sorted(worth, key=lambda pair: (-pair[1], pair[0]))


def _listed(keys: list[str]) -> str:
    """``"'a', 'b' or 'c'"`` — the way a person would read a short list out."""
    quoted = [repr(key) for key in keys]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


@cache
def _all_keys() -> list[str]:
    """Every key the database holds, sorted. Cached: the database does not move."""
    return sorted(available_timezones())


@cache
def _keys_by_case() -> dict[str, str]:
    """Every key by its lowercase self, for matching without regard to case."""
    return {key.lower(): key for key in _all_keys()}


@cache
def _keys_by_area() -> dict[str, list[str]]:
    """Keys grouped by the area they are under, keyed lowercase."""
    grouped: dict[str, list[str]] = {}
    for key in _all_keys():
        area, slash, _ = key.partition("/")
        if slash:
            grouped.setdefault(area.lower(), []).append(key)
    return grouped
