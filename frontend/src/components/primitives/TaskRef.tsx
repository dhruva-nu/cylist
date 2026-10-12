import type { PrState } from '../../api/client'
import { JiraIcon, PrIcon } from './icons'
import styles from './ui.module.css'

/**
 * A task's Jira key or pull request, as short as it can be said.
 *
 * Both fields are free text on the way in, and people fill them either way:
 * `ATL-41` and `#212` typed by hand on one card, the whole URL pasted off the
 * browser bar on the next. The URL is the more useful of the two — it is the
 * only form that can be followed — and the less readable, being long enough to
 * push everything else off the row it sits on. So a URL shows as the identifier
 * it contains and carries the URL on the link; anything else shows as typed,
 * with nothing to click.
 *
 * `stopPropagation` on the pointer and key events is what makes this safe to
 * drop on a board card. That card is both the drag handle and the button that
 * opens the task, so an event left to bubble would open the dialog behind the
 * new tab, or pick the card up instead of following the link.
 *
 * `compact` is the board's form: the mark alone, with the identifier moved to
 * the tooltip and the accessible name. Nobody reads `GLSSW-134004` off a
 * column — they click it, or they open the card and read it spelled out — and
 * thirteen monospace characters that begin the same way on every card in the
 * project were the widest thing on the row carrying the least. Everywhere with
 * room to say it, the dialog above all, still says it.
 *
 * `state` is a pull request's own (CYLIST-76), and it is drawn on the icon
 * rather than said beside it: the whole point of the marks is that a column of
 * cards reports where its work stands without being read word by word. The
 * identifier keeps `--link` whatever the state, because it is the part you can
 * click and the only thing on the row that should look clickable. The state is
 * spelled out in the tooltip and the accessible name, where colour and shape
 * are no use at all.
 */
export function TaskRef({
  kind,
  value,
  state = 'raised',
  compact = false,
}: {
  kind: 'jira' | 'pr'
  value: string
  /** How far the pull request got. Ignored for a Jira reference, which has no state. */
  state?: PrState
  compact?: boolean
}) {
  const { label, href } = kind === 'jira' ? jiraRefDisplay(value) : prRefDisplay(value)
  const icon =
    kind === 'jira' ? (
      <JiraIcon />
    ) : (
      <span className={`${styles.prMark} ${styles[`pr_${state}`]}`}>
        <PrIcon state={state} />
      </span>
    )
  // The icon is decoration and the label is an abbreviation, so neither says
  // on its own what the reference is of — nor, for a pull request, how far it
  // got, which is the one thing here that is carried by colour alone.
  const accessibleName =
    kind === 'jira' ? `Jira ${label}` : `Pull request ${label}, ${PR_STATE_WORDS[state]}`

  // Hidden rather than dropped: what compact takes off the card it keeps for
  // the reader who is not looking at the card.
  const text = compact ? (
    <span className="visually-hidden">{accessibleName}</span>
  ) : (
    <span className={styles.refLabel}>{label}</span>
  )

  if (!href) {
    return (
      <span className={styles.refMark} title={compact ? accessibleName : undefined}>
        {icon}
        {text}
      </span>
    )
  }

  return (
    <a
      className={`${styles.refMark} ${styles.taskRef}`}
      href={href}
      target="_blank"
      rel="noreferrer"
      aria-label={accessibleName}
      // The URL is the useful tooltip where the identifier is already on
      // screen. Where it is not, the identifier is what the tooltip is for.
      title={compact ? accessibleName : href}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      {icon}
      {text}
    </a>
  )
}

/** How each state is said in a tooltip and read out. Lower case: it finishes a
 * sentence that starts "Pull request #212". */
const PR_STATE_WORDS: Record<PrState, string> = {
  raised: 'raised',
  approved: 'approved',
  merged: 'merged',
}

/** What :func:`TaskRef` renders: the text to show, and where it points. */
interface RefDisplay {
  label: string
  /** Null when the stored value is not a URL — there is nothing to follow. */
  href: string | null
}

/**
 * A stored `https://…` value as a URL, or null.
 *
 * The scheme test is not only about `new URL` accepting the string: it is what
 * keeps a `javascript:` value someone typed into the Jira box out of an href.
 */
function asUrl(value: string): URL | null {
  if (!/^https?:\/\//i.test(value)) return null
  try {
    return new URL(value)
  } catch {
    return null
  }
}

/** The last path segment: the shortest thing left to show when nothing parses. */
function lastSegment(url: URL): string {
  return url.pathname.split('/').filter(Boolean).pop() ?? url.hostname
}

function jiraRefDisplay(value: string): RefDisplay {
  const url = asUrl(value)
  if (!url) return { label: value, href: null }
  // The query string is searched as well as the path: `/browse/ATL-41` is the
  // link people copy, but a board hands out `?selectedIssue=ATL-41`.
  const key = /[A-Z][A-Z0-9]*-\d+/i.exec(`${url.pathname} ${url.search}`)
  return { label: key ? key[0].toUpperCase() : lastSegment(url), href: value }
}

/**
 * How a stored pull-request reference reads as an identifier: `#212`.
 *
 * Exported because a control *about* one has to name it — the select on the
 * card's detail view is labelled "State of pull request #212" — and the whole
 * URL in an accessible name is a sentence nobody can listen to.
 */
export function prRefLabel(value: string): string {
  return prRefDisplay(value).label
}

/**
 * The repository a pull-request URL is in: `cylist` for
 * `github.com/dhruva-nu/cylist/pull/212`. Null when the value is not a URL or
 * names no repository — `#212` typed by hand says nothing about where it is.
 *
 * The board's list of a card's pull requests leads with this, because three
 * `#212`s in three repositories are otherwise three identical lines.
 */
export function prRefRepo(value: string): string | null {
  const url = asUrl(value)
  if (!url) return null
  const segments = url.pathname.split('/').filter((segment) => segment && segment !== '-')
  const at = segments.findIndex((segment) =>
    /^(?:pull|pulls|pull-requests|merge_requests)$/i.test(segment),
  )
  return at > 0 ? (segments[at - 1] ?? null) : null
}

/** How a stored Jira reference reads as an identifier: `ATL-41`. */
export function jiraRefLabel(value: string): string {
  return jiraRefDisplay(value).label
}

function prRefDisplay(value: string): RefDisplay {
  const url = asUrl(value)
  if (!url) return { label: value, href: null }
  // GitHub and Bitbucket say `pull`/`pull-requests`, GitLab `merge_requests`.
  const number = /\/(?:pull|pull-requests|merge_requests)\/(\d+)/i.exec(url.pathname)
  return { label: number ? `#${number[1]}` : lastSegment(url), href: value }
}
