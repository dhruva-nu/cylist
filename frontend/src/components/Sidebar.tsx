/**
 * The left-hand sidebar: the things you consult while working, rather than
 * the things you navigate to.
 *
 * A project's areas are places you go and stay, and they are tabs in the bar
 * above. What lives here is the other kind of thing — a preference to set, a
 * list to glance at, a report to read off — none of which is worth leaving the
 * board for, and all of which used to be a dialog over the top of whatever you
 * were doing.
 *
 * It is a column of the frame down the left-hand edge rather than a drawer
 * over the page: the page narrows to make room for it, which is the whole
 * point of consulting something beside your work instead of on top of it.
 *
 * It collapses, and what it collapses to is the reason it is allowed to. Not
 * away — to a rail against the same edge, holding the handle that brings it
 * back and the count of what is due today. So the two objections to a panel
 * that shuts both go: there is no state in which Settings is unreachable, and
 * no state in which three overdue cards are behind something you forgot was
 * there. What the rail buys is the width, which is the one thing a board four
 * columns wide actually wants back.
 *
 * Collapsed or not is remembered between visits, and it collapses by gliding
 * rather than by swapping: the two widths are drawn one over the other and
 * the box's own width is the thing that moves. See the note over the markup,
 * and `--sidebar-glide` in the stylesheet for the timing.
 *
 * Both widths stay mounted while it does. That used to be the thing to avoid,
 * back when every section was a live query and a shut panel refetching what
 * nobody was looking at was a cost paid on every screen — but the sections
 * are strips now, and the one query left is the count, which the rail asks
 * for anyway under the same key.
 *
 * Under 900px the frame stacks instead — panel first, page under it, the
 * frame itself taking the scroll — and the rail becomes a strip across the
 * top rather than a column down the side. Panel first because on this side it
 * is first in the markup as well as first on screen, and a stacked column
 * that reversed the two would be putting tab order and reading order at odds
 * to save a scroll the handle already saves.
 *
 * Sections stack, and each either folds open in place or opens over the page.
 * That is what makes this extensible without a tab bar to redesign every time
 * something is added: a new section is one more entry in the column, and it
 * behaves on its own without arguing with its neighbours over which one is
 * showing.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'
import { THEME_CHOICES, useTheme, type ThemeChoice } from '../theme/theme'
import { DayReportPanel } from './DayReport'
import { SidebarSection } from './SidebarSection'
import { Today, TodayCount } from './Today'
import styles from './Sidebar.module.css'

const THEME_LABELS: Record<ThemeChoice, string> = {
  light: 'Light',
  dark: 'Dark',
  system: 'System',
}

/**
 * Not `cylist.sidebar.open`, which this replaces.
 *
 * That key was written on mount with whatever the panel happened to be
 * showing, so every browser that ever loaded the app holds a value under it —
 * and a value nobody chose cannot be told apart from one somebody did. Only a
 * press of the handle writes this one, so what is under it is always an
 * answer rather than an echo of the default.
 */
const OPEN_KEY = 'cylist.sidebar.showing'
const FOLDED_KEY = 'cylist.sidebar.folded'

/**
 * Whether the panel is showing, remembered between visits.
 *
 * Collapsed on a first visit, at every width. The panel is a third of a
 * narrow window and a board wants every pixel of the rest, so what somebody
 * arrives to is their work with a rail beside it — and the rail is not
 * nothing: it holds the handle and the count of what is due today, which is
 * the one thing in the panel worth seeing before you have asked for it.
 * Anybody who wants the panel opens it once and it stays open.
 *
 * Every touch of localStorage is wrapped, for the reason `theme.ts` gives:
 * reading it throws outright in a private window, and a frame that will not
 * render is a worse outcome than a preference that is not remembered.
 */
function readOpen(): boolean {
  try {
    return window.localStorage.getItem(OPEN_KEY) === 'true'
  } catch {
    // A private window. Collapsed is the answer for a first visit anyway.
    return false
  }
}

