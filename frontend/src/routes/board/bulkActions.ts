/**
 * What the board does to several selected cards at once (CYLIST-202).
 *
 * There is no bulk endpoint: each card goes through the same request a single
 * card does, so every rule the server keeps — a reason for a hold, an open
 * sub-task blocking the last column — is kept here too, and a card it refuses
 * does not stop the rest. They are sent one after another rather than together
 * so that a move lands the cards in the order they were selected in.
 */

import { useMutation } from '@tanstack/react-query'
import { api, type Task, type TaskStatus } from '../../api/client'
import { refreshesItself } from '../../api/queries'

export type BulkOperation =
  | { kind: 'move'; columnId: string }
  | { kind: 'status'; status: Extract<TaskStatus, 'hold' | 'cancelled'>; reason: string }
  | { kind: 'delete' }

/** A card the server refused, and what it said. */
export interface BulkFailure {
  reference: string
  message: string
}

export interface BulkResult {
  done: number
  failed: BulkFailure[]
}

async function applyTo(task: Task, index: number, operation: BulkOperation): Promise<void> {
  switch (operation.kind) {
    case 'move':
      await api.moveTask(task.reference, operation.columnId, index)
      return
    case 'status':
      await api.setTaskStatus(task.reference, {
        status: operation.status,
        reason: operation.reason,
      })
      return
    case 'delete':
      await api.deleteTask(task.id)
      return
  }
}

/**
 * Whether the card is already as `operation` would leave it, and so is left
 * out rather than written to the timeline as a change that changed nothing.
 */
function alreadyDone(task: Task, operation: BulkOperation): boolean {
  switch (operation.kind) {
    case 'move':
      return task.column_id === operation.columnId
    case 'status':
      return task.status === operation.status
    case 'delete':
      return false
  }
}

export async function runBulk(tasks: Task[], operation: BulkOperation): Promise<BulkResult> {
  const result: BulkResult = { done: 0, failed: [] }
  const todo = tasks.filter((task) => !alreadyDone(task, operation))
  // Cards that need nothing still count as done: they are where they were asked to be.
  result.done = tasks.length - todo.length
  for (const [index, task] of todo.entries()) {
    try {
      await applyTo(task, index, operation)
      result.done += 1
    } catch (error) {
      result.failed.push({
        reference: task.reference,
        message: error instanceof Error ? error.message : String(error),
      })
    }
  }
  return result
}

/** One sentence for the screen reader and the notice: what happened to how many. */
export function summariseBulk(operation: BulkOperation, result: BulkResult): string {
  const verb = {
    move: 'moved',
    status:
      operation.kind === 'status' && operation.status === 'hold' ? 'put on hold' : 'cancelled',
    delete: 'deleted',
  }[operation.kind]
  const cards = (n: number) => `${n} ${n === 1 ? 'card' : 'cards'}`
  const said = `${cards(result.done)} ${verb}.`
  if (!result.failed.length) return said
  const named = result.failed.map((f) => `${f.reference}: ${f.message}`).join('; ')
  return `${said} ${cards(result.failed.length)} refused — ${named}`
}

/** `refresh` is the board's own, run once at the end rather than after every card. */
export function useBulkActions(refresh: () => Promise<void>) {
  return useMutation<BulkResult, Error, { tasks: Task[]; operation: BulkOperation }>({
    mutationFn: ({ tasks, operation }) => runBulk(tasks, operation),
    onSettled: () => refresh(),
    meta: refreshesItself,
  })
}
