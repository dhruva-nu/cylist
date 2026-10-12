import type { Sensitivity } from '../../api/client'
import { usePermissionGrid } from '../../api/queries'

/**
 * A picker for one of the three levels.
 *
 * Takes the catalogue from the server rather than hard-coding three options,
 * so the words a picker shows are the words the roles screen shows beside a
 * clearance.
 */
export function LevelPicker({
  projectKey,
  value,
  onChange,
  includeInherit,
}: {
  projectKey: string
  value: Sensitivity | ''
  onChange: (level: Sensitivity | '') => void
  /** Offer "take the folder's default", which is what omitting it does. */
  includeInherit?: string | undefined
}) {
  // The same query the roles screen draws its clearances from, so a level is
  // worded identically wherever it appears and the catalogue is fetched once
  // per project rather than once per picker.
  const permissions = usePermissionGrid(projectKey)
  const levels = permissions.data?.levels ?? []

  return (
    <select value={value} onChange={(event) => onChange(event.target.value as Sensitivity | '')}>
      {includeInherit ? <option value="">{includeInherit}</option> : null}
      {levels.map((level) => (
        <option key={level.key} value={level.key}>
          {level.label}
        </option>
      ))}
    </select>
  )
}
