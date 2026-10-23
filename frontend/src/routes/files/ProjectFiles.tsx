/**
 * A project's files.
 *
 * A folder tree on the left, what the selected folder holds on the right.
 * Uploads and links share the table because they answer the same question —
 * "where is the thing about X?" — and it matters far less whether the bytes are
 * ours than whether you can find them.
 *
 * The project's root is a folder like any other: it is selected when the screen
 * opens, it takes uploads and links directly, and it is drawn by the same code
 * as every folder under it. Nothing here has to special-case "no folder chosen"
 * because there is no such state to be in.
 */

import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type FileItem, type Sensitivity } from '../../api/client'
import { useInvalidate, useProject } from '../../api/queries'
import { projectFilesKey } from '../../components/projectFiles'
import { PageHead } from '../../components/Shell'
import { Button, ErrorBanner, ErrorNotice, useAnnouncer } from '../../components/ui'
import { usePermissions } from '../usePermissions'
import { LinkDialog, NewFolderDialog, RenameDialog, UploadDialog } from './FileDialogs'
import { FolderContents } from './FolderContents'
import { DropZone, FolderTree } from './FolderTree'
import styles from './ProjectFiles.module.css'
import { useFolderTree } from './useFolderTree'

/** A file or folder as a mutation needs it: what to call, and what to say it was. */
interface Named {
  id: string
  name: string
}

/**
 * Deleting takes the content off the server's disk, and nothing here undoes
 * it — the one place in Cylist where a stray click really does lose data.
 */
function confirmDelete(what: string): boolean {
  return window.confirm(`Delete ${what}? This cannot be undone.`)
}

