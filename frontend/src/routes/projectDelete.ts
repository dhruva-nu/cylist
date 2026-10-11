/**
 * The rule guarding the one button on this screen that nobody can undo.
 *
 * Kept out of the TSX and tested on its own, the way `projectRoles.ts` keeps
 * the questions the roles screen asks. The server checks the same thing — see
 * `delete_project` in `app/routers/projects.py` — and is what actually
 * refuses; this is what lets the dialog keep its button disabled rather than
 * letting somebody press it and be told no.
 */

/**
 * Whether what has been typed names this project.
 *
 * Case and surrounding space are forgiven for the same reason the server
 * forgives them: somebody who typed `atl ` has said which project they mean,
 * and a confirmation that reads as a trick is one people learn to paste past.
 * Nothing else is — a project is deleted by naming it, not by naming one like
 * it.
 */
export function confirms(projectKey: string, typed: string): boolean {
  return typed.trim().toLocaleUpperCase() === projectKey.toLocaleUpperCase()
}
