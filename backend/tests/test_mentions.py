"""Who a comment tags: the server's reading of ``@Name`` has to be the board's."""

from __future__ import annotations

from uuid import uuid4

from app.models.person import Person
from app.services.mentions import tagged_in


def _person(name: str) -> Person:
    return Person(id=uuid4(), name=name)


ADITI = _person("Aditi")
ADITI_K = _person("Aditi K")
RAVI = _person("Ravi")
MEMBERS = [ADITI, ADITI_K, RAVI]


def _names(text: str) -> list[str]:
    return [person.name for person in tagged_in(text, MEMBERS)]


def test_text_without_a_tag_tags_nobody() -> None:
    assert _names("") == []
    assert _names("Ship it on Friday.") == []


def test_the_longest_name_wins() -> None:
    assert _names("@Aditi K, can you look?") == ["Aditi K"]
    assert _names("@Aditi, can you look?") == ["Aditi"]


def test_a_name_that_runs_on_is_nobody() -> None:
    assert [person.name for person in tagged_in("@Aditi Kumar", [ADITI_K])] == []
    assert _names("@Ravindra") == []


def test_a_shorter_name_still_counts_when_the_longer_one_runs_on() -> None:
    assert _names("@Aditi Kumar") == ["Aditi"]


def test_an_address_is_not_a_tag() -> None:
    assert _names("mail ravi@Ravi.com") == []


def test_case_does_not_matter_and_each_person_counts_once() -> None:
    assert _names("@ravi then @Aditi K then @RAVI") == ["Ravi", "Aditi K"]


def test_a_tag_may_open_the_text_or_follow_punctuation() -> None:
    assert _names("(@Ravi) and @Aditi.") == ["Ravi", "Aditi"]


def test_only_members_can_be_tagged() -> None:
    assert _names("@Meera please") == []
