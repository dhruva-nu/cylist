/**
 * The bar that appears while cards are selected (CYLIST-202): what can be done
 * to all of them at once.
 *
 * A hold or a cancel asks why, because the server will not take one without a
 * reason; a delete asks first, because it cannot be taken back. A move names
 * the column and nothing else — the cards land at its top in the order they
 * were selected in.
 */

import { useState } from 'react'
import type { BoardColumn } from '../../api/client'
import { Modal, ModalBody } from '../../components/Modal'
import { Button } from '../../components/ui'
import type { BulkOperation } from './bulkActions'
import styles from './BulkBar.module.css'

type Asking = 'hold' | 'cancel' | 'delete' | null

export function BulkBar({
  count,
  columns,
  busy,
  onRun,
  onClear,
}: {
  count: number
  columns: BoardColumn[]
  busy: boolean
  onRun: (operation: BulkOperation) => void
  onClear: () => void
}) {
  const [asking, setAsking] = useState<Asking>(null)
  const [reason, setReason] = useState('')
  const cards = `${count} ${count === 1 ? 'card' : 'cards'}`

  const close = () => {
    setAsking(null)
    setReason('')
  }

  return (
    <>
      <div className={styles.bar} role="toolbar" aria-label={`${cards} selected`}>
        <span className={styles.count}>{cards} selected</span>
        <label className={styles.move}>
          <span>Move to</span>
          <select
            disabled={busy}
            value=""
            onChange={(event) => {
              if (event.target.value) onRun({ kind: 'move', columnId: event.target.value })
            }}
          >
            <option value="">Column…</option>
            {columns.map((column) => (
              <option key={column.id} value={column.id}>
                {column.name.trim()}
              </option>
            ))}
          </select>
        </label>
        <Button small disabled={busy} onClick={() => setAsking('hold')}>
          Hold
        </Button>
        <Button small disabled={busy} onClick={() => setAsking('cancel')}>
          Cancel
        </Button>
        <Button small danger disabled={busy} onClick={() => setAsking('delete')}>
          Delete
        </Button>
        <Button small variant="ghost" onClick={onClear}>
          Clear
        </Button>
      </div>

      {asking === 'hold' || asking === 'cancel' ? (
        <Modal
          title={`${asking === 'hold' ? 'Put on hold' : 'Cancel'} ${cards}?`}
          onClose={close}
          footer={
            <>
              <Button onClick={close}>Back</Button>
              <Button
                variant="go"
                disabled={!reason.trim()}
                onClick={() => {
                  onRun({
                    kind: 'status',
                    status: asking === 'hold' ? 'hold' : 'cancelled',
                    reason: reason.trim(),
                  })
                  close()
                }}
              >
                {asking === 'hold' ? 'Put on hold' : 'Cancel them'}
              </Button>
            </>
          }
        >
          <ModalBody>
            <label className={styles.why}>
              <span>Why? It is written on each card&apos;s timeline.</span>
              <textarea
                autoFocus
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
          </ModalBody>
        </Modal>
      ) : null}

      {asking === 'delete' ? (
        <Modal
          title={`Delete ${cards}?`}
          onClose={close}
          footer={
            <>
              <Button onClick={close}>Keep them</Button>
              <Button
                variant="go"
                danger
                onClick={() => {
                  onRun({ kind: 'delete' })
                  close()
                }}
              >
                Delete {cards}
              </Button>
            </>
          }
        >
          <ModalBody>
            <p className={styles.confirm}>
              This removes {cards} and their sub-tasks, comments and history. It cannot be undone.
              If the work is simply not going ahead, cancel them instead.
            </p>
          </ModalBody>
        </Modal>
      ) : null}
    </>
  )
}
