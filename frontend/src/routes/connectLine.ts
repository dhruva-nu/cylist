/**
 * The one line that connects Claude Code on another machine to Cylist's MCP
 * tools, served at `/mcp` by the same process as the web app.
 */

import type { Scope, SetupInfo } from '../api/client'

/** What Claude Code lists the server as — the same name `cylist setup` uses. */
export const MCP_NAME = 'cylist'

/**
 * The `claude mcp add` line for a token.
 *
 * `--header` goes last because it takes any number of values: anywhere
 * earlier, it would swallow the name and the address as more headers. Double
 * quotes because they mean the same thing in PowerShell, cmd and a POSIX
 * shell, and a token has nothing in it any of them would expand. `--scope
 * user` so the tools are there in every project on that machine, not only
 * the directory the line happened to be run in.
 */
export function connectLine(origin: string, token: string): string {
  return (
    `claude mcp add --transport http --scope user ${MCP_NAME} ${origin}/mcp ` +
    `--header "Authorization: Bearer ${token}"`
  )
}

/** What a connection line's token holds when the server has not said: the board. */
const FALLBACK_SCOPES: Scope[] = ['read', 'write']

const KNOWN_SCOPES: readonly Scope[] = ['read', 'write', 'vault:read', 'vault:reveal', 'admin']

/**
 * The scopes to mint a connection line's token with — the server's own answer.
 *
 * `GET /setup` names what an agent is given (`agent_scopes`), so that widening
 * it is a change in one place on the server, the same one `cylist setup` reads.
 * Anything this build does not recognise is dropped rather than sent, and
 * `admin` is never handed to an agent whatever the server says.
 */
export function agentScopes(setup: Pick<SetupInfo, 'agent_scopes'> | undefined): Scope[] {
  const named = (setup?.agent_scopes ?? []).filter(
    (scope): scope is Scope => KNOWN_SCOPES.includes(scope as Scope) && scope !== 'admin',
  )
  return named.length > 0 ? named : FALLBACK_SCOPES
}
