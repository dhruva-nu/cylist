/**
 * What a file may be renamed to, decided before the request is sent.
 *
 * The server has the last word — it owns the one rule this cannot know, that
 * no other item in the folder already has the name — but the rules that need
 * nothing but the name itself are worth answering here, because a dialog that
 * greys its own Save button says "not that" faster than a round trip that
 * comes back 422.
 *
 * The rules are the server's, in `app/schemas/files.py::clean_name`. They are
 * restated rather than shared because there is no way to share them, so if
 * that function grows a rule this one has to grow it too.
 */

/** The longest name the column will hold; the server's `NAME_MAX_LENGTH`. */
export const NAME_MAX_LENGTH = 200

/**
 * Why `next` cannot be the name, or null if it can be.
 *
 * The unchanged case is one of these: renaming a file to what it is already
 * called is not an error, but it is not a request either, and a Save that
 * fires anyway writes an activity row saying nothing happened.
 */
export function renameProblem(current: string, next: string): string | null {
  const name = next.trim()
  if (!name) return 'A name is needed.'
  if (name === current.trim()) return null
  if (name === '.' || name === '..' || name.includes('/') || name.includes('\\')) {
    return 'A name cannot be a path.'
  }
  if ([...name].some((character) => character < ' ' || character === '\x7f')) {
    return 'A name cannot contain control characters.'
  }
  if (name.length > NAME_MAX_LENGTH) {
    return `A name can be at most ${NAME_MAX_LENGTH} characters.`
  }
  return null
}

/** Whether saving `next` over `current` would actually change anything. */
export function renameIsAChange(current: string, next: string): boolean {
  return next.trim() !== current.trim() && next.trim() !== ''
}

/** A name's extension, lowercased, or '' where it has none. */
export function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/**
 * A warning about the extension the rename would leave behind, or null.
 *
 * Nothing here blocks the rename — the extension is part of the name and the
 * name is the user's — but it is the part with consequences they did not ask
 * for: the row's badge is read off it, and so is the filename the browser
 * saves the download under. Losing it is usually a typo rather than a wish.
 */
export function extensionWarning(current: string, next: string): string | null {
  if (!renameIsAChange(current, next)) return null
  const before = extensionOf(current)
  const after = extensionOf(next.trim())
  if (!before || before === after) return null
  if (!after) return `This drops the .${before} ending, which is what it downloads as.`
  return `This changes .${before} to .${after}, which is what it downloads as.`
}
