import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type BoardColumn, type Task } from '../../api/client'
import { useTasks } from '../../api/queries'
import { Field, Modal, ModalBody } from '../../components/Modal'
import { Button, EmptyState, ErrorNotice } from '../../components/ui'
import { cardMatches } from './cardMatch'
import styles from './GoalPage.module.css'

/**
 * Puts cards that are already on the board onto this goal.
 *
 * Only cards on no goal at all are offered. Moving a card between goals is a
 * thing you do on the card, where you can see what you are taking it off; a
 * list that silently stole cards from another epic would be a list nobody
 * could use quickly, which is the only reason to have it.
 */
export function LinkCardsDialog({
  projectKey,
  goalId,
  goalName,
  columns,
  announce,
  onDone,
  onClose,
}: {
  projectKey: string
  goalId: string
  goalName: string
  columns: BoardColumn[]
  announce: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [searchText, setSearchText] = useState('')
  const tasks = useTasks(projectKey)

  const link = useMutation({
    mutationFn: (task: Task) => api.updateTask(task.reference, { goal_id: goalId }),
    onSuccess: async (task) => {
      await onDone()
      announce(`${task.reference} put on ${goalName}.`)
    },
  })

  const searchWord = searchText.trim().toLowerCase()
  const columnName = (id: string | null) => columns.find((column) => column.id === id)?.name ?? ''
  const offered = (tasks.data ?? [])
    .filter((task) => task.goal_id === null)
    .filter((task) => cardMatches(task, searchWord))

  return (
    <Modal
      title={`Link cards to ${goalName}`}
      onClose={onClose}
      footer={<Button onClick={onClose}>Done</Button>}
    >
      <ModalBody>
        <ErrorNotice error={link.error} />
        <Field label="Find a card" hint="Cards already on another goal are not offered here.">
          <input
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            placeholder="ATL-41, or part of a title"
          />
        </Field>

        {tasks.isPending ? <EmptyState>Loading the board…</EmptyState> : null}
        {tasks.data && offered.length === 0 ? (
          <EmptyState>
            {searchWord ? 'No card matches that.' : 'Every card on the board is already on a goal.'}
          </EmptyState>
        ) : null}

        <ul className={styles.offered}>
          {offered.map((task) => (
            <li key={task.id}>
              <button
                type="button"
                disabled={link.isPending}
                onClick={() => link.mutate(task)}
                className={styles.offer}
              >
                <span className={styles.cardRef}>{task.reference}</span>
                <span className={styles.cardTitle}>{task.title}</span>
                <span className={styles.offerColumn}>{columnName(task.column_id)}</span>
                <span aria-hidden="true">+</span>
              </button>
            </li>
          ))}
        </ul>
      </ModalBody>
    </Modal>
  )
}
