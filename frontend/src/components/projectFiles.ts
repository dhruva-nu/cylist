/**
 * The project's files, as the `>` tag needs them: one flat list of names.
 *
 * A hook rather than a query written out at each of its call sites, because
 * every box that takes prose and every place prose is read back wants the same
 * list, and a second spelling of the key would mean an upload that appeared in
 * one of them and not the others.
 *
 * Never waited for. A tag whose file has not arrived yet reads as the plain
 * text it is — which is what it is anyway, this being tagging in the text
 * rather than a reference stored beside it — so nothing here blocks a dialog
 * or a page on the list.
 */

import { useQuery, type QueryKey } from '@tanstack/react-query'

import { api, type FiledItem } from '../api/client'

/** The one key, so uploading a file can invalidate every reader of it. */
export function projectFilesKey(projectKey: string): QueryKey {
  return ['project-items', projectKey]
}

/** Every file and link in the project; empty until they arrive. */
export function useProjectFiles(projectKey: string): readonly FiledItem[] {
  const files = useQuery({
    queryKey: projectFilesKey(projectKey),
    queryFn: () => api.listProjectItems(projectKey),
  })
  return files.data ?? []
}
