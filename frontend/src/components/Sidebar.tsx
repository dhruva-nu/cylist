/**
 * The right-hand sidebar: the things you consult while working, rather than
 * the things you navigate to.
 *
 * A project's areas are places you go and stay, and they are tabs in the bar
 * above. What lives here is the other kind of thing — a preference to set, a
 * list to glance at, a report to read off — none of which is worth leaving the
 * board for, and all of which used to be a dialog over the top of whatever you
 * were doing.
 *
 * It is a column of the frame rather than a drawer floating over it: on a wide
 * display the page narrows and nothing is covered, which is the whole point of
 * consulting something beside your work instead of on top of it. Under 900px
 * there is no room to give away, so it becomes an overlay against the right
 * edge — and there, where it does cover the page, Escape closes it.
 *
 * Sections stack and each folds. That is what makes this extensible without a
 * tab bar to redesign every time something is added: a new section is one more
 * entry in the column, and it opens or folds on its own without arguing with
 * its neighbours over which one is showing.
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
import { Today } from './Today'
import styles from './Sidebar.module.css'

const THEME_LABELS: Record<ThemeChoice, string> = {
  light: 'Light',
  dark: 'Dark',
  system: 'System',
}

/** Where the sidebar stops being a column of the frame and starts covering it.
 * The same number as the `max-width` in the stylesheet, and it has to be: the
 * keyboard behaviour differs between the two shapes, so the script has to know
 * which one the CSS chose. */
const OVERLAY_WIDTH = 900

const OPEN_KEY = 'cylist.sidebar.open'
const FOLDED_KEY = 'cylist.sidebar.folded'

/**
 * Whether the sidebar is showing, remembered between visits.
 *
 * With nothing remembered it opens on a display wide enough to hold it beside
 * the page and stays shut on one that is not — an overlay covering the page
 * before anybody asked for it is a worse first impression than a control they
 * have to find.
 *
 * Every touch of localStorage is wrapped, for the reason `theme.ts` gives:
 * reading it throws outright in a private window, and a frame that will not
 * render is a worse outcome than a preference that is not remembered.
 */
function readOpen(): boolean {
  try {
    const stored = window.localStorage.getItem(OPEN_KEY)
    if (stored === 'true') return true
    if (stored === 'false') return false
  } catch {
    // Fall through to the width, which is the answer for a first visit anyway.
  }
  return window.innerWidth >= OVERLAY_WIDTH
}

export function useSidebar(): { open: boolean; toggle: () => void; close: () => void } {
  const [open, setOpen] = useState<boolean>(readOpen)

  useEffect(() => {
    try {
      window.localStorage.setItem(OPEN_KEY, String(open))
    } catch {
      // It still holds for this visit; it just will not be remembered.
    }
  }, [open])

  const toggle = useCallback(() => setOpen((showing) => !showing), [])
  const close = useCallback(() => setOpen(false), [])

  return { open, toggle, close }
}

/** Whether the sidebar is currently the overlay shape rather than a column.
 * Watched rather than read once, so dragging a window narrow moves the
 * keyboard behaviour with the layout instead of leaving them disagreeing. */
function useOverlay(): boolean {
  const [overlay, setOverlay] = useState(
    () => window.matchMedia(`(max-width: ${OVERLAY_WIDTH - 1}px)`).matches,
  )

  useEffect(() => {
    const query = window.matchMedia(`(max-width: ${OVERLAY_WIDTH - 1}px)`)
    const onChange = (event: MediaQueryListEvent) => setOverlay(event.matches)
    query.addEventListener('change', onChange)
    setOverlay(query.matches)
    return () => query.removeEventListener('change', onChange)
  }, [])

  return overlay
}

/**
 * Which sections are folded away, remembered by name.
 *
 * Kept the way the board's folded columns are: a list of what is *shut*, so a
 * section added later starts open rather than having to be listed to be seen
 * at all.
 *
 * On a first visit the day report is folded and nothing else is. It is by far
 * the longest section — a whole day of a project, however busy the day was —
 * and open by default it would push everything above it out of view before
 * anybody had said they wanted to read it.
 */
const FOLDED_TO_BEGIN_WITH = ['Day report']

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

/**
 * The toggle in the header, and the panel it shows.
 *
 * The button lives in the bar so it sits with the other chrome, and the panel
 * is a column of the frame further down — two places in the tree for one
 * control, which is why the state is a hook the Shell owns rather than
 * something either half keeps to itself.
 */
export function SidebarToggle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      className={styles.toggle}
      aria-expanded={open}
      aria-controls="sidebar"
      aria-label={open ? 'Close the sidebar' : 'Open the sidebar'}
      title={open ? 'Close the sidebar' : 'Open the sidebar'}
      onClick={onToggle}
      data-open={open}
    >
      {/* A panel with its right-hand third filled: the shape of what the button
          does, at the size the bar's other marks are drawn. */}
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <rect x="2.4" y="3.6" width="15.2" height="12.8" rx="2.4" />
        <path d="M13 3.6v12.8" />
        <path
          className={styles.toggleFill}
          d="M13 3.6h2.2a2.4 2.4 0 0 1 2.4 2.4v8a2.4 2.4 0 0 1-2.4 2.4H13z"
        />
      </svg>
    </button>
  )
}

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const overlay = useOverlay()
  const [folded, setFolded] = useState<string[]>(readFolded)

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

  // Only while it is covering the page. As a column it covers nothing, and
  // Escape there would be a key that closes something the user is reading
  // beside their work for no reason they asked for — and would fight the
  // dialogs, which listen for the same key on the same document.
  useEffect(() => {
    if (!open || !overlay) return

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, overlay, onClose])

  // Unmounted rather than hidden. Every section here is a live query, and a
  // shut sidebar that keeps refetching what nobody is looking at is a cost
  // paid on every screen in the app.
  if (!open) return null

  return (
    <aside
      id="sidebar"
      className={`${styles.sidebar} ${overlay ? styles.overlay : ''}`}
      aria-label="Sidebar"
    >
      {/* A way out, for the one shape that needs one. As a column of the frame
          the toggle in the bar is still there to press again; as an overlay it
          is directly underneath this panel, so without this the only way back
          to the page is the keyboard. */}
      {overlay ? (
        <button type="button" className={styles.close} onClick={onClose}>
          Close
          <span aria-hidden="true">✕</span>
        </button>
      ) : null}
      {/* Today's work first. It is the section you open the sidebar for, and
          the one whose answer changes hour to hour; Settings is the one you
          set once and leave, so it sits at the bottom. */}
      <Today folded={folded.includes('Today')} onToggle={() => toggleSection('Today')} />
      {/* The day behind you, under the day in front of you. It is the longest
          section by far, so it sits below the list it would otherwise push off
          the top of the panel. */}
      <DayReportPanel
        folded={folded.includes('Day report')}
        onToggle={() => toggleSection('Day report')}
      />
      <SidebarSection
        name="Settings"
        folded={folded.includes('Settings')}
        onToggle={() => toggleSection('Settings')}
      >
        <SettingsSection />
      </SidebarSection>
    </aside>
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
