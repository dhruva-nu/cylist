/**
 * Short-lived messages about something that went wrong out of sight.
 *
 * A store of its own rather than component state, because the thing that has
 * a failure to report — the mutation cache's `onError` — is not in the React
 * tree. `Toasts` is where they are drawn; anything may `showToast`.
 */

import { useSyncExternalStore } from 'react'

export interface Toast {
  id: number
  text: string
}

/** How long a toast stays when nobody dismisses it. Long enough to read a sentence twice. */
export const TOAST_MS = 8000

/** The most that are kept at once; a burst of failures replaces the oldest rather than burying the screen. */
const MAX_TOASTS = 4

let toasts: readonly Toast[] = []
let nextId = 1
const listeners = new Set<() => void>()
const timers = new Map<number, ReturnType<typeof setTimeout>>()

function publish(next: readonly Toast[]): void {
  toasts = next
  listeners.forEach((listener) => listener())
}

/** Show `text`, unless the same words are already on screen, and return the toast's id. */
export function showToast(text: string): number {
  const existing = toasts.find((toast) => toast.text === text)
  if (existing) return existing.id
  const id = nextId++
  const kept = [...toasts, { id, text }].slice(-MAX_TOASTS)
  for (const dropped of toasts) {
    if (!kept.some((toast) => toast.id === dropped.id)) clearTimer(dropped.id)
  }
  publish(kept)
  timers.set(
    id,
    setTimeout(() => dismissToast(id), TOAST_MS),
  )
  return id
}

export function dismissToast(id: number): void {
  clearTimer(id)
  publish(toasts.filter((toast) => toast.id !== id))
}

function clearTimer(id: number): void {
  const timer = timers.get(id)
  if (timer !== undefined) clearTimeout(timer)
  timers.delete(id)
}

/** Forget every toast and timer. For tests, which share one module between cases. */
export function clearToasts(): void {
  timers.forEach((timer) => clearTimeout(timer))
  timers.clear()
  publish([])
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** What is on screen now. */
export function currentToasts(): readonly Toast[] {
  return toasts
}

export function useToasts(): readonly Toast[] {
  return useSyncExternalStore(subscribe, currentToasts)
}
