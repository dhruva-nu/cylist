/**
 * What the reader may do on the project they are looking at.
 *
 * Every screen that offers an action asks this, so that a role which is not
 * allowed the action is not offered a button for it — CYLIST-45's note on the
 * People page put it best: a button that always refuses is worse than one
 * that is not there.
 *
 * It is a read of the same grid the control screen draws, so a project's
 * permissions are fetched once per project and shared by every page under it.
 * The server is still the fence; this only decides what is worth drawing.
 */

import type { Permission } from '../api/client'
import { usePermissionGrid } from '../api/queries'
import { can } from './projectPermissions'

export function usePermissions(projectKey: string): (permission: Permission) => boolean {
  const grid = usePermissionGrid(projectKey)

  // `can` is permissive while the query is in flight — see the note there.
  return (permission: Permission) => can(grid.data?.mine, permission)
}
