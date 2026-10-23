/**
 * The one way this client talks to the server.
 *
 * Every failure the server produces arrives in one envelope:
 *   { "error": { "code": "...", "reason": "..." | null, "message": "...", "details": {} } }
 * so `request` turns any non-2xx response into an `ApiError` carrying that
 * code. Callers branch on `error.code`, never on a status number — and, to tell
 * one kind of conflict from another, on `error.reason`, never on the message.
 */

import { withBase } from './basePath'

// Under the deployment's base path — `/dev_1/api/v1` in a dev slot.
export const API_BASE = withBase('/api/v1')

/** A failure the server described in its error envelope. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly reason: string | null = null,
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
  error?: {
    code?: string
    reason?: string | null
    message?: string
    details?: Record<string, unknown>
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: 'same-origin', // carries the session cookie
      headers: {
        Accept: 'application/json',
        // FormData carries its own multipart content type, boundary included;
        // setting one here would produce a body the server cannot parse.
        ...(init.body && !(init.body instanceof FormData)
          ? { 'Content-Type': 'application/json' }
          : {}),
        ...init.headers,
      },
    })
  } catch {
    throw new ApiError(0, 'network_error', 'Cannot reach the Cylist API. Is it running?')
  }

  if (response.status === 204) {
    return undefined as T
  }

  const responseBody: unknown = await response.json().catch(() => null)

  if (!response.ok) {
    const envelope = (responseBody ?? {}) as ErrorEnvelope
    throw new ApiError(
      response.status,
      envelope.error?.code ?? 'unknown_error',
      envelope.error?.message ?? 'Something went wrong.',
      envelope.error?.details ?? {},
      envelope.error?.reason ?? null,
    )
  }

  return responseBody as T
}

/** A request body as the server takes it. */
export const jsonBody = (value: unknown) => JSON.stringify(value)

/**
 * What to tell the reader about something that went wrong.
 *
 * Any `Error` carries a sentence worth showing — an `ApiError` the server's
 * own — so it is shown as it is. Anything else thrown is not something a
 * person can act on, and gets the caller's fallback instead.
 */
export function errorMessage(caught: unknown, fallback = 'Something went wrong.'): string {
  return caught instanceof Error ? caught.message : fallback
}
