/**
 * Typed access to the Cylist API.
 *
 * Every failure the server produces arrives in one envelope:
 *   { "error": { "code": "...", "message": "...", "details": {} } }
 * so `request` turns any non-2xx response into an `ApiError` carrying that
 * code. Callers branch on `error.code`, never on a status number.
 *
 * Once the backend is running, regenerate exact response types with
 * `npm run api:types`; the hand-written types below cover Phase 0 only.
 */

const API_BASE = '/api/v1'

export type Scope = 'read' | 'write' | 'vault:read' | 'vault:reveal' | 'admin'

export interface Identity {
  token_id: string
  label: string
  channel: 'web' | 'api'
  scopes: Scope[]
}

export interface ApiToken {
  id: string
  name: string
  scopes: Scope[]
  created_at: string
  expires_at: string | null
  last_used_at: string | null
}

export interface IssuedToken extends ApiToken {
  /** The only copy of the plaintext. Shown once, never retrievable again. */
  token: string
}

export interface Health {
  status: 'ok' | 'degraded'
  database: 'up' | 'down'
}

/** A failure the server described in its error envelope. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** True when the caller is not signed in, or the credential was revoked. */
  get isUnauthenticated(): boolean {
    return this.code === 'unauthorized'
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; details?: Record<string, unknown> }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: 'same-origin', // carries the session cookie
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers,
      },
    })
  } catch {
    throw new ApiError(0, 'network_error', 'Cannot reach the Cylist API. Is it running?')
  }

  if (response.status === 204) {
    return undefined as T
  }

  const body: unknown = await response.json().catch(() => null)

  if (!response.ok) {
    const envelope = (body ?? {}) as ErrorEnvelope
    throw new ApiError(
      response.status,
      envelope.error?.code ?? 'unknown_error',
      envelope.error?.message ?? 'Something went wrong.',
      envelope.error?.details ?? {},
    )
  }

  return body as T
}

export const api = {
  health: () => request<Health>('/health'),

  signIn: (password: string) =>
    request<Identity>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),

  signOut: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),

  me: () => request<Identity>('/me'),

  listTokens: () => request<ApiToken[]>('/tokens'),

  issueToken: (input: { name: string; scopes: Scope[]; expires_in_days?: number }) =>
    request<IssuedToken>('/tokens', { method: 'POST', body: JSON.stringify(input) }),

  revokeToken: (id: string) => request<{ ok: boolean }>(`/tokens/${id}`, { method: 'DELETE' }),
}
