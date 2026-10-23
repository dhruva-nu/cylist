import { describe, expect, it } from 'vitest'
import { applyInert, type Inertable } from './modalInert'

const element = (): Inertable => ({ inert: false })

describe('applyInert', () => {
  it('leaves the app alone while no dialog is open', () => {
    const app = element()
    app.inert = true

    applyInert(app, [])

    expect(app.inert).toBe(false)
  })

  it('makes the app inert behind one dialog, and the dialog not', () => {
    const app = element()
    const dialog = element()

    applyInert(app, [dialog])

    expect(app.inert).toBe(true)
    expect(dialog.inert).toBe(false)
  })

  it('makes every dialog but the innermost inert', () => {
    const [outer, middle, inner] = [element(), element(), element()]

    applyInert(element(), [outer, middle, inner])

    expect([outer.inert, middle.inert, inner.inert]).toEqual([true, true, false])
  })

  it('wakes the one beneath when the innermost closes', () => {
    const [outer, inner] = [element(), element()]
    applyInert(element(), [outer, inner])

    applyInert(element(), [outer])

    expect(outer.inert).toBe(false)
  })

  it('copes with a page that is not there', () => {
    const dialog = element()

    applyInert(null, [dialog])

    expect(dialog.inert).toBe(false)
  })
})
