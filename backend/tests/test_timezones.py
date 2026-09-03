"""Reading the zone a caller meant out of the name they gave.

The names in here are the ones that actually arrive: a browser's own key for
India, a shell's ``TZ`` with POSIX's leading colon, a city written with the
space it is written with everywhere but in the tz database, and a name a letter
short of a real one. Each has to come back as a zone; what must not happen is a
name that could mean two places quietly coming back as one of them.
"""

from __future__ import annotations

from importlib.util import find_spec

import pytest

from app.core.errors import UnprocessableRequestError
from app.core.timezones import UTC_ZONE, zone_for


def test_a_name_the_database_holds_is_used_as_given() -> None:
    assert str(zone_for("Asia/Kolkata")) == "Asia/Kolkata"


def test_the_whole_database_is_carried_not_borrowed() -> None:
    """The zones a deployment knows must not depend on its base image.

    A developer's machine has the full database in ``/usr/share/zoneinfo``, so
    the links below resolve there whether or not anything is declared — which
    is exactly how they came to 422 in a container and not on a laptop. This
    asserts the dependency itself, where no system database can stand in for
    it.
    """
    assert find_spec("tzdata") is not None, "the backend must depend on tzdata"


@pytest.mark.parametrize("link", ["Asia/Calcutta", "US/Pacific", "Asia/Macao", "Hongkong", "Zulu"])
def test_a_compatibility_link_is_a_name_the_database_holds(link: str) -> None:
    """The old names are names, because clients still send them.

    ``Asia/Calcutta`` is what a good many browsers report for India. Every name
    here exists only as a link in the tz database's ``backward`` file, and
    `python:3.12-slim` — what the image is built on — ships the canonical zones
    without them. These pass because the backend depends on ``tzdata`` and
    carries the whole database itself; drop that dependency and this is the
    test that says so.
    """
    assert str(zone_for(link)) == link


def test_no_zone_asked_for_means_utc() -> None:
    assert zone_for(None) is UTC_ZONE


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_zone_is_no_zone_asked_for(blank: str) -> None:
    assert zone_for(blank) is UTC_ZONE


@pytest.mark.parametrize(
    ("given", "meant"),
    [
        ("asia/kolkata", "Asia/Kolkata"),
        ("ASIA/KOLKATA", "Asia/Kolkata"),
        ("  Asia/Kolkata  ", "Asia/Kolkata"),
        (":Asia/Kolkata", "Asia/Kolkata"),  # what `TZ=:Asia/Kolkata` passes on
        ("America/New York", "America/New_York"),
        ("america/new york", "America/New_York"),
    ],
)
def test_a_name_spelled_loosely_is_read_as_what_it_spells(given: str, meant: str) -> None:
    assert str(zone_for(given)) == meant


@pytest.mark.parametrize(
    ("typo", "meant"),
    [
        ("Asia/Cacutta", "Asia/Calcutta"),  # the report this was raised about
        ("Asia/Kolkatta", "Asia/Kolkata"),
        ("Asia/Tokio", "Asia/Tokyo"),
        ("Europe/Berlim", "Europe/Berlin"),
        ("Australia/Sidney", "Australia/Sydney"),
    ],
)
def test_a_typo_with_one_obvious_reading_is_read_that_way(typo: str, meant: str) -> None:
    assert str(zone_for(typo)) == meant


def test_a_hyphen_is_part_of_the_name_not_a_stand_in_for_an_underscore() -> None:
    """``America/Port-au-Prince`` means its hyphens; nothing may eat them."""
    assert str(zone_for("America/Port-au-Prince")) == "America/Port-au-Prince"


def test_a_typo_is_only_matched_against_its_own_area() -> None:
    """Cities repeat across continents, so a mangled one stays on its own.

    ``Asia/Cordoba`` is not a zone, and ``America/Cordoba`` is — but a caller
    who said Asia did not mean Argentina.
    """
    with pytest.raises(UnprocessableRequestError) as raised:
        zone_for("Asia/Cordoba")

    assert "America/Cordoba" not in str(raised.value.message)


def test_a_name_as_close_to_two_zones_as_to_each_other_is_refused() -> None:
    """A tie is named, not broken: the caller knows which they meant."""
    with pytest.raises(UnprocessableRequestError) as raised:
        zone_for("Asia/Macaz")

    assert raised.value.details["suggestions"] == ["Asia/Macao", "Asia/Macau"]
    assert "Asia/Macao" in raised.value.message
    assert "Asia/Macau" in raised.value.message


def test_a_resemblance_is_not_a_typo() -> None:
    """Two real cities that read alike are two cities, not one misspelt."""
    assert str(zone_for("Asia/Kathmandu")) == "Asia/Kathmandu"
    assert str(zone_for("Asia/Kolkata")) == "Asia/Kolkata"


def test_an_abbreviation_is_ambiguous_and_says_so() -> None:
    """IST is India, Israel and Ireland; guessing would be a coin toss."""
    with pytest.raises(UnprocessableRequestError) as raised:
        zone_for("IST")

    assert "IANA" in raised.value.message


def test_a_name_that_is_no_zone_at_all_is_a_clean_error() -> None:
    with pytest.raises(UnprocessableRequestError) as raised:
        zone_for("Mars/Olympus_Mons")

    assert raised.value.status_code == 422
    assert raised.value.details["timezone"] == "Mars/Olympus_Mons"


def test_a_city_without_its_area_is_suggested_rather_than_assumed() -> None:
    """ "Kolkata" is most of a name, but the area is the caller's to say."""
    with pytest.raises(UnprocessableRequestError) as raised:
        zone_for("Kolkata")

    assert raised.value.details["suggestions"] == ["Asia/Kolkata"]


@pytest.mark.parametrize("path", ["/etc/localtime", "../../etc/passwd", "Asia/../Asia/Kolkata"])
def test_a_path_is_not_a_zone_name(path: str) -> None:
    """The keys zoneinfo refuses outright fail as cleanly as an unknown city."""
    with pytest.raises(UnprocessableRequestError):
        zone_for(path)
