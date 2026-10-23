// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Modal } from './Modal'

import { mountPage } from '../test/dom'

function show(ui: React.ReactElement) {
  return render(ui, { container: mountPage().app })
}

function Opener({ onClosed = () => undefined }: { onClosed?: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)}>Open it</button>
      {open ? (
        <Modal
          title="Rename the column"
          onClose={() => {
            setOpen(false)
            onClosed()
          }}
          footer={<button>Save</button>}
        >
          <input aria-label="Name" />
          <button>Reset</button>
        </Modal>
      ) : null}
    </>
  )
}

describe('Modal', () => {
  it('is a modal dialog named by its title', async () => {
    show(<Opener />)
    await userEvent.click(screen.getByText('Open it'))

    const dialog = screen.getByRole('dialog', { name: 'Rename the column' })

    expect(dialog.getAttribute('aria-modal')).toBe('true')
  })

  it('is named by its heading, not by a copy of it', async () => {
    show(<Opener />)
    await userEvent.click(screen.getByText('Open it'))

    expect(screen.getByRole('dialog').getAttribute('aria-labelledby')).toBe(
      screen.getByRole('heading', { name: 'Rename the column' }).id,
    )
  })

  it('makes the app inert behind it, and awake again once it closes', async () => {
    show(<Opener />)
    const app = document.getElementById('root') as HTMLElement
    await userEvent.click(screen.getByText('Open it'))
    expect(app.inert).toBe(true)

    await userEvent.keyboard('{Escape}')

    expect(app.inert).toBe(false)
  })

  it('draws the dialog outside the app, so the app going inert does not take it too', async () => {
    show(<Opener />)
    await userEvent.click(screen.getByText('Open it'))

    expect(document.getElementById('overlay')?.contains(screen.getByRole('dialog'))).toBe(true)
    expect(document.getElementById('root')?.contains(screen.getByRole('dialog'))).toBe(false)
  })

  it('moves focus into the dialog when it opens', async () => {
    show(<Opener />)
    await userEvent.click(screen.getByText('Open it'))

    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
  })

  it('keeps Tab inside, wrapping from the last control to the first and back', async () => {
    show(<Opener />)
    await userEvent.click(screen.getByText('Open it'))
    const dialog = screen.getByRole('dialog')
    const stops = [...dialog.querySelectorAll<HTMLElement>('input, button')]
    const first = stops[0] as HTMLElement
    const last = stops.at(-1) as HTMLElement

    last.focus()
    await userEvent.tab()
    expect(document.activeElement).toBe(first)

    await userEvent.tab({ shift: true })
    expect(document.activeElement).toBe(last)
  })

  it('closes on Escape and gives focus back to what opened it', async () => {
    const closed = vi.fn()
    show(<Opener onClosed={closed} />)
    const opener = screen.getByText('Open it')
    await userEvent.click(opener)

    await userEvent.keyboard('{Escape}')

    expect(closed).toHaveBeenCalledOnce()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(opener)
  })

  it('closes on a click on the backdrop, and not on one inside the panel', async () => {
    const closed = vi.fn()
    show(<Opener onClosed={closed} />)
    await userEvent.click(screen.getByText('Open it'))

    await userEvent.click(screen.getByLabelText('Name'))
    expect(closed).not.toHaveBeenCalled()

    // The backdrop is the dialog's parent.
    await userEvent.click(screen.getByRole('dialog').parentElement as HTMLElement)
    expect(closed).toHaveBeenCalledOnce()
  })

  it('lets only the innermost of two dialogs answer Escape', async () => {
    const outer = vi.fn()
    const inner = vi.fn()
    show(
      <>
        <Modal title="Outer" onClose={outer} footer={null}>
          <p>behind</p>
        </Modal>
        <Modal title="Inner" onClose={inner} footer={null}>
          <p>in front</p>
        </Modal>
      </>,
    )

    await userEvent.keyboard('{Escape}')

    expect(inner).toHaveBeenCalledOnce()
    expect(outer).not.toHaveBeenCalled()
  })
})
