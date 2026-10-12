/** The left-hand pane of the files screen: the folder tree, and the drop target under it. */

import { useState, type DragEvent, type KeyboardEvent, type MutableRefObject } from 'react'
import { FolderIcon } from '../../components/ui'
import styles from './ProjectFiles.module.css'
import type { TreeRow } from './useFolderTree'

/** The folder tree as the left-hand pane draws it: one flat list of rows. */
export function FolderTree({
  rows,
  selectedId,
  focusedId,
  expanded,
  nodeRefs,
  onSelect,
  onFocus,
  onKeyDown,
}: {
  rows: TreeRow[]
  selectedId: string | null
  focusedId: string | null
  expanded: Set<string>
  nodeRefs: MutableRefObject<Map<string, HTMLDivElement>>
  onSelect: (folderId: string) => void
  onFocus: (folderId: string) => void
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void
}) {
  return (
    <div className={styles.tree} role="tree" aria-label="Folders" onKeyDown={onKeyDown}>
      {rows.map(({ node, level, position, siblings }) => (
        <div
          key={node.id}
          ref={(element) => {
            if (element) nodeRefs.current.set(node.id, element)
            else nodeRefs.current.delete(node.id)
          }}
          role="treeitem"
          aria-level={level}
          aria-posinset={position}
          aria-setsize={siblings}
          aria-selected={selectedId === node.id}
          aria-expanded={node.children.length ? expanded.has(node.id) : undefined}
          tabIndex={focusedId === node.id ? 0 : -1}
          className={[
            styles.node,
            selectedId === node.id && styles.on,
            expanded.has(node.id) && styles.open,
          ]
            .filter(Boolean)
            .join(' ')}
          // Depth is drawn rather than nested: a flat list of rows is
          // what the arrow keys walk, and aria-level carries the shape.
          style={{ paddingLeft: 10 + (level - 1) * 16 }}
          onClick={() => onSelect(node.id)}
          onFocus={() => onFocus(node.id)}
        >
          <span className={styles.caret} aria-hidden="true">
            {node.children.length ? '▶' : ''}
          </span>
          <FolderIcon />
          <span>{node.name}</span>
        </div>
      ))}
    </div>
  )
}

/** The folder pane doubles as a drop target, as the mock promises it does. */
export function DropZone({
  folderName,
  busy,
  onFiles,
}: {
  folderName: string
  busy: boolean
  onFiles: (files: File[]) => void
}) {
  const [over, setOver] = useState(false)

  function accept(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setOver(false)
    const files = Array.from(event.dataTransfer.files)
    if (files.length) onFiles(files)
  }

  return (
    <div
      className={`${styles.drop} ${over ? styles.dropActive : ''}`}
      onDragOver={(event) => {
        event.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={accept}
    >
      {busy ? (
        'Uploading…'
      ) : (
        <>
          Drop files here to upload into <b>{folderName}</b>
        </>
      )}
    </div>
  )
}
