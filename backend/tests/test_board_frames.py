"""The frames a board socket sends, held against what the web app parses.

``frontend/src/api/fixtures/board-frames.json`` is the contract in the one
place both sides read: this test fails when the server's frames stop matching
it, and ``frontend/src/api/realtime.test.ts`` fails when the client stops
understanding it. WebSocket routes are not in the OpenAPI document, so nothing
else ties the two together.

To change a frame, change it here and in the fixture together; the failure
prints the fixture the server would now write.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.realtime import protocol

FIXTURE = Path(__file__).parents[2] / "frontend" / "src" / "api" / "fixtures" / "board-frames.json"
AT = "2026-10-22T10:00:00+00:00"


def the_frames() -> dict[str, dict[str, Any]]:
    """Every frame a board socket can send, as the server builds it, with a fixed time."""
    frames: dict[str, dict[str, Any]] = {
        # `routes.board_socket` sends this literal.
        "ready": {"type": "ready"},
        "changed, naming a card": protocol.board_changed(
            [{"entity_type": "task", "entity_id": "4f1c2a9e-0000-7000-8000-000000000001"}]
        ),
        "changed, naming nothing": protocol.board_changed(None),
        "changed, naming two things": protocol.merge_changed(
            protocol.board_changed([{"entity_type": "task", "entity_id": "a"}]),
            protocol.board_changed([{"entity_type": "goal", "entity_id": "b"}]),
        ),
        "agent feed": protocol.feed_changed("4f1c2a9e-0000-7000-8000-000000000001"),
        "error": protocol.error("not_found", "No such project."),
    }
    return {
        name: {**frame, "at": AT} if "at" in frame else dict(frame)
        for name, frame in frames.items()
    }


def test_the_frames_match_the_fixture_the_web_app_reads() -> None:
    expected = json.loads(FIXTURE.read_text())

    assert the_frames() == expected, (
        "A board frame changed. The fixture the server would now write:\n"
        + json.dumps(the_frames(), indent=2)
    )