export function ProjectFiles() {
  const { projectKey } = useParams({ from: '/p/$projectKey/files' })
  const may = usePermissions(projectKey)
  const invalidate = useInvalidate()

  const [dialog, setDialog] = useState<'upload' | 'link' | 'folder' | null>(null)
  // The item being renamed, rather than a fourth `dialog` value: the dialog is
  // about one row, and it is the row it needs, not the fact that it is open.
  const [renaming, setRenaming] = useState<FileItem | null>(null)
  const { announce } = useAnnouncer()

  const project = useProject(projectKey)
  const tree = useQuery({
    queryKey: ['folder-tree', projectKey],
    queryFn: ({ signal }) => api.getTree(projectKey, signal),
  })

  const folderTree = useFolderTree(tree.data ?? null)
  const { selectedId, selectFolder } = folderTree

  // The subfolders whole and the files a page at a time: a folder of
  // thousands of exports should open on its first hundred, not hang.
  const pages = useInfiniteQuery({
    queryKey: ['folder-children', selectedId],
    queryFn: ({ pageParam, signal }) =>
      api.getFolderChildren(selectedId as string, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next,
    enabled: selectedId !== null,
  })
  const contents = {
    error: pages.error,
    data: pages.data?.pages[0]
      ? {
          ...pages.data.pages[0],
          items: pages.data.pages.flatMap((page) => page.items),
        }
      : undefined,
  }

  async function refresh() {
    await invalidate(
      ['folder-tree', projectKey],
      ['folder-children'],
      // What the `>` tag in a description or a comment is completed from: a
      // file uploaded here is taggable in the next thing anybody writes.
      projectFilesKey(projectKey),
    )
  }

  async function refreshAndSay(said: string) {
    await refresh()
    announce(said)
  }

  const folderName = contents.data?.folder.name ?? project.data?.name ?? projectKey

  const upload = useMutation({
    mutationFn: async ({
      files,
      addedBy,
      level,
    }: {
      files: File[]
      addedBy: string | null
      level: Sensitivity | null
    }) => {
      if (selectedId === null) throw new Error('The folder tree has not finished loading.')
      // One at a time: two uploads of the same name in one folder race for it,
      // and the loser's 409 is clearer when it is the only thing that failed.
      for (const file of files) await api.uploadFile(selectedId, file, addedBy, level)
      return files.length
    },
    onSuccess: (count) =>
      refreshAndSay(`${count} ${count === 1 ? 'file' : 'files'} uploaded to ${folderName}.`),
  })

  const deleteItem = useMutation({
    mutationFn: (item: Named) => api.deleteItem(item.id),
    onSuccess: (_deleted, item) => refreshAndSay(`${item.name} deleted.`),
  })

  const deleteFolder = useMutation({
    mutationFn: (folder: Named) => api.deleteFolder(folder.id),
    onSuccess: (_deleted, folder) => refreshAndSay(`Folder ${folder.name} deleted.`),
  })

  const crumbs = contents.data?.path.map((crumb) => crumb.name) ?? [
    project.data?.name ?? projectKey,
  ]

  if (tree.error) return <ErrorBanner>{tree.error.message}</ErrorBanner>

  const mayChangeFiles = may('files')

  return (
    <>
      <PageHead
        title="Files"
        actions={
          mayChangeFiles ? (
            <>
              {/* Disabled only while the tree is still on its way: every folder
                  it can arrive at, the root included, takes files. Absent
                  entirely for a role that is not allowed files at all. */}
              <Button
                variant="go"
                disabled={selectedId === null}
                onClick={() => setDialog('upload')}
              >
                ↑ Upload files
              </Button>
              <Button disabled={selectedId === null} onClick={() => setDialog('link')}>
                + Add a link
              </Button>
              <Button disabled={selectedId === null} onClick={() => setDialog('folder')}>
                + New folder
              </Button>
            </>
          ) : null
        }
      >
        Files upload to the server. Links to SharePoint or Google Drive sit in the same folders.
      </PageHead>
      <ErrorNotice error={upload.error ?? deleteItem.error ?? deleteFolder.error} />
      <ErrorNotice error={contents.error} />

      <div className={styles.split}>
        <div className={styles.pane}>
          <div className={styles.paneHead}>Folders</div>
          <FolderTree
            rows={folderTree.rows}
            selectedId={selectedId}
            focusedId={folderTree.focusedId}
            expanded={folderTree.expanded}
            nodeRefs={folderTree.nodeRefs}
            onSelect={selectFolder}
            onFocus={folderTree.setFocused}
            onKeyDown={folderTree.onTreeKeyDown}
          />
          <DropZone
            folderName={folderName}
            busy={upload.isPending}
            onFiles={(files) => upload.mutate({ files, addedBy: null, level: null })}
          />
        </div>

        <FolderContents
          crumbs={crumbs}
          folders={contents.data?.folders ?? []}
          items={contents.data?.items ?? []}
          more={
            pages.hasNextPage
              ? { loading: pages.isFetchingNextPage, onLoad: () => void pages.fetchNextPage() }
              : null
          }
          onOpenFolder={selectFolder}
          onDeleteFolder={
            mayChangeFiles
              ? (folder) => {
                  if (confirmDelete(`"${folder.name}" and everything in it`)) {
                    deleteFolder.mutate(folder)
                  }
                }
              : null
          }
          onRenameItem={mayChangeFiles ? setRenaming : null}
          onDeleteItem={
            mayChangeFiles
              ? (item) => {
                  if (confirmDelete(`"${item.name}"`)) deleteItem.mutate(item)
                }
              : null
          }
        />
      </div>

      {dialog === 'folder' && selectedId !== null ? (
        <NewFolderDialog
          projectKey={projectKey}
          parentId={selectedId}
          parentName={folderName}
          onDone={(what) => refreshAndSay(`${what} added to ${folderName}.`)}
          onClose={() => setDialog(null)}
        />
      ) : null}

      {dialog === 'upload' && selectedId !== null ? (
        <UploadDialog
          projectKey={projectKey}
          folderName={folderName}
          busy={upload.isPending}
          onUpload={(files, addedBy, level) => upload.mutate({ files, addedBy, level })}
          onClose={() => setDialog(null)}
        />
      ) : null}

      {renaming ? (
        <RenameDialog
          item={renaming}
          onDone={(from, to) => refreshAndSay(`${from} renamed to ${to}.`)}
          onClose={() => setRenaming(null)}
        />
      ) : null}

      {dialog === 'link' && selectedId !== null ? (
        <LinkDialog
          projectKey={projectKey}
          folderId={selectedId}
          onDone={(what) => refreshAndSay(`${what} added to ${folderName}.`)}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </>
  )
}
