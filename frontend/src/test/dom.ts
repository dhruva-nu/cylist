/**
 * What a component test needs of a browser that jsdom does not have.
 *
 * Imported by the tests that run under jsdom, and by nothing else.
 */

import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

beforeEach(() => {
  // jsdom lays nothing out, so every element answers "I take up no space" and
  // anything that asks (the dialog's focus trap skips what is not laid out)
  // finds nothing to focus. One box is enough to say "I am here".
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(
    () => [{}] as unknown as DOMRectList,
  )
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

/** A `WebSocket` that does what it is told by the test, and nothing else. */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static readonly OPEN = 1
  static readonly CLOSED = 3

  readonly url: string
  readyState = 0
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  closed = false

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  /** The server says something. */
  receive(frame: unknown): void {
    this.readyState = FakeWebSocket.OPEN
    this.onmessage?.({ data: JSON.stringify(frame) })
  }

  /** The server hangs up, or the line drops. */
  drop(code = 1006): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code })
  }

  close(): void {
    this.closed = true
    this.readyState = FakeWebSocket.CLOSED
  }

  static install(): void {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  }

  static get latest(): FakeWebSocket {
    const socket = FakeWebSocket.instances.at(-1)
    if (!socket) throw new Error('No socket has been opened.')
    return socket
  }
}
