/** The right-hand pane of the vault: whatever node is selected, or a word on how to pick one. */

import type { VaultNode, VaultNodeKind } from '../../api/client'
import { Button, Eyebrow, LevelTag, cardStyles } from '../../components/ui'
import styles from './ProjectVault.module.css'
import { SecretDetail } from './SecretDetail'
import { LockIcon, NodeIcon } from './vaultIcons'

/** The right-hand pane before anything has been picked from a tree. */
export function NothingSelected() {
  return (
    <div className={styles.blank}>
      <div>
        <LockIcon />
        <br />
        Select a node to see what is inside.
        <br />
        <span className={styles.hint}>
          Branches hold more nodes; secrets hold a login, key or link.
        </span>
      </div>
    </div>
  )
}

export function NodeDetail({
  node,
  path,
  announce,
  mayChange,
  mayReveal,
  onAdd,
  onSelect,
  onEdit,
  onMove,
  onDelete,
}: {
  node: VaultNode
  path: string[]
  announce: (message: string) => void
  /** Whether the reader's role allows changing the vault. Revealing a secret
      is a separate permission, and the reveal control answers to that one. */
  mayChange: boolean
  /** Whether the reader's role allows reading a stored secret. */
  mayReveal: boolean
  onAdd: (kind: VaultNodeKind) => void
  onSelect: (node: VaultNode) => void
  onEdit: () => void
  onMove: () => void
  onDelete: () => void
}) {
  return (
    <div className={styles.detail}>
      <div className={styles.detailHead}>
        <div>
          <div className={styles.path}>{path.slice(0, -1).join(' / ')}</div>
          <h2>
            {node.name} <LevelTag level={node.sensitivity} />
          </h2>
          {node.kind === 'branch' ? (
            <p className={styles.note}>
              Branch with {node.children.length} child{node.children.length === 1 ? '' : 'ren'}.
            </p>
          ) : null}
        </div>
        {mayChange ? (
          <div className={styles.detailActions}>
            {node.kind === 'branch' ? (
              <>
                <Button small onClick={() => onAdd('branch')}>
                  + Branch
                </Button>
                <Button small variant="go" onClick={() => onAdd('secret')}>
                  + Secret
                </Button>
              </>
            ) : (
              <Button small onClick={onEdit}>
                Edit
              </Button>
            )}
            <Button small variant="ghost" onClick={onMove}>
              Move
            </Button>
            <Button small variant="ghost" danger onClick={onDelete}>
              Delete
            </Button>
          </div>
        ) : null}
      </div>

      {node.kind === 'secret' ? (
        <div className={styles.reading}>
          {/* Keyed by the node, so moving the selection throws away whatever
              the last secret had revealed before anything else is drawn —
              not a render later, with the old plaintext under the new name. */}
          <SecretDetail key={node.id} node={node} announce={announce} mayReveal={mayReveal} />
        </div>
      ) : (
        <div className={styles.cards}>
          {node.children.map((child) => (
            <button
              key={child.id}
              type="button"
              className={`${cardStyles.card} ${cardStyles.clickable} ${styles.childCard}`}
              onClick={() => onSelect(child)}
            >
              <span className={styles.childKind}>
                <NodeIcon kind={child.kind} />
                <Eyebrow>{child.kind}</Eyebrow>
              </span>
              <b>{child.name}</b>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
