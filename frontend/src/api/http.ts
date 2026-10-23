/**
 * The one way this client talks to the server.
 *
 * Every failure the server produces arrives in one envelope:
 *   { "error": { "code": "...", "message": "...", "details": {} } }
 * so `request` turns any non-2xx response into an `ApiError` carrying that
 * code. Callers branch on `error.code`, never on a status number.
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

/**
 * `RequestInit` where the signal may be absent *or* undefined, so a caller can
 * hand on the optional `signal` a query gives it without first checking it.
 */
export type RequestOptions = Omit<RequestInit, 'signal'> & { signal?: AbortSignal | undefined }

export async function request<T>(path: string, init: RequestOptions = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: init.signal ?? null,
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

  const text = await response.text().catch(() => '')
  const responseBody: unknown = parsed(text)

  if (!response.ok) {
    const envelope = (responseBody ?? {}) as ErrorEnvelope
    throw new ApiError(
      response.status,
      envelope.error?.code ?? 'unknown_error',
      envelope.error?.message ?? 'Something went wrong.',
      envelope.error?.details ?? {},
    )
  }

  if (text === '') return undefined as T
  if (responseBody === undefined) {
    // Handing a caller `null` where it expects a list or a card is a crash
    // somewhere else, later, with no hint of which request went wrong.
    throw new ApiError(
      response.status,
      'invalid_response',
      'The server answered with something this app could not read.',
    )
  }
  return responseBody as T
}

/** The JSON in `text`, or `undefined` if it is not JSON — an empty body included. */
function parsed(text: string): unknown {
  try {
    return JSON.parse(text) as unknown
  } catch {
    return undefined
  }
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
