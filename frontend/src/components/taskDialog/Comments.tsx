import { useMutation } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import {
  api,
  type FiledItem,
  type Person,
  type TaskComment,
  type TaskDetail,
} from '../../api/client'
import { Field } from '../Modal'
import { MentionBox } from '../Mentions'
import { Avatar, Button, ErrorNotice, Tagged } from '../ui'
import type { CommentDeleting } from './useCommentDeleting'
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
        task={task}
        members={members}
        files={files}
        deleting={deleting}
        emptyText="No comments yet — start the conversation below."
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

/**
 * Who is speaking and what they say — the form's composer, and the detail
 * view's. One card: the speaker's face, a roomy box, and a footer holding who
 * it is posted as and, where this composer posts on its own, the button.
 */
export function CommentComposer({
  members,
  files,
  author,
  onAuthor,
  comment,
  onComment,
  onSubmit,
  submitLabel = 'Add comment',
  canSubmit = true,
}: {
  members: Person[]
  files: readonly FiledItem[]
  author: string
  onAuthor: (id: string) => void
  comment: string
  onComment: (text: string) => void
  /** Present where the composer posts by itself; the form posts with Save. */
  onSubmit?: (() => void) | undefined
  submitLabel?: string
  canSubmit?: boolean
}) {
  const [changing, setChanging] = useState(false)
  const speaker = members.find((person) => person.id === author)

  return (
    <div className={styles.composer}>
      {speaker ? (
        <Avatar name={speaker.name} colour={speaker.colour} />
      ) : (
        <Avatar name="?" colour="var(--ink-3)" />
      )}
      <div className={styles.card}>
        <MentionBox
          value={comment}
          onChange={onComment}
          members={members}
          files={files}
          multiline
          rows={3}
          onSubmit={onSubmit && canSubmit ? onSubmit : undefined}
          className={styles.draft}
          aria-label="Add a comment"
          placeholder="Add a comment, @ to tag someone, > a file…"
        />
        <div className={styles.footer}>
          <div className={styles.as}>
            {changing ? (
              <select
                value={author}
                onChange={(event) => {
                  onAuthor(event.target.value)
                  setChanging(false)
                }}
                onBlur={() => setChanging(false)}
                aria-label="Comment as"
                className={styles.asSelect}
                autoFocus
              >
                <option value="">Unattributed</option>
                {members.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name}
                  </option>
                ))}
              </select>
            ) : (
              <>
                <span>
                  Commenting as <strong>{speaker?.name ?? 'no one (unattributed)'}</strong>
                </span>
                <button type="button" className={styles.change} onClick={() => setChanging(true)}>
                  Change
                </button>
              </>
            )}
          </div>
          {onSubmit ? (
            <>
              <span className={styles.hint}>⌘↵ to post</span>
              <Button small variant="go" disabled={!canSubmit} onClick={onSubmit}>
                {submitLabel}
              </Button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/**
 * A card's timeline as far back as the reader has asked for, oldest first.
 *
 * The card carries its newest entries (CYLIST-130); the ones before them are
 * read a page at a time from `comments_next`, newest first, and put above.
 * What has been read is let go of when the timeline itself changes — a
 * comment posted or taken back — because the card's own window has then
 * moved, and keeping the old pages would leave a gap or a ghost between them.
 */
function useEarlierComments(task: TaskDetail | null) {
  const shown = `${task?.id}:${task?.comments_next}:${task?.comment_count}`
  const [read, setRead] = useState({ shown, older: [] as TaskComment[], next: task?.comments_next })
  // State that follows props, settled during render — React's own pattern.
  if (read.shown !== shown) setRead({ shown, older: [], next: task?.comments_next })

  const earlier = useMutation({
    mutationFn: (after: string) => api.listComments(task?.reference ?? '', after),
    onSuccess: (page) =>
      setRead((current) => ({
        ...current,
        older: [...[...page.items].reverse(), ...current.older],
        next: page.next,
      })),
  })

  const comments = [...read.older, ...(task?.comments ?? [])]
  const unread = (task?.comment_count ?? 0) - comments.length
  const { next } = read
  return {
    comments,
    more: next ? { unread, loading: earlier.isPending, onLoad: () => earlier.mutate(next) } : null,
    error: earlier.error,
  }
}

/**
 * The card's comments, oldest first, with whatever the reader may take back
 * offered a ✕ — the same list on the detail view and on the form. A long one
 * starts at its newest entries, with the way to the earlier ones above them.
 */
export function Timeline({
  task,
  members,
  files,
  deleting,
  emptyText,
  children,
}: {
  /** Null while the card is being written, which is the empty timeline. */
  task: TaskDetail | null
  members: Person[]
  files: readonly FiledItem[]
  deleting: CommentDeleting | null
  /** What the timeline says while nothing has been said. */
  emptyText: string
  /** Drawn under the entries — the form's composer. */
  children?: ReactNode
}) {
  const { comments, more, error } = useEarlierComments(task)
  return (
    <>
      <ErrorNotice error={deleting?.error ?? error} />
      <div className={styles.timeline}>
        {more ? (
          <button
            type="button"
            className={styles.earlier}
            disabled={more.loading}
            onClick={more.onLoad}
          >
            {more.loading
              ? 'Loading…'
              : `Show earlier · ${more.unread} more ${more.unread === 1 ? 'entry' : 'entries'}`}
          </button>
        ) : null}
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
          <p className={styles.quiet}>{emptyText}</p>
        )}
      </div>
      {children}
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
