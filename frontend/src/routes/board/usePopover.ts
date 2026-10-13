import { useEffect, useRef, useState } from 'react'

/**
 * Open and shut for a popover anchored under its button: shut again by a
 * press anywhere outside it or by Escape, which hands focus back to the
 * button — the place a keyboard user opened it from.
 *
 * The document is only listened to while the popover is open, so a toolbar of
 * shut popovers costs nothing.
 */
export function usePopover() {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      const inside = event.target instanceof Node && root.current?.contains(event.target)
      if (!inside) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      setOpen(false)
      button.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return { open, setOpen, root, button }
}
