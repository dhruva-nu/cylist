import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/http'
import { handlesOwnErrors, reportFailure } from '../api/mutationErrors'
import { TOAST_MS, clearToasts, currentToasts, dismissToast, showToast } from './toasts'

const texts = () => currentToasts().map((toast) => toast.text)

beforeEach(() => {
  vi.useFakeTimers()
  clearToasts()
})

afterEach(() => {
  clearToasts()
  vi.useRealTimers()
})

describe('showToast', () => {
  it('shows a message, and takes it away after a while', () => {
    showToast('Could not save.')
    expect(texts()).toEqual(['Could not save.'])

    vi.advanceTimersByTime(TOAST_MS)

    expect(texts()).toEqual([])
  })

  it('does not stack the same words twice', () => {
    const first = showToast('Could not save.')
    const second = showToast('Could not save.')

    expect(second).toBe(first)
    expect(texts()).toEqual(['Could not save.'])
  })

  it('keeps the newest few, dropping the oldest', () => {
    for (const text of ['a', 'b', 'c', 'd', 'e']) showToast(text)

    expect(texts()).toEqual(['b', 'c', 'd', 'e'])
  })

  it('can be dismissed before it expires', () => {
    const id = showToast('Could not save.')

    dismissToast(id)

    expect(texts()).toEqual([])
  })
})

describe('reportFailure', () => {
  const failure = new ApiError(409, 'conflict', 'That name is taken.')
  const plain = { meta: undefined, options: {} }

  it('says what went wrong, in the server’s words, when nobody else will', () => {
    reportFailure(failure, plain)

    expect(texts()).toEqual(['That name is taken.'])
  })

  it('stays quiet for a write that reports its own failure', () => {
    reportFailure(failure, { meta: handlesOwnErrors, options: {} })

    expect(texts()).toEqual([])
  })

  it('stays quiet for a write with an onError of its own', () => {
    reportFailure(failure, { meta: undefined, options: { onError: () => undefined } })

    expect(texts()).toEqual([])
  })

  it('stays quiet for a lost session, which the sign-in screen answers', () => {
    reportFailure(new ApiError(401, 'unauthorized', 'Sign in.'), plain)

    expect(texts()).toEqual([])
  })
})
