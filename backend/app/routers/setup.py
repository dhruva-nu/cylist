"""What a client needs to know before it has a token.

One endpoint, and it answers one question: *where else can I reach you?* A
laptop that configured itself over the tailnet stops being able to resolve
that name the moment it leaves; a laptop configured against the public
hostname pays a detour through the funnel while sitting on the same LAN. Both
are the same misconfiguration — a client that was told one address for a
server that has several.

So the server hands over the whole list and the client keeps all of it,
trying them in order until one answers. Nobody reconfigures anything when the
network changes, which is the entire point.

Unauthenticated, for the reason ``/health`` is: this is what a client reads
*before* it has a credential, and there is nothing here that is not already
public. Every address in the list is a hostname somebody has published in DNS
on purpose, and the endpoint says nothing about what is behind them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import Environment, Settings, app_settings
from app.schemas.common import Schema

router = APIRouter(tags=["setup"])

AGENT_SCOPES = ["read", "write"]
"""What an agent that runs a board needs, and the most it should be given.

Returned so that whatever mints a token for one does not have to hold its own
opinion about this, and so that widening it is a change in one place.
"""


class SetupInfo(Schema):
    """Where this server can be reached, and what to mint for an agent."""

    urls: list[str]
    """Every address this deployment answers on, most local first.

    Advisory: the caller has just reached one address that is not necessarily
    in here — a LAN address, a port-forward — and should keep that one too,
    ahead of these. Empty when nobody has configured
    ``CYLIST_CLIENT_URLS``, which is the correct answer for a server that is
    only ever reached one way.
    """

    environment: Environment
    agent_scopes: list[str]


@router.get("/setup", response_model=SetupInfo, summary="How to reach this server")
async def setup(settings: Settings = Depends(app_settings)) -> SetupInfo:
    """Describe this deployment to a client that is configuring itself."""
    return SetupInfo(
        urls=_normalise(settings.client_urls),
        environment=settings.environment,
        agent_scopes=AGENT_SCOPES,
    )


def _normalise(urls: list[str]) -> list[str]:
    """Trim, drop blanks and duplicates, and keep the configured order.

    Order is the whole value of the list — it is the order a client will try
    them in — so this deduplicates by first appearance rather than sorting.
    """
    seen: dict[str, None] = {}
    for url in urls:
        cleaned = url.strip().rstrip("/")
        if cleaned:
            seen.setdefault(cleaned, None)
    return list(seen)
