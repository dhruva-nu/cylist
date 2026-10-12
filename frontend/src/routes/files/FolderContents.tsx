/** The right-hand pane of the files screen: where the selected folder sits, and what is in it. */

import type { MouseEvent } from 'react'
import { api, type FileItem, type Folder, type ItemSource } from '../../api/client'
import { formatSize, formatStamp } from '../../components/format'
import { Avatar, Button, LevelTag, cardStyles } from '../../components/ui'
import { extensionOf } from './fileRename'
import styles from './ProjectFiles.module.css'

const SOURCE_LABELS: Record<ItemSource, string> = {
  upload: 'Uploaded',
  sharepoint: 'SharePoint',
  gdrive: 'Google Drive',
  other: 'Link',
}

/** Which coloured badge a filename gets, keyed by its extension. */
const BADGES: Record<string, string> = {
  pdf: 'pdf',
  doc: 'doc',
  docx: 'doc',
  md: 'doc',
  txt: 'doc',
  rtf: 'doc',
  html: 'doc',
  xls: 'xls',
  xlsx: 'xls',
  csv: 'xls',
  zip: 'zip',
  gz: 'zip',
  tar: 'zip',
  rar: 'zip',
  '7z': 'zip',
  png: 'img',
  jpg: 'img',
  jpeg: 'img',
  gif: 'img',
  svg: 'img',
  webp: 'img',
}

function badgeOf(name: string): { kind: string; label: string } {
  const extension = extensionOf(name)
  const kind = BADGES[extension]
  if (kind) return { kind, label: extension.slice(0, 4).toUpperCase() }
  return { kind: 'other', label: extension ? extension.slice(0, 3).toUpperCase() : 'FILE' }
}

/** The small ghost button a row's links are drawn as, so Open and Download match the buttons beside them. */
const ROW_LINK_CLASS = `${cardStyles.button} ${cardStyles.ghost} ${cardStyles.small} ${styles.action}`

/**
 * The right-hand pane: where the selected folder sits, and what is in it.
 *
 * The delete handlers are null where the reader's role does not allow files,
 * which takes the button off every row rather than leaving one that refuses.
 */
