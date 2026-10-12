import type { Task } from '../../api/client'

/**
 * Whether a card is one somebody typing `word` is looking for: its reference
 * or its title contains it. `word` is already trimmed and lower-cased, and an
 * empty one matches every card — nothing typed is nothing filtered.
 */
export function cardMatches(task: Pick<Task, 'reference' | 'title'>, word: string): boolean {
  return !word || `${task.reference} ${task.title}`.toLowerCase().includes(word)
}