/**
 * Which sections are folded away, remembered by name.
 *
 * Kept the way the board's folded columns are: a list of what is *shut*, so a
 * section added later starts open rather than having to be listed to be seen
 * at all.
 *
 * Nothing is folded on a first visit. The day report used to be, being by far
 * the longest thing in the panel; it is a button now and opens over the page,
 * so what is left here is a short list and one preference, and both are worth
 * more open than the scroll they cost.
 */
const FOLDED_TO_BEGIN_WITH: string[] = []

function readFolded(): string[] {
  let stored: string | null = null
  try {
    stored = window.localStorage.getItem(FOLDED_KEY)
  } catch {
    // A private window. The defaults are the right answer there too.
    return FOLDED_TO_BEGIN_WITH
  }
  if (stored === null) return FOLDED_TO_BEGIN_WITH

  try {
    const parsed: unknown = JSON.parse(stored)
    // An empty list is a real answer — every section deliberately open — so it
    // is honoured rather than falling back to the defaults.
    return Array.isArray(parsed)
      ? parsed.filter((name): name is string => typeof name === 'string')
      : FOLDED_TO_BEGIN_WITH
  } catch {
    return FOLDED_TO_BEGIN_WITH
  }
}

export function Sidebar() {
  const [open, setOpen] = useState<boolean>(readOpen)
  const [folded, setFolded] = useState<string[]>(readFolded)

  /**
   * Show or hide the panel, and remember which.
   *
   * Written here, on the press, rather than in an effect watching `open`.
   * An effect would also fire on the first render, storing the default as
   * though it had been chosen — which is exactly what made the old key
   * useless.
   */
  const showSidebar = useCallback((showing: boolean) => {
    setOpen(showing)
    try {
      window.localStorage.setItem(OPEN_KEY, String(showing))
    } catch {
      // It still holds for this visit; it just will not be remembered.
    }
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(FOLDED_KEY, JSON.stringify(folded))
    } catch {
      // As above: the fold holds for this visit and is not remembered.
    }
  }, [folded])

  const toggleSection = useCallback((name: string) => {
    setFolded((current) =>
      current.includes(name) ? current.filter((one) => one !== name) : [...current, name],
    )
  }, [])

  return (
    <aside
      className={`${styles.sidebar} ${open ? styles.showing : styles.shut}`}
      aria-label="Sidebar"
    >
      {/*
        Two layers in one box, stacked in a single grid cell, with the box's
        width the thing that moves between them. That is what makes the change
        an animation rather than a jump: a panel swapped for a rail has nothing
        to interpolate, while a box going 336px → 46px does, and the layer on
        the way out can fade while the one on the way in fades up.

        Both are always mounted, which used to be the thing worth avoiding —
        the sections were live queries and a shut panel refetching what nobody
        was looking at was a cost on every screen. They are not any more.
        Today and the day report are strips that fetch nothing until their
        dialog is opened, Settings is local state, and the one query left is
        the count — which the rail asks for anyway, under the same key, so
        react-query answers both from one request.

        The hidden layer goes `visibility: hidden` once it has faded, so it
        leaves the tab order and the accessibility tree rather than sitting
        there as a second set of controls nobody can see.
      */}
      <div className={styles.rail}>
        <Handle open={false} onToggle={() => showSidebar(true)} />
        {/* The one thing worth saying from a 46px rail: how much is wanted
            today, and in red if any of it is late. It is the count the Today
            strip carries, and it is here for exactly the reason a panel that
            can be shut needs it to be — a number nobody can see is a number
            that stops being worth keeping. */}
        <TodayCount />
        <span className={styles.railName} aria-hidden="true">
          Sidebar
        </span>
      </div>

      <div className={styles.panel}>
        {/* Sticky, so the way out of the panel is where you left it however
            far down the sections you have scrolled. */}
        <div className={styles.panelHead}>
          <Handle open onToggle={() => showSidebar(false)} />
        </div>
        {/* Today's work at the top. It is the one whose answer changes hour to
            hour, and its count is the thing worth having in the corner of your
            eye; Settings is the one you set once and leave, so it sits at the
            bottom. */}
        <Today />
        {/* The day behind you, under the day in front of you. Both are strips
            that open over the page rather than sections that fold: a list you
            consult and a report you ask for are both things you want at a
            dialog's width and then want gone. Settings is the one thing here
            that really is a panel — a preference you set in place — so it is
            the one that still folds. */}
        <DayReportPanel />
        <SidebarSection
          name="Settings"
          folded={folded.includes('Settings')}
          onToggle={() => toggleSection('Settings')}
        >
          <SettingsSection />
        </SidebarSection>
      </div>
    </aside>
  )
}

