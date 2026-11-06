/**
 * Images in a description: what goes into the markdown, and how it is drawn.
 *
 * A description holds `![name](/api/v1/images/{id})` and never the bytes —
 * the image itself is in the blob store (CYLIST-182). The URL is stored from
 * the site root, not this deployment's base path, so a card reads the same
 * from wherever it is opened; `imageSrc` adds the prefix when drawing.
 */

import { withBase } from '../api/basePath'

/** Where the server serves description images, from the site root. */
export const IMAGE_PATH_PREFIX = '/api/v1/images/'

/** The types the server accepts, for the file picker's filter. */
export const IMAGE_TYPES = 'image/png,image/jpeg,image/gif,image/webp'

/** The markdown for one image. Brackets in the name would close the alt text early. */
export function imageMarkdown(name: string, url: string): string {
  const alt = name.replace(/\.[^.]+$/, '').replace(/[[\]\\]/g, '') || 'image'
  return `![${alt}](${url})`
}

/**
 * `text` put into `value` at `at`, on a line of its own, and where the caret
 * belongs afterwards. An image in the middle of a sentence is drawn as one,
 * which is never what pasting a screenshot meant.
 */
export function insertBlock(
  value: string,
  at: number,
  text: string,
): { value: string; caret: number } {
  const position = Math.max(0, Math.min(at, value.length))
  const before = value.slice(0, position)
  const after = value.slice(position)
  const lead = before === '' || before.endsWith('\n') ? '' : '\n'
  const trail = after.startsWith('\n') ? '' : '\n'
  const inserted = `${lead}${text}${trail}`
  return { value: before + inserted + after, caret: position + inserted.length }
}

/** The files among a paste or a drop that are images. */
export function imageFiles(files: FileList | readonly File[] | null | undefined): File[] {
  return Array.from(files ?? []).filter((file) => file.type.startsWith('image/'))
}

/** An image's `src` as this page must ask for it: ours under the base path, others as given. */
export function imageSrc(src: string | undefined, base?: string): string | undefined {
  if (src === undefined || !src.startsWith(IMAGE_PATH_PREFIX)) return src
  return withBase(src, base)
}
