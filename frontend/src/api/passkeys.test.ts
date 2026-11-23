import { describe, expect, it } from 'vitest'
import { passkeysSupported, wasCancelled } from './passkeys'

describe('passkeysSupported', () => {
  it('is false where the browser has no WebAuthn', () => {
    // vitest's environment has no PublicKeyCredential, which is what an old browser looks like.
    expect(passkeysSupported()).toBe(false)
  })
})

describe('wasCancelled', () => {
  it('treats dismissing the browser prompt as a choice', () => {
    expect(wasCancelled(new DOMException('dismissed', 'NotAllowedError'))).toBe(true)
    expect(wasCancelled(new DOMException('aborted', 'AbortError'))).toBe(true)
  })

  it('does not hide a real failure', () => {
    expect(wasCancelled(new DOMException('no such key', 'InvalidStateError'))).toBe(false)
    expect(wasCancelled(new Error('boom'))).toBe(false)
  })
})