/**
 * The control that collapses the panel, and the one that brings it back.
 *
 * One component in both shapes on purpose: it is the same control, in the same
 * place — hard against the left-hand edge — and a reader who has learnt where
 * the handle is should not have to learn it twice.
 *
 * The chevron points where pressing it sends the panel: left, off the edge, to
 * put it away; right, back over the page, to bring it out. It lives in the
 * panel rather than up in the bar, which is where the toggle used to be —
 * a button in one corner of the frame acting on something in the other is a
 * button you have to be told about.
 */
function Handle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const says = open ? 'Collapse the sidebar' : 'Expand the sidebar'

  return (
    <button
      type="button"
      className={styles.handle}
      aria-expanded={open}
      aria-label={says}
      title={says}
      onClick={onToggle}
    >
      <span aria-hidden="true">{open ? '‹' : '›'}</span>
    </button>
  )
}

/**
 * What you can set. One thing so far, and the shape is what matters: a labelled
 * row per preference, so the second one is a row rather than a redesign.
 */
function SettingsSection() {
  return (
    <div className={styles.settings}>
      <Setting
        label="Theme"
        hint="“System” follows your operating system, and keeps following it when it changes at dusk."
      >
        <ThemeChoiceGroup />
      </Setting>
    </div>
  )
}

function Setting({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className={styles.setting}>
      <span className={styles.settingLabel}>{label}</span>
      {children}
      {hint ? <span className={styles.settingHint}>{hint}</span> : null}
    </div>
  )
}

/**
 * Light / dark / system.
 *
 * A radio group rather than three buttons: the three are one choice with one
 * answer, so a screen reader should say "2 of 3" and the arrow keys should
 * move between them. Roving tabindex keeps the whole control to a single tab
 * stop.
 *
 * It used to sit in the header. Three words of chrome on every screen, for a
 * choice made once and then left alone, is the sort of thing the bar is better
 * without — and a Settings section with nothing in it would have been worse.
 */
function ThemeChoiceGroup() {
  const { theme, setTheme } = useTheme()
  const group = useRef<HTMLDivElement>(null)

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'].includes(event.key)) return
    const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : -1

    event.preventDefault()
    const at = THEME_CHOICES.indexOf(theme)
    // The fallback never fires — the modulo keeps the index in range — but
    // saying so costs less than an assertion that stops being true.
    const next = THEME_CHOICES[(at + step + THEME_CHOICES.length) % THEME_CHOICES.length] ?? theme
    setTheme(next)
    // Focus follows selection in a radio group, and the button for `next` is
    // the only one that will be tabbable after this render.
    group.current?.querySelector<HTMLElement>(`[data-choice="${next}"]`)?.focus()
  }

  return (
    <div
      ref={group}
      className={styles.theme}
      role="radiogroup"
      aria-label="Colour theme"
      onKeyDown={onKeyDown}
    >
      {THEME_CHOICES.map((choice) => (
        <button
          key={choice}
          type="button"
          data-choice={choice}
          role="radio"
          aria-checked={theme === choice}
          tabIndex={theme === choice ? 0 : -1}
          className={theme === choice ? styles.themeOn : undefined}
          onClick={() => setTheme(choice)}
        >
          {THEME_LABELS[choice]}
        </button>
      ))}
    </div>
  )
}
