/**
 * Light, dark, or whatever the operating system says.
 *
 * The choice is stamped on `<html>` as `data-theme`, which is what the three
 * blocks in tokens.css key off. "System" stamps nothing at all rather than
 * stamping the current OS preference: an attribute would freeze the answer at
 * the moment it was written, and the point of following the system is that it
 * keeps following when the system changes at dusk.
 *
 * Remembered through `storage.ts`, so a browser that refuses localStorage
 * still gets a theme — just not a remembered one.
 */

import { useCallback, useEffect, useState } from 'react'
import { readStored, writeStored } from '../storage'

export type ThemeChoice = 'light' | 'dark' | 'system'

export const THEME_CHOICES: readonly ThemeChoice[] = ['light', 'dark', 'system']

const STORAGE_KEY = 'cylist.theme'

function isChoice(value: unknown): value is ThemeChoice {
  return typeof value === 'string' && (THEME_CHOICES as readonly string[]).includes(value)
}

export function readTheme(): ThemeChoice {
  const stored = readStored(STORAGE_KEY)
  return isChoice(stored) ? stored : 'system'
}

function stamp(choice: ThemeChoice): void {
  const root = document.documentElement
  if (choice === 'system') root.removeAttribute('data-theme')
  else root.dataset.theme = choice
}

/**
 * Apply the remembered choice.
 *
 * Called from main.tsx before React mounts, so the first paint is already in
 * the right theme rather than flashing the light one on the way.
 */
export function applyStoredTheme(): void {
  stamp(readTheme())
}

/** The current choice, and a way to change it. */
export function useTheme(): { theme: ThemeChoice; setTheme: (choice: ThemeChoice) => void } {
  const [theme, setThemeState] = useState<ThemeChoice>(readTheme)

  // React's own render is not what puts the attribute on <html>, so re-apply
  // whenever the choice changes — including on mount, where it makes the state
  // and the document agree after a hydration or a fast refresh.
  useEffect(() => stamp(theme), [theme])

  const setTheme = useCallback((choice: ThemeChoice) => {
    writeStored(STORAGE_KEY, choice)
    setThemeState(choice)
  }, [])

  return { theme, setTheme }
}
