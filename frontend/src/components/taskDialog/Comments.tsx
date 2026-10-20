import type { ReactNode } from 'react'
import type { FiledItem, Person, TaskComment, TaskDetail } from '../../api/client'
import { Field } from '../Modal'
import { MentionBox } from '../Mentions'
import { Avatar, ErrorNotice, Tagged } from '../ui'
import type { CommentDeleting } from './useCommentDeleting'
import shared from './shared.module.css'
import styles from './Comments.module.css'

/**
 * What has been said about the card, and a box to say something.
 *
 * Drawn on the form for both a new card and an existing one: a new card gets
 * the composer without the timeline — there is nothing to read yet, and a
 * first comment typed while writing the card is posted with it.
 */
export function Comments({
  task,
  members,
  files,
  author,
  onAuthor,
  comment,
  onComment,
  deleting,
}: {
  /** Null while the card is being written, which is the empty timeline. */
  task: TaskDetail | null
  members: Person[]
  /** The files a comment may tag, and whose tags the timeline draws. */
  files: readonly FiledItem[]
  author: string
  onAuthor: (id: string) => void
  comment: string
  onComment: (text: string) => void
  /** Null on a card being written: nothing has been said to take back yet. */
  deleting: CommentDeleting | null
}) {
  return (
    <Field label="Comments">
      <Timeline
        comments={task?.comments ?? []}
        members={members}
        files={files}
        deleting={deleting}
        emptyText="No comments yet."
      >
        <CommentComposer
          members={members}
          files={files}
          author={author}
          onAuthor={onAuthor}
          comment={comment}
          onComment={onComment}
        />
      </Timeline>
    </Field>
  )
}

/** Who is speaking and what they say — the form's composer, and the detail view's. */
export function CommentComposer({
  members,
  files,
  author,
  onAuthor,
  comment,
  onComment,
}: {
  members: Person[]
  files: readonly FiledItem[]
  author: string
  onAuthor: (id: string) => void
  comment: string
  onComment: (text: string) => void
}) {
  return (
    <div className={shared.composer}>
      <select
        value={author}
        onChange={(event) => onAuthor(event.target.value)}
        aria-label="Comment as"
      >
        <option value="">Unattributed</option>
        {members.map((person) => (
          <option key={person.id} value={person.id}>
            {person.name}
          </option>
        ))}
      </select>
      <MentionBox
        value={comment}
        onChange={onComment}
        members={members}
        files={files}
        className={shared.grow}
        aria-label="Add a comment"
        placeholder="Add a comment, @ to tag someone, > a file…"
      />
    </div>
  )
}

/**
 * The card's comments, oldest first, with whatever the reader may take back
 * offered a ✕ — the same list on the detail view and on the form.
 */
export function Timeline({
  comments,
  members,
  files,
  deleting,
  emptyText,
  children,
}: {
  comments: TaskComment[]
  members: Person[]
  files: readonly FiledItem[]
  deleting: CommentDeleting | null
  /** What the timeline says while nothing has been said. */
  emptyText: string
  /** Drawn under the entries — the form's composer. */
  children?: ReactNode
}) {
  return (
    <>
      <ErrorNotice error={deleting?.error} />
      <div className={styles.timeline}>
        {comments.length ? (
          comments.map((entry) => (
            <TimelineEntry
              key={entry.id}
              entry={entry}
              members={members}
              files={files}
              onDelete={deleting?.deletable(entry) ? () => deleting.remove(entry) : undefined}
              deleting={deleting?.removingId === entry.id}
            />
          ))
        ) : (
          <span className={shared.empty}>{emptyText}</span>
        )}
        {children}
      </div>
    </>
  )
}

/** One timeline entry. System entries are drawn apart from what people wrote. */
function TimelineEntry({
  entry,
  members,
  files,
  onDelete,
  deleting = false,
}: {
  entry: TaskComment
  members: Person[]
  files: readonly FiledItem[]
  /** Omitted where this reader may not take this entry off — see `mayDelete`. */
  onDelete?: (() => void) | undefined
  deleting?: boolean
}) {
  const when = new Date(entry.created_at).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })

  if (entry.kind === 'status_change') {
    const names = (entry.meta.tagged ?? [])
      .map((id) => members.find((person) => person.id === id)?.name)
      .filter((name): name is string => Boolean(name))
    // Tinted by the status it moved to, where that status has a tint of its own.
    const tone = styles[entry.meta.to ?? 'active']

    return (
      <div className={[styles.entry, styles.system, tone].filter(Boolean).join(' ')}>
        <div className={styles.bubble}>
          {entry.body}
          {names.length ? <> · waiting on {names.map((name) => `@${name}`).join(', ')}</> : null}
          <span className={styles.when}> · {when}</span>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.entry}>
      {entry.author ? (
        <Avatar name={entry.author.name} colour={entry.author.colour} />
      ) : (
        <Avatar name="?" colour="var(--ink-3)" />
      )}
      <div className={styles.bubble}>
        <div className={styles.who}>
          {entry.author?.name ?? 'Unattributed'}
          {/* The time and the ✕ as one end, so the name still sits against the
              other: `.who` spreads exactly two children. */}
          <span className={styles.whoEnd}>
            {when}
            {onDelete ? (
              <button
                type="button"
                className={styles.retract}
                disabled={deleting}
                // Named rather than titled "Delete": one timeline holds several
                // of these, and a screen reader hearing "Delete, button" six
                // times has been told nothing about which.
                aria-label={`Delete the comment ${wordsOf(entry.body)}`}
                title="Delete this comment"
                onClick={onDelete}
              >
                ✕
              </button>
            ) : null}
          </span>
        </div>
        <Tagged text={entry.body} members={members} files={files} />
      </div>
    </div>
  )
}

/** The opening of a comment, short enough to sit inside a label. */
function wordsOf(body: string): string {
  const flat = body.trim().replace(/\s+/g, ' ')
  return flat.length > 40 ? `${flat.slice(0, 40)}…` : flat
}
