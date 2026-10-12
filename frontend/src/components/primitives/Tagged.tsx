import { api, type FiledItem, type Person } from '../../api/client'
import { splitMentions } from '../mentions'
import styles from './ui.module.css'

/**
 * Text with its tags drawn as tags — `@` for a person, `>` for a file; see
 * `mentions.ts` for what counts as one. Used wherever somebody's prose is read
 * back: a description, a comment, the label on a stage.
 *
 * A tagged file is a link, because a file named in a comment was named so that
 * whoever reads the comment can open it; a highlight you had to go to the
 * files screen to act on would be a decoration. A person's tag is not a link:
 * there is nowhere in this product that a name goes to.
 *
 * Whitespace is the author's. A description is written in paragraphs, and
 * reading it back as one run-on line would lose what they wrote.
 */
export function Tagged({
  text,
  members,
  files,
}: {
  text: string
  members: readonly Person[]
  /**
   * The project's files, for its `>` tags. Omitted where they are not to hand,
   * which costs a tag its pill and its link — the words are the words either
   * way, which is the point of tagging in the text itself.
   */
  files?: readonly FiledItem[] | undefined
}) {
  return (
    <span className={styles.tagged}>
      {splitMentions(text, members, files ?? []).map((run, index) => {
        if (run.kind === 'mention') {
          return (
            <span
              key={index}
              className={styles.mention}
              title={`${run.person.name} — ${run.person.title}`}
            >
              {run.text}
            </span>
          )
        }
        if (run.kind === 'file') return <FileTag key={index} file={run.file} text={run.text} />
        return <span key={index}>{run.text}</span>
      })}
    </span>
  )
}

/**
 * A tagged file, as the link it is: a download for bytes we hold, the document
 * itself for one living in SharePoint or Drive.
 *
 * The words are the author's `>name` rather than a name read back out of the
 * file, so the tag reads in the paragraph exactly as it does in the box it was
 * typed in — including the case it was typed in.
 */
function FileTag({ file, text }: { file: FiledItem; text: string }) {
  const where = file.folder_path ? ` — in ${file.folder_path}` : ''

  return file.kind === 'link' && file.url ? (
    <a
      className={styles.fileTag}
      href={file.url}
      target="_blank"
      rel="noreferrer"
      title={`${file.name}${where} — opens ${file.url}`}
    >
      {text}
    </a>
  ) : (
    <a
      className={styles.fileTag}
      href={api.downloadUrl(file.id)}
      download={file.name}
      title={`${file.name}${where} — downloads`}
    >
      {text}
    </a>
  )
}
