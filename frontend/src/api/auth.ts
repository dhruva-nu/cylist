/** Signing in and out, and who this session is. */

import { jsonBody, request } from './http'
import type { Person } from './people'

/** `permit` answers an agent's permission prompt; the phone app's token has it, an agent's never does. */
export type Scope = 'read' | 'write' | 'vault:read' | 'vault:reveal' | 'admin' | 'permit'

/**
 * The server's password floor, repeated so a form can say so before the trip.
 *
 * Length rather than character classes, which is what both OWASP and NIST now
 * recommend — the rules that ask for a symbol mostly produce `Password1!`.
 * Kept in step with `MIN_PASSWORD_LENGTH` in `app/schemas/people.py`; the
 * server is what enforces it, and this only saves a round trip.
 */
export const MIN_PASSWORD_LENGTH = 12

export interface Identity {
  token_id: string
  label: string
  channel: 'web' | 'api'
  scopes: Scope[]
  /**
   * Who this session is. Null only on a deployment with no accounts yet,
   * where you are signed in with the bootstrap password and the first thing
   * to do is open an account.
   */
  person: Person | null
}

/**
 * A freshly minted API token. `token` is the plaintext, and this is the only
 * response that will ever carry it — see `TokenIssued` in
 * `app/schemas/tokens.py`.
 */
export interface TokenIssued {
  id: string
  name: string
  scopes: string[]
  created_at: string
  expires_at: string | null
  token: string
}

export interface SetupInfo {
  urls: string[]
  environment: string
  agent_scopes: string[]
  /** False while the deployment is still on its bootstrap password. */
  has_accounts: boolean
}

export interface InviteIssued {
  person: Person
  /** Shown once. Cylist sends no email, so this is yours to pass on. */
  token: string
  url: string
  expires_at: string
}

export const authApi = {
  /** What this deployment is, and whether anybody can sign in to it yet. */
  setup: (signal?: AbortSignal) => request<SetupInfo>('/setup', { signal }),

  signIn: (email: string, password: string) =>
    request<Identity>('/auth/login', { method: 'POST', body: jsonBody({ email, password }) }),
  /**
   * The one login that belongs to nobody, for a deployment with no accounts.
   * Refused the moment the first account exists, so the sign-in screen offers
   * it only while `setup().has_accounts` is false.
   */
  bootstrapSignIn: (password: string) =>
    request<Identity>('/auth/login', { method: 'POST', body: jsonBody({ password }) }),
  me: (signal?: AbortSignal) => request<Identity>('/me', { signal }),
  /** Revoke this browser's session and clear its cookie. */
  signOut: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),

  /** Turn an invitation link into an account, and sign in with it. */
  acceptInvite: (token: string, password: string) =>
    request<Identity>('/auth/accept-invite', {
      method: 'POST',
      body: jsonBody({ token, password }),
    }),
  createToken: (name: string, scopes: Scope[]) =>
    request<TokenIssued>('/tokens', { method: 'POST', body: jsonBody({ name, scopes }) }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ ok: boolean }>('/auth/password', {
      method: 'POST',
      body: jsonBody({ current_password: currentPassword, new_password: newPassword }),
    }),
}