export function FolderContents({
  crumbs,
  folders,
  items,
  onOpenFolder,
  onDeleteFolder,
  onRenameItem,
  onDeleteItem,
}: {
  crumbs: string[]
  folders: Folder[]
  items: FileItem[]
  onOpenFolder: (folderId: string) => void
  onDeleteFolder: ((folder: Folder) => void) | null
  onRenameItem: ((item: FileItem) => void) | null
  onDeleteItem: ((item: FileItem) => void) | null
}) {
  return (
    <div className={styles.pane}>
      <div className={styles.paneHead}>
        <span className={styles.crumbs}>{crumbs.join(' / ')}</span>
        <span className={styles.tally}>
          {folders.length} folders · {items.length} files
        </span>
      </div>
      <div className={styles.scroll}>
        {folders.length + items.length === 0 ? (
          <div className={styles.blank}>
            This folder is empty.
            <span className={styles.hint}>Upload a file, add a link, or make a folder.</span>
          </div>
        ) : (
          <table className={styles.table} role="table">
            {/* The roles are stated because the rows are laid out as grids, and
                that drops the roles a table would otherwise have carried. */}
            <thead role="rowgroup">
              <tr role="row">
                <th role="columnheader">Name</th>
                <th role="columnheader">Source</th>
                <th role="columnheader">Size</th>
                <th role="columnheader">Added by</th>
                <th role="columnheader">Date</th>
                <th role="columnheader">
                  <span className={styles.offscreen}>Actions</span>
                </th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {folders.map((folder) => (
                <FolderRow
                  key={folder.id}
                  id={folder.id}
                  name={folder.name}
                  onOpen={onOpenFolder}
                  onDelete={onDeleteFolder ? () => onDeleteFolder(folder) : null}
                />
              ))}
              {items.map((item) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  onRename={onRenameItem ? () => onRenameItem(item) : null}
                  onDelete={onDeleteItem ? () => onDeleteItem(item) : null}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function FolderRow({
  id,
  name,
  onOpen,
  onDelete,
}: {
  id: string
  name: string
  onOpen: (id: string) => void
  /** Null where the reader's role does not allow files — no button, not a
      button that refuses. */
  onDelete: (() => void) | null
}) {
  return (
    // The whole row is clickable for the mouse, but a `tr` is not something
    // the keyboard can reach, so the name is a real button and that is what
    // Tab lands on. Both do the same thing.
    <tr className={styles.folderRow} role="row" onClick={() => onOpen(id)}>
      <td role="cell">
        <button
          type="button"
          className={`${styles.name} ${styles.openFolder}`}
          onClick={(event) => {
            event.stopPropagation()
            onOpen(id)
          }}
        >
          <span className={`${styles.badge} ${styles.folder}`} aria-hidden="true">
            ▸
          </span>
          {name}
        </button>
      </td>
      <td className={styles.muted} role="cell">
        Folder
      </td>
      <td className={styles.mono} role="cell">
        —
      </td>
      <td className={styles.muted} role="cell">
        —
      </td>
      <td className={styles.mono} role="cell">
        —
      </td>
      <td role="cell">
        <div className={styles.actions}>
          {onDelete ? (
            <DeleteAction
              onClick={(event) => {
                event.stopPropagation()
                onDelete()
              }}
            />
          ) : null}
        </div>
      </td>
    </tr>
  )
}

function ItemRow({
  item,
  onRename,
  onDelete,
}: {
  item: FileItem
  onRename: (() => void) | null
  onDelete: (() => void) | null
}) {
  const isLink = item.kind === 'link'
  const badge = isLink ? { kind: 'link', label: '↗' } : badgeOf(item.name)

  return (
    <tr role="row">
      <td role="cell">
        <div className={styles.name}>
          <span className={`${styles.badge} ${styles[badge.kind]}`}>{badge.label}</span>
          <span>
            {item.name}
            {/* Beside the name rather than in a column of its own: most rows
                carry no tag at all, and a column that is empty four times in
                five is a column nobody reads. */}
            <LevelTag level={item.sensitivity} />
            {item.url ? <div className={styles.url}>{item.url}</div> : null}
          </span>
        </div>
      </td>
      <td className={styles.muted} role="cell">
        {SOURCE_LABELS[item.source]}
      </td>
      <td className={styles.mono} role="cell">
        {formatSize(item.size)}
      </td>
      <td role="cell">
        {item.added_by ? (
          <span className={styles.who}>
            <Avatar name={item.added_by.name} colour={item.added_by.colour} />
            {item.added_by.name}
          </span>
        ) : (
          <span className={styles.muted}>—</span>
        )}
      </td>
      <td className={styles.mono} role="cell">
        {formatStamp(item.created_at)}
      </td>
      <td role="cell">
        <div className={styles.actions}>
          {isLink && item.url ? (
            <a className={ROW_LINK_CLASS} href={item.url} target="_blank" rel="noreferrer">
              Open
            </a>
          ) : (
            <a className={ROW_LINK_CLASS} href={api.downloadUrl(item.id)} download={item.name}>
              Download
            </a>
          )}
          {/* Not a `DeleteAction`: that one is red, and this is the action
              that undoes a bad name rather than one to hesitate over. */}
          {onRename ? (
            <Button variant="ghost" small onClick={onRename}>
              Rename
            </Button>
          ) : null}
          {onDelete ? <DeleteAction onClick={onDelete} /> : null}
        </div>
      </td>
    </tr>
  )
}

/** A row's Delete: small, ghosted, and red. */
function DeleteAction({ onClick }: { onClick: (event: MouseEvent<HTMLButtonElement>) => void }) {
  return (
    <Button variant="ghost" small danger onClick={onClick}>
      Delete
    </Button>
  )
}
