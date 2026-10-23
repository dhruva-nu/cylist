/**
 * A project's vault.
 *
 * Trees down the left, whatever is selected on the right. The rule the whole
 * screen is built around: **a plaintext secret is never in the page until you
 * ask for it.** The tree endpoint returns metadata only, and what `Reveal`
 * brings back is held by `SecretDetail` alone — see there.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type VaultNode } from '../../api/client'
import { useInvalidate } from '../../api/queries'
import { PageHead } from '../../components/Shell'
import {
  Button,
  EmptyState,
  ErrorBanner,
  ErrorNotice,
  LiveRegion,
  useAnnouncer,
} from '../../components/ui'
import { usePermissions } from '../usePermissions'
import { NodeDetail, NothingSelected } from './NodeDetail'
import styles from './ProjectVault.module.css'
import { MoveDialog, NodeDialog, TreeDialog } from './VaultDialogs'
import { TreeSection } from './VaultTreeNav'
import { findNode, toggleId, withId, type Destination } from './vaultTree'
import { handlesOwnErrors } from '../../api/mutationErrors'

export function ProjectVault() {
  const { projectKey } = useParams({ from: '/p/$projectKey/vault' })
  const may = usePermissions(projectKey)
  const invalidate = useInvalidate()
  const { message, announce } = useAnnouncer()

  /** Which trees are open. Null until the reader opens or closes one — see below. */
  const [chosenOpenTrees, setChosenOpenTrees] = useState<string[] | null>(null)
  const [openNodes, setOpenNodes] = useState<string[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [namingTree, setNamingTree] = useState(false)
  const [addingAt, setAddingAt] = useState<Destination | null>(null)
  const [editing, setEditing] = useState<VaultNode | null>(null)
  const [moving, setMoving] = useState<VaultNode | null>(null)

  const trees = useQuery({
    queryKey: ['vault-trees', projectKey],
    queryFn: ({ signal }) => api.listVaultTrees(projectKey, signal),
  })

  const treeDetails = useQuery({
    queryKey: ['vault-tree-details', projectKey, (trees.data ?? []).map((t) => t.id).join(',')],
    queryFn: ({ signal }) =>
      Promise.all((trees.data ?? []).map((tree) => api.getVaultTree(tree.id, signal))),
    enabled: trees.data !== undefined,
  })

  // The first tree starts open, as in the mock: an empty left rail teaches
  // nobody what a tree is. Worked out here rather than written into state by
  // an effect, so it is open on the very first paint the trees arrive in.
  const firstTreeId = trees.data?.[0]?.id
  const openTrees = chosenOpenTrees ?? (firstTreeId ? [firstTreeId] : [])

  async function refresh() {
    await invalidate(['vault-trees', projectKey], ['vault-tree-details', projectKey])
  }

  const deleteNode = useMutation({
    meta: handlesOwnErrors,
    // The node rather than its id, so the announcement can name what has gone.
    mutationFn: (node: VaultNode) => api.deleteVaultNode(node.id),
    onSuccess: async (_deleted, node) => {
      setSelectedId(null)
      announce(`Deleted ${node.name}.`)
      await refresh()
    },
  })

  if (trees.isPending || treeDetails.isPending) return <EmptyState>Loading the vault…</EmptyState>
  if (trees.error) return <ErrorBanner>{trees.error.message}</ErrorBanner>
  if (treeDetails.error) return <ErrorBanner>{treeDetails.error.message}</ErrorBanner>

  const detailedTrees = treeDetails.data
  const selected = selectedId ? findNode(detailedTrees, selectedId) : null

  function select(node: VaultNode) {
    setSelectedId(node.id)
    if (node.kind === 'branch') setOpenNodes((current) => toggleId(current, node.id))
  }

  /** Expand or collapse one branch. The arrow keys need to say which, where a
   * click only ever means "the other one". */
  function setBranchOpen(nodeId: string, open: boolean) {
    setOpenNodes((current) =>
      open ? withId(current, nodeId) : current.filter((other) => other !== nodeId),
    )
  }

  /** After a dialog has changed something: refetch, then say what changed. */
  async function refreshAndSay(said: string) {
    await refresh()
    announce(said)
  }

  return (
    <>
      <PageHead
        title="Vault"
        actions={
          may('vault') ? (
            <Button variant="go" onClick={() => setNamingTree(true)}>
              + New tree
            </Button>
          ) : null
        }
      >
        Multiple trees, each as deep as you like. Secrets stay masked until you reveal them, and
        every reveal is written to the activity log.
      </PageHead>

      <LiveRegion message={message} />

      <ErrorNotice error={deleteNode.error} />

      {detailedTrees.length === 0 ? (
        <EmptyState>
          No trees yet. Start one — “Logins”, “Certificates &amp; keys”, whatever this project
          needs.
        </EmptyState>
      ) : (
        <div className={styles.split}>
          <div className={styles.trees}>
            {detailedTrees.map((tree) => (
              <TreeSection
                key={tree.id}
                tree={tree}
                open={openTrees.includes(tree.id)}
                onToggleOpen={() => setChosenOpenTrees(toggleId(openTrees, tree.id))}
                selectedId={selectedId}
                openNodes={openNodes}
                onSelect={select}
                onSetBranchOpen={setBranchOpen}
                onAddNode={
                  may('vault') ? () => setAddingAt({ treeId: tree.id, parentId: null }) : null
                }
              />
            ))}
          </div>

          <div className={styles.pane}>
            {selected ? (
              <NodeDetail
                node={selected.node}
                path={selected.path}
                announce={announce}
                onAdd={(kind) =>
                  setAddingAt({ treeId: selected.node.tree_id, parentId: selected.node.id, kind })
                }
                onSelect={select}
                mayChange={may('vault')}
                mayReveal={may('vault_reveal')}
                onEdit={() => setEditing(selected.node)}
                onMove={() => setMoving(selected.node)}
                onDelete={() => deleteNode.mutate(selected.node)}
              />
            ) : (
              <NothingSelected />
            )}
          </div>
        </div>
      )}

      {namingTree ? (
        <TreeDialog
          projectKey={projectKey}
          onDone={(name) => refreshAndSay(`Created the ${name} tree.`)}
          onClose={() => setNamingTree(false)}
        />
      ) : null}

      {addingAt ? (
        <NodeDialog
          projectKey={projectKey}
          destination={addingAt}
          onCreated={(created) => {
            setChosenOpenTrees(withId(openTrees, created.tree_id))
            if (created.parent_id) setBranchOpen(created.parent_id, true)
            setSelectedId(created.id)
          }}
          onDone={(name) => refreshAndSay(`Added ${name}.`)}
          onClose={() => setAddingAt(null)}
        />
      ) : null}

      {editing ? (
        <NodeDialog
          projectKey={projectKey}
          node={editing}
          destination={{ treeId: editing.tree_id, parentId: editing.parent_id }}
          onDone={(name) => refreshAndSay(`Saved ${name}.`)}
          onClose={() => setEditing(null)}
        />
      ) : null}

      {moving ? (
        <MoveDialog
          node={moving}
          tree={detailedTrees.find((tree) => tree.id === moving.tree_id)}
          onDone={(name) => refreshAndSay(`Moved ${name}.`)}
          onClose={() => setMoving(null)}
        />
      ) : null}
    </>
  )
}
