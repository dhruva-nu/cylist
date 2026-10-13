/** A project's files: its folders, uploads and links. */

import { API_BASE, jsonBody, request } from './http'
import type { Person } from './people'
import type { Sensitivity } from './roles'

export type ItemKind = 'file' | 'link'
export type ItemSource = 'upload' | 'sharepoint' | 'gdrive' | 'other'

export interface Folder {
  id: string
  project_id: string
  /** Null only for the project's root folder. */
  parent_id: string | null
  name: string
  /**
   * Whether this is the project's root: one per project, named after it,
   * holding files and folders alike, and impossible to rename or delete.
   */
  is_root: boolean
  /** What things uploaded here are classified as unless the caller says. */
  default_sensitivity: Sensitivity
  created_at: string
}

/** A folder in the whole-tree response, with its subfolders inside it. */
export interface FolderNode {
  id: string
  name: string
  parent_id: string | null
  is_root: boolean
  children: FolderNode[]
}

export interface FolderCrumb {
  id: string
  name: string
}

export interface FileItem {
  id: string
  folder_id: string
  kind: ItemKind
  name: string
  /** How far it may travel. A role not cleared this high is not shown it. */
  sensitivity: Sensitivity
  url: string | null
  source: ItemSource
  size: number | null
  mime: string | null
  added_by: Person | null
  created_at: string
}

/**
 * An item with where it is filed — a row of the project-wide flat listing.
 *
 * The path is what tells two files of the same name apart in a picker. It is
 * a string of folder names because nothing navigates with it: a `>` tag
 * carries the name, and the path is only shown beside it.
 */
export interface FiledItem extends FileItem {
  /** Folder names between the root and the item, `/`-joined; empty at the top. */
  folder_path: string
}

export interface FolderChildren {
  folder: Folder
  /** The folders above this one, outermost first, ending with it. */
  path: FolderCrumb[]
  folders: Folder[]
  items: FileItem[]
}

export interface LinkInput {
  name: string
  url: string
  source: ItemSource
  added_by?: string | null
  /** Null or omitted takes the folder's default. */
  sensitivity?: Sensitivity | null
}

export const filesApi = {
  /** The project's root folder, with the whole tree nested inside it. */
  getTree: (projectKey: string) => request<FolderNode>(`/projects/${projectKey}/tree`),
  createFolder: (
    projectKey: string,
    input: {
      name: string
      parent_id: string | null
      /** Null inherits the parent folder's, which is usually what is wanted. */
      default_sensitivity?: Sensitivity | null
    },
  ) =>
    request<Folder>(`/projects/${projectKey}/folders`, { method: 'POST', body: jsonBody(input) }),
  deleteFolder: (id: string) => request<{ ok: boolean }>(`/folders/${id}`, { method: 'DELETE' }),
  getFolderChildren: (id: string) => request<FolderChildren>(`/folders/${id}/children`),
  /**
   * Every file and link in the project, flat — what a `>` tag is completed
   * from, and what one is recognised against when prose is read back.
   */
  listProjectItems: (projectKey: string) => request<FiledItem[]>(`/projects/${projectKey}/items`),

  uploadFile: (
    folderId: string,
    file: File,
    addedBy?: string | null,
    level?: Sensitivity | null,
  ) => {
    const form = new FormData()
    form.append('file', file)
    if (addedBy) form.append('added_by', addedBy)
    // Left off rather than sent empty: omitting it is what makes the server
    // fall back to the folder's default.
    if (level) form.append('sensitivity', level)
    return request<FileItem>(`/folders/${folderId}/upload`, { method: 'POST', body: form })
  },
  addLink: (folderId: string, input: LinkInput) =>
    request<FileItem>(`/folders/${folderId}/links`, { method: 'POST', body: jsonBody(input) }),
  /**
   * Rename a file or link, or change how far it may travel.
   *
   * A file's URL and source are fixed by the upload, so only a link's may be
   * sent; the server refuses the rest rather than ignoring it.
   */
  updateItem: (id: string, input: { name?: string; sensitivity?: Sensitivity }) =>
    request<FileItem>(`/items/${id}`, { method: 'PATCH', body: jsonBody(input) }),
  deleteItem: (id: string) => request<{ ok: boolean }>(`/items/${id}`, { method: 'DELETE' }),
  /** Where the browser fetches a file's bytes from — used as an anchor's href. */
  downloadUrl: (id: string) => `${API_BASE}/items/${id}/download`,
}
