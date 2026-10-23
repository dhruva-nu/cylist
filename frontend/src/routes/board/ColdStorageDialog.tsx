import { useInfiniteQuery, useMutation } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { api, type BoardColumn, type ColdStorageRun, type Task } from '../../api/client'
import { queryKeys, useInvalidate } from '../../api/queries'
import { formatStamp } from '../../components/format'
import { Modal, ModalBody } from '../../components/Modal'
import { Avatar, Button, EmptyState, ErrorNotice } from '../../components/ui'
import styles from './ColdStorageDialog.module.css'
import { handlesOwnErrors } from '../../api/mutationErrors'

/**
 * Cold storage (CYLIST-70): where the board's finished cards go once nobody
 * needs to see them, and the way back for any one of them.
 *
 * Two halves, because they are the two directions. The top empties the last
 * column into a CSV in the project's files; the list underneath is everything
 * stored so far, each with a Restore. The two are one dialog rather than a
 * button and a page, because the question "where did that card go?" is asked by
 * whoever just pressed the button, and the answer should be beside it.
 *
 * Worded as what it will do rather than as a warning: nothing here deletes
 * anything. A stored card still opens by its reference and still counts
 * towards its goal; the CSV is the readable record of it, not the only one.
 */
export function ColdStorageDialog({
  projectKey,
  column,
  cardCount,
  mayStore,
  mayRestore,
  announce,
  onDone,
  onOpenTask,
  onClose,
}: {
  projectKey: string
  /** The board's last column, which is what gets emptied. */
  column: BoardColumn
  /** How many cards are in it now, whatever the board's filters are hiding. */
  cardCount: number
  /** Whether the reader's role allows both cards and files, which storing needs. */
  mayStore: boolean
  /** Whether the reader's role allows cards, which restoring one needs. */
  mayRestore: boolean
  announce: (message: string) => void
  onDone: () => Promise<void>
  onOpenTask: (taskId: string) => void
  onClose: () => void
}) {
  const invalidate = useInvalidate()
  // A page at a time, most recently finished first: cold storage only grows,
  // and the cards worth finding in it are nearly always the latest.
  const stored = useInfiniteQuery({
    queryKey: queryKeys.coldStorage(projectKey),
    queryFn: ({ pageParam, signal }) => api.listColdStorage(projectKey, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next,
  })

  const store = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.storeFinished(projectKey),
    onSuccess: async (run) => {
      announce(`${cardsWord(run.references.length)} moved to cold storage, in ${run.file.name}.`)
      await Promise.all([
        onDone(),
        // The CSV is a new file, and the files page may be cached from before.
        invalidate(queryKeys.folderTree(projectKey), queryKeys.allFolderChildren),
      ])
    },
  })

  const restore = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (task: Task) => api.restoreTask(task.reference),
    onSuccess: async (restored) => {
      announce(`${restored.reference} restored to ${column.name}.`)
      await onDone()
    },
  })

  const cards = stored.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <Modal
      wide
      title="Cold storage"
      onClose={onClose}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <ModalBody>
        <ErrorNotice error={store.error ?? restore.error ?? stored.error} />

        <section className={styles.store} aria-label={`Store ${column.name}`}>
          <p className={styles.lead}>
            Take every card in <b>{column.name}</b> off the board and write them to a CSV in{' '}
            <Link to="/p/$projectKey/files" params={{ projectKey }} onClick={onClose}>
              Files › Cold storage
            </Link>
            . Nothing is deleted: each card still opens by its reference, still counts towards its
            goal, and can be restored from the list below.
          </p>
          <div className={styles.storeRow}>
            <Button
              variant="go"
              disabled={!mayStore || cardCount === 0 || store.isPending}
              onClick={() => store.mutate()}
            >
              {store.isPending
                ? 'Storing…'
                : cardCount === 0
                  ? `${column.name} is empty`
                  : `Move ${cardsWord(cardCount).toLowerCase()} to cold storage`}
            </Button>
            {mayStore ? null : (
              <span className={styles.note}>
                Your role needs both cards and files on this board to do this.
              </span>
            )}
          </div>
          {store.data ? <Stored run={store.data} /> : null}
        </section>

        <h3 className={styles.heading}>
          Stored cards
          {cards.length ? (
            <span className={styles.count}>
              {cards.length}
              {stored.hasNextPage ? '+' : ''}
            </span>
          ) : null}
        </h3>
        {stored.isPending ? (
          <EmptyState>Loading…</EmptyState>
        ) : cards.length === 0 ? (
          <EmptyState>Nothing in cold storage yet.</EmptyState>
        ) : (
          <ul className={styles.list}>
            {cards.map((task) => (
              <li key={task.id} className={styles.row}>
                <span className={styles.reference}>{task.reference}</span>
                <button
                  type="button"
                  className={styles.title}
                  onClick={() => onOpenTask(task.id)}
                  title={`Open ${task.reference}`}
                >
                  {task.title}
                </button>
                <span className={styles.meta}>
                  <Avatar name={task.assignee.name} colour={task.assignee.colour} />
                  {task.archived_at ? (
                    <span title={new Date(task.archived_at).toLocaleString()}>
                      stored {formatStamp(task.archived_at)}
                    </span>
                  ) : null}
                </span>
                {mayRestore ? (
                  <Button
                    small
                    disabled={restore.isPending}
                    onClick={() => restore.mutate(task)}
                    aria-label={`Restore ${task.reference} to ${column.name}`}
                  >
                    Restore
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {stored.hasNextPage ? (
          <div className={styles.more}>
            <Button
              small
              disabled={stored.isFetchingNextPage}
              onClick={() => void stored.fetchNextPage()}
            >
              {stored.isFetchingNextPage ? 'Loading…' : 'Show older cards'}
            </Button>
          </div>
        ) : null}
      </ModalBody>
    </Modal>
  )
}

/** What the last press did, with the file it wrote one click away. */
function Stored({ run }: { run: ColdStorageRun }) {
  return (
    <p className={styles.done} role="status">
      {cardsWord(run.references.length)} moved to cold storage, in{' '}
      <a href={api.downloadUrl(run.file.id)} download>
        {run.file.name}
      </a>
      .
    </p>
  )
}

function cardsWord(count: number): string {
  return count === 1 ? '1 card' : `${count} cards`
}
