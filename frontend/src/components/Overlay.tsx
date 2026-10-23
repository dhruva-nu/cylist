import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'

/** The element in `index.html` that everything drawn over the page is put in. */
export const OVERLAY_ID = 'overlay'

/**
 * Draw `children` outside the app's own element, in the overlay beside it.
 *
 * A dialog is `inert`-ing the page behind it — see `modalInert` — and anything
 * inside that page would go with it: the dialog itself, the toast that says its
 * request failed, the live region that says it saved. So everything that must
 * stay reachable while a dialog is open lives here instead. Nothing is drawn
 * where there is no overlay to put it in, which is the case under a server
 * render.
 */
export function Overlay({ children }: { children: ReactNode }) {
  const host = typeof document === 'undefined' ? null : document.getElementById(OVERLAY_ID)
  return host ? createPortal(children, host) : null
}
