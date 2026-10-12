import type { Task } from '../../api/client'
import { Modal, ModalBody } from '../../components/Modal'
import { Button } from '../../components/ui'
import type { Move } from './useBoardMutations'
import styles from './ReopenDialog.module.css'

/** A held drop: the move it would make, and the two columns it reads between. */
export interface Reopening {
  move: Move
  task: Task
  from: string
  to: string
}

/**
 * Asks before a finished card goes back on the board.
 *
 * Worded as what it will do rather than as a warning, because reopening a card
 * is a perfectly ordinary thing to want: work comes back. What the reader is
 * being told is that it will be written down.
 */
export function ReopenDialog({
  reopening,
  onClose,
  onConfirm,
}: {
  reopening: Reopening
  onClose: () => void
  onConfirm: () => void
}) {
  const { task, from, to } = reopening
  return (
    <Modal
      title={`Reopen ${task.reference}?`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Leave it in {from}</Button>
          <Button variant="go" onClick={onConfirm}>
            Reopen it
          </Button>
        </>
      }
    >
      <ModalBody>
        <p className={styles.confirm}>
          <b>{task.title}</b> is finished — it is in {from}, which is the end of this board. Moving
          it to {to} puts it back to work: it stops counting as done, and its history records that
          it was reopened.
        </p>
      </ModalBody>
    </Modal>
  )
}
