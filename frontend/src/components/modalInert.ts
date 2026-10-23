/**
 * Keep the page behind a dialog out of reach.
 *
 * `aria-modal` asks assistive technology to ignore the rest of the page, and a
 * focus trap keeps Tab inside, but neither stops a screen reader's browse mode
 * or a pointer from reaching what is behind. `inert` does all of it at once:
 * not focusable, not clickable, not read.
 *
 * Dialogs nest, so it is applied to the whole stack: the app is inert while any
 * dialog is open, and every dialog but the innermost is inert too.
 */

/** What this needs of an element, so it can be tested without a document. */
export interface Inertable {
  inert: boolean
}

/**
 * Make `app` inert if any dialog is open, and every dialog in `dialogs` but the
 * last — which is the innermost — inert as well. `dialogs` is in the order they
 * were opened.
 */
export function applyInert(app: Inertable | null, dialogs: readonly Inertable[]): void {
  if (app) app.inert = dialogs.length > 0
  dialogs.forEach((dialog, index) => {
    dialog.inert = index < dialogs.length - 1
  })
}

/** The attribute every dialog's backdrop carries, so they can be found in the page. */
export const SCRIM_ATTRIBUTE = 'data-modal-scrim'

/** `applyInert` to the page as it is now. */
export function syncInert(): void {
  const scrims = [...document.querySelectorAll<HTMLElement>(`[${SCRIM_ATTRIBUTE}]`)].filter(
    (scrim) => scrim.isConnected,
  )
  applyInert(document.getElementById('root'), scrims)
}
