import { describe, expect, it } from 'vitest'
import { backoffDelay, boardSocketUrl, CLOSE, isFinal, parseEvent } from './realtime'

const at = (protocol: string, host: string) => ({ protocol, host }) as Location

describe('boardSocketUrl', () => {
  it('follows the scheme the page was served over', () => {
    expect(boardSocketUrl('ATL', at('https:', 'cylist.example'))).toBe(
      'wss://cylist.example/api/v1/projects/ATL/board/ws',
    )
    expect(boardSocketUrl('ATL', at('http:', 'localhost:5173'))).toBe(
      'ws://localhost:5173/api/v1/projects/ATL/board/ws',
    )
  })

  it('escapes a key that would otherwise change the path', () => {
    expect(boardSocketUrl('a/b', at('https:', 'x'))).toContain('/projects/a%2Fb/board/ws')
  })
})

describe('backoffDelay', () => {
  const middle = () => 0.5

  it('doubles up to a ceiling', () => {
    const flat = [0, 1, 2, 3, 4, 5, 6, 10].map((n) => backoffDelay(n, middle))
    expect(flat).toEqual([500, 1000, 2000, 4000, 8000, 15000, 15000, 15000])
  })

  it('spreads either side, so tabs do not return in lockstep', () => {
    expect(backoffDelay(0, () => 0)).toBe(400)
    expect(backoffDelay(0, () => 1)).toBe(600)
  })
})

describe('isFinal', () => {
  it('gives up only when retrying cannot help', () => {
    expect(isFinal(CLOSE.UNAUTHORIZED)).toBe(true)
    // A board that is gone may come back; a server that dropped us certainly
    // may. Neither is a reason to stop.
    expect(isFinal(CLOSE.NOT_FOUND)).toBe(false)
    expect(isFinal(1006)).toBe(false)
  })
})

describe('parseEvent', () => {
  it('reads the three messages the server sends', () => {
    expect(parseEvent('{"type":"ready"}')).toEqual({ type: 'ready' })
    expect(parseEvent('{"type":"board.changed","at":"2026-09-10T12:00:00Z"}')).toEqual({
      type: 'board.changed',
      at: '2026-09-10T12:00:00Z',
    })
    expect(parseEvent('{"type":"error","code":"unauthorized","message":"no"}')).toEqual({
      type: 'error',
      code: 'unauthorized',
      message: 'no',
    })
  })

  it('ignores anything it does not recognise rather than throwing', () => {
    // A newer server may send messages this build has never heard of, and a
    // board that crashed on one would be a board that cannot be deployed to
    // ahead of its clients.
    expect(parseEvent('{"type":"something.new"}')).toBeNull()
    expect(parseEvent('not json')).toBeNull()
    expect(parseEvent('null')).toBeNull()
    expect(parseEvent(new Blob())).toBeNull()
  })
})
