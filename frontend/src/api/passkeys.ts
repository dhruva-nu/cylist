/**
 * Passkeys: signing in with a fingerprint, face or PIN, and recovery without a
 * password (CYLIST-204).
 *
 * The browser does the cryptography. The server hands over WebAuthn options as
 * JSON, `PublicKeyCredential.parse…FromJSON` turns them into what
 * `navigator.credentials` wants, and `credential.toJSON()` turns the answer
 * back into something to POST — so there is no base64 here to get wrong.
 */

import { jsonBody, request } from './http'
import type { Identity } from './auth'

export interface Passkey {
  id: string
  name: string
  transports: string[]
  created_at: string
  last_used_at: string | null
}

interface CeremonyOptions {
  challenge_id: string
  options: Record<string, unknown>
}

export interface PasskeyRegistered {
  passkey: Passkey
  passkey_count: number
  /** True until there are two: one lost phone must not be the end of the account. */
  recommend_another: boolean
}

export interface RecoveryStarted {
  request_id: string
  /** Shown on the new device; a signed-in one types it. */
  code: string
  /** Kept on this device and never displayed. */
  secret: string
  expires_at: string
}

export interface RecoveryState {
  state: 'pending' | 'signed_in'
  identity: Identity | null
}

/** Whether this browser can do passkeys at all (it needs the JSON helpers). */
export function passkeysSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof PublicKeyCredential !== 'undefined' &&
    'parseCreationOptionsFromJSON' in PublicKeyCredential &&
    'parseRequestOptionsFromJSON' in PublicKeyCredential &&
    typeof navigator.credentials?.create === 'function'
  )
}

/**
 * True when the person dismissed the browser's own prompt rather than the
 * ceremony failing — cancelling is not an error worth showing.
 */
export function wasCancelled(error: unknown): boolean {
  return (
    error instanceof DOMException &&
    (error.name === 'NotAllowedError' || error.name === 'AbortError')
  )
}

export const passkeysApi = {
  passkeys: (signal?: AbortSignal) => request<Passkey[]>('/me/passkeys', { signal }),

  /** Make a passkey on this device and keep its public key on the server. */
  async addPasskey(name: string): Promise<PasskeyRegistered> {
    const started = await request<CeremonyOptions>('/auth/passkeys/register/options', {
      method: 'POST',
    })
    const created = await navigator.credentials.create({
      publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(
        started.options as unknown as PublicKeyCredentialCreationOptionsJSON,
      ),
    })
    if (!(created instanceof PublicKeyCredential)) throw new Error('No passkey was made.')
    return request<PasskeyRegistered>('/auth/passkeys/register/verify', {
      method: 'POST',
      body: jsonBody({
        challenge_id: started.challenge_id,
        credential: created.toJSON() as unknown,
        name,
      }),
    })
  },

  renamePasskey: (id: string, name: string) =>
    request<Passkey>(`/me/passkeys/${id}`, { method: 'PATCH', body: jsonBody({ name }) }),
  removePasskey: (id: string) =>
    request<{ ok: boolean }>(`/me/passkeys/${id}`, { method: 'DELETE' }),

  /** Sign in with whichever passkey this device offers for the site. */
  async signInWithPasskey(): Promise<Identity> {
    const started = await request<CeremonyOptions>('/auth/passkeys/login/options', {
      method: 'POST',
    })
    const got = await navigator.credentials.get({
      publicKey: PublicKeyCredential.parseRequestOptionsFromJSON(
        started.options as unknown as PublicKeyCredentialRequestOptionsJSON,
      ),
    })
    if (!(got instanceof PublicKeyCredential)) throw new Error('No passkey was offered.')
    return request<Identity>('/auth/passkeys/login/verify', {
      method: 'POST',
      body: jsonBody({ challenge_id: started.challenge_id, credential: got.toJSON() as unknown }),
    })
  },

  /** A new device asks to be let in. Works for any address, so it reveals nothing. */
  startRecovery: (email: string) =>
    request<RecoveryStarted>('/auth/recovery/start', {
      method: 'POST',
      body: jsonBody({ email }),
    }),
  /** Poll with the secret; signed in once a device that still is has approved. */
  claimRecovery: (requestId: string, secret: string) =>
    request<RecoveryState>(`/auth/recovery/${requestId}/claim`, {
      method: 'POST',
      body: jsonBody({ secret }),
    }),
  /** From a signed-in device: let the device showing this code in. */
  approveRecovery: (code: string) =>
    request<{ ok: boolean }>('/auth/recovery/approve', {
      method: 'POST',
      body: jsonBody({ code }),
    }),
  /** Spend a code the server's operator made from the command line. */
  redeemOperatorCode: (code: string) =>
    request<Identity>('/auth/recovery/redeem', { method: 'POST', body: jsonBody({ code }) }),
}
