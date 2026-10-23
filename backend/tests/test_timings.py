"""The clocks that live in Settings, and the ways to read them."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.config import Settings, settings_of
from app.main import create_app


class TestTimings:
    def test_defaults_are_what_the_loops_always_ran_on(self) -> None:
        settings = Settings()

        assert settings.agent_socket_idle_after == timedelta(minutes=5)
        assert settings.agent_socket_stamp_every == timedelta(seconds=60)
        assert settings.agent_quiet_after == timedelta(minutes=10)
        assert settings.agent_reap_every == timedelta(seconds=60)
        assert settings.invite_ttl == timedelta(days=7)
        assert settings.blob_sweep_idle_for == timedelta(hours=1)
        assert settings.blob_sweep_every == timedelta(hours=1)

    def test_a_stamp_vouches_for_the_idle_window_plus_one_stamp_interval(self) -> None:
        settings = Settings(agent_socket_idle_seconds=100, agent_socket_stamp_seconds=7)

        assert settings.agent_socket_witnesses_for == timedelta(seconds=107)

    def test_an_environment_variable_sets_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CYLIST_INVITE_TTL_DAYS", "2")

        assert Settings().invite_ttl == timedelta(days=2)

    @pytest.mark.parametrize(
        "field", ["agent_socket_idle_seconds", "agent_quiet_seconds", "invite_ttl_days"]
    )
    def test_a_clock_that_never_runs_is_refused(self, field: str) -> None:
        with pytest.raises(ValueError, match=field):
            Settings(**{field: 0})


class TestReachingSettings:
    def test_a_connection_finds_the_settings_its_app_was_built_with(
        self, settings: Settings
    ) -> None:
        app = create_app(settings)

        class Connection:
            pass

        connection = Connection()
        connection.app = app  # type: ignore[attr-defined]

        assert settings_of(connection) is settings  # type: ignore[arg-type]
