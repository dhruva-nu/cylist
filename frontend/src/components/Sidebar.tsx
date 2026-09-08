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
 * It is a column of the frame and it is always there. Not a drawer over the
 * page and not something you open: the page narrows to make room for it, which
 * is the whole point of consulting something beside your work instead of on
 * top of it. A panel you have to open first is a panel you forget is there,
 * and one that can be shut is one whose count of overdue cards nobody sees.
 *
 * Under 900px there is no width to give away, so the frame stacks instead —
 * page first, panel under it, the frame itself taking the scroll. Nothing is
 * hidden at any width, because with no control to bring it back, hiding it
 * would be the one change that made Settings unreachable.
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

const FOLDED_KEY = 'cylist.sidebar.folded'

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

export function Sidebar() {
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

  return (
    <aside className={styles.sidebar} aria-label="Sidebar">
      {/* Today's work at the top. It is the section whose answer changes hour
          to hour, and the one worth having in the corner of your eye;
          Settings is the one you set once and leave, so it sits at the
          bottom. */}
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
