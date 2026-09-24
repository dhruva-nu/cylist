/**
 * The one line that connects Claude Code on another machine to Cylist's MCP
 * tools, served at `/mcp` by the same process as the web app.
 */

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
