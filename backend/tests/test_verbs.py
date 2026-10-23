"""The vocabulary of the audit trail."""

from __future__ import annotations

import pytest

from app.models.activity import Activity, Channel, EntityType, Verb
from app.services.activity import wording
from app.services.hooks import EVENTS


def _entry(verb: Verb) -> Activity:
    return Activity(
        actor_label="Aditi K",
        channel=Channel.WEB,
        verb=verb,
        entity_type=EntityType.TASK,
        payload={},
    )


class TestVerbs:
    @pytest.mark.parametrize("verb", list(Verb))
    def test_every_verb_has_a_sentence_of_its_own(self, verb: Verb) -> None:
        """A verb nobody worded would read as its last word — "Deleted." — in a history."""
        assert wording.is_worded(verb)
        assert wording.describe(_entry(verb))

    def test_a_verb_is_its_member_name(self) -> None:
        """Written once and spelt the same way everywhere, so nothing is looked up by guess."""
        assert all(verb.name == verb.value.upper().replace(".", "_") for verb in Verb)

    def test_the_hook_catalogue_offers_only_verbs_the_trail_records(self) -> None:
        assert set(EVENTS) <= {verb.value for verb in Verb} | {"hook.test"}
