/**
 * One role, and everything there is to say about it: who holds it, what it may
 * do, where on the board, and how far it may read.
 */

import {
  isMe,
  type ColumnRule,
  type Identity,
  type Member,
  type Permission,
  type PermissionInfo,
  type RolePermissions,
  type RoleSummary,
  type Sensitivity,
  type SensitivityInfo,
} from '../../api/client'
import { Avatar, Button, KindTag } from '../../components/ui'
import {
  clearanceOf,
  countPeople,
  explain,
  isEveryoneElse,
  isFixed,
  summarise,
  summariseColumns,
  type ColumnRuleField,
} from '../projectPermissions'
import styles from './ProjectRoles.module.css'
import { whyUndeletable } from './projectRoles'

/** One labelled tick box, with the sentence that says what it allows. */
function Tick({
  entry,
  on,
  disabled,
  onChange,
}: {
  entry: PermissionInfo
  on: boolean
  disabled: boolean
  onChange: (on: boolean) => void
}) {
  return (
    <li className={styles.tick}>
      <label>
        <input
          type="checkbox"
          checked={on}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>
          <b>{entry.label}</b>
          <span className={styles.summary}>{entry.summary}</span>
        </span>
      </label>
    </li>
  )
}

/**
 * One role, and everything there is to say about it.
 *
 * The three axes read top to bottom in the order they narrow each other: what
 * this role may do at all, then where on the board it may do the card half of
 * it, then how far it may read. Who holds it comes first, because that is what
 * the reader clicked the name to find out.
 */
export function RoleDetail({
  line,
  role,
  roles,
  catalogue,
  levels,
  members,
  identity,
  amAdmin,
  permissions,
  columnRules,
  busy,
  onPermission,
  onColumn,
  onClearance,
  onSetRole,
  onDelete,
}: {
  line: RolePermissions
  /** The role row behind this line, absent for the baseline. */
  role: RoleSummary | undefined
  roles: RoleSummary[]
  catalogue: PermissionInfo[]
  levels: SensitivityInfo[]
  members: Member[]
  identity: Identity | undefined
  amAdmin: boolean
  /** What the role holds, with any tick still on its way to the server. */
  permissions: Permission[]
  /** Where on the board it may work, likewise. */
  columnRules: ColumnRule[]
  busy: { permissions: boolean; columns: boolean; clearance: boolean; deleting: boolean }
  onPermission: (entry: Permission, on: boolean) => void
  onColumn: (columnId: string, field: ColumnRuleField, on: boolean) => void
  onClearance: (level: Sensitivity) => void
  onSetRole: (personId: string, role: string | null) => void
  onDelete: (role: RoleSummary) => void
}) {
  const undeletable = role ? whyUndeletable(role) : null
  const why = explain(line) ?? role?.description
  const holders = members.filter((person) =>
    isEveryoneElse(line) ? person.role === null : person.role?.id === line.role_id,
  )

  return (
    <div className={styles.detail}>
      <header className={styles.detailHead}>
        <div className={styles.detailTitle}>
          <span className={styles.dot} style={{ background: line.colour }} aria-hidden />
          <h2>{line.name}</h2>
          <span className={styles.count}>{summarise({ ...line, permissions }, catalogue)}</span>
        </div>
        {/* Not drawn on the admin role or the baseline: neither can be deleted,
            and a greyed button explaining that is still a button. */}
        {amAdmin && role && !isEveryoneElse(line) && !isFixed(line) ? (
          <Button
            variant="ghost"
            small
            danger
            disabled={undeletable !== null || busy.deleting}
            title={undeletable ?? undefined}
            onClick={() => onDelete(role)}
          >
            Delete role
          </Button>
        ) : null}
      </header>

      {/* One line, not two: the admin role carries a seeded description that
          says what `explain` says, and printing both reads as a stutter. */}
      {why ? <p className={styles.why}>{why}</p> : null}

      <section className={styles.axis}>
        <header>
          <b>Who holds it</b>
          <span className={styles.count}>{countPeople(line.member_count)}</span>
        </header>
        <Holders
          holders={holders}
          identity={identity}
          roles={roles}
          amAdmin={amAdmin}
          baseline={isEveryoneElse(line)}
          onSetRole={onSetRole}
        />
      </section>

      {/* Two columns rather than one long scroll. The twelve tick boxes are
          the tall thing and the two narrowing axes are the short ones, so
          stacking all three put the clearance a screen and a half below the
          permission it qualifies. Side by side they are visible together,
          which is how the decision is actually made. */}
      <div className={styles.axes}>
        <section className={styles.axis}>
          <header>
            <b>What it can do</b>
          </header>
          <ul className={styles.permissions}>
            {catalogue.map((entry) => (
              <Tick
                key={entry.key}
                entry={entry}
                on={isFixed(line) || permissions.includes(entry.key)}
                disabled={!amAdmin || isFixed(line) || busy.permissions}
                onChange={(on) => onPermission(entry.key, on)}
              />
            ))}
          </ul>
        </section>

        {/* Both of these narrow what the tick boxes beside them allowed —
            they never widen it — so they sit in the second column, under a
            heading that says which permission each is about. */}
        <div className={styles.narrowing}>
          <Workflow
            columnRules={columnRules}
            summary={summariseColumns({ ...line, columns: columnRules })}
            disabled={!amAdmin || isFixed(line) || busy.columns}
            gated={!isFixed(line) && !permissions.includes('tasks')}
            onChange={onColumn}
          />

          <Clearance
            levels={levels}
            value={clearanceOf(line)}
            disabled={!amAdmin || isFixed(line) || busy.clearance}
            onChange={onClearance}
          />
        </div>
      </div>
    </div>
  )
}

/**
 * The people on this line, and the control that moves one off it.
 *
 * The same `select` the People page used to carry, grouped by what somebody
 * is rather than listed flat. Changing it here moves that person to another
 * line of the sidebar, which is the whole of what "say what they are" means.
 */
function Holders({
  holders,
  identity,
  roles,
  amAdmin,
  baseline,
  onSetRole,
}: {
  holders: Member[]
  identity: Identity | undefined
  roles: RoleSummary[]
  amAdmin: boolean
  /** Whether this is the "Everyone else" line, which nobody is *given*. */
  baseline: boolean
  onSetRole: (personId: string, role: string | null) => void
}) {
  if (holders.length === 0) {
    return (
      <p className={styles.why}>
        {baseline
          ? 'Everybody on this project has been given a role.'
          : 'Nobody holds this role yet. Give it to somebody from their own line.'}
      </p>
    )
  }

  return (
    <ul className={styles.holders}>
      {holders.map((person) => (
        <li key={person.id}>
          <Avatar name={person.name} colour={person.colour} />
          <span className={styles.who}>
            <b>{person.name}</b>
            <span className={styles.title}>{person.title}</span>
          </span>
          <KindTag kind={person.kind} />
          {isMe(person, identity) ? <span className={styles.you}>you</span> : null}
          {amAdmin ? (
            <label className={styles.picker}>
              <span className={styles.srOnly}>{person.name}&rsquo;s role</span>
              <select
                value={person.role?.id ?? ''}
                onChange={(event) => onSetRole(person.id, event.target.value || null)}
              >
                <option value="">No role</option>
                {roles.map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

/**
 * Where on the board this role may work.
 *
 * Two ticks per column and they are different jobs: moving a card into Review
 * is work, while deciding that a Hotfix in Review passes through "Drafted,
 * Reviewed, Merged" is designing the workflow — and a team often wants the
 * second in one person's hands while everybody does the first.
 */
function Workflow({
  columnRules,
  summary,
  disabled,
  gated,
  onChange,
}: {
  columnRules: ColumnRule[]
  summary: string
  disabled: boolean
  /** Whether the flat `tasks` permission is off, which makes all of this moot. */
  gated: boolean
  onChange: (columnId: string, field: ColumnRuleField, on: boolean) => void
}) {
  if (columnRules.length === 0) return null

  return (
    <section className={styles.axis}>
      <header>
        <b>Where on the board</b>
        <span className={styles.count}>{gated ? 'No cards at all' : summary}</span>
      </header>
      {gated ? (
        <p className={styles.why}>
          This role cannot change cards, so where on the board it may do so does not arise.
        </p>
      ) : (
        <table className={styles.columns}>
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Move cards in</th>
              <th scope="col">Set sub-stages</th>
            </tr>
          </thead>
          <tbody>
            {columnRules.map((column) => (
              <tr key={column.column_id}>
                <th scope="row">{column.name}</th>
                <td>
                  <input
                    type="checkbox"
                    checked={column.may_enter}
                    disabled={disabled}
                    aria-label={`Move cards into ${column.name}`}
                    onChange={(event) =>
                      onChange(column.column_id, 'may_enter', event.target.checked)
                    }
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={column.may_stage}
                    disabled={disabled}
                    aria-label={`Set the sub-stages for ${column.name}`}
                    onChange={(event) =>
                      onChange(column.column_id, 'may_stage', event.target.checked)
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

/**
 * How sensitive a thing this role may read.
 *
 * A radio group rather than a select: there are three, they are ordered, and
 * the whole point of the control is seeing at a glance which way along that
 * order a role sits.
 */
function Clearance({
  levels,
  value,
  disabled,
  onChange,
}: {
  levels: SensitivityInfo[]
  value: Sensitivity
  disabled: boolean
  onChange: (level: Sensitivity) => void
}) {
  if (levels.length === 0) return null

  return (
    <section className={styles.axis}>
      <header>
        <b>Uploads it can read</b>
        <span className={styles.count}>up to {value}</span>
      </header>
      <ul className={styles.levels}>
        {levels.map((level) => (
          <li key={level.key}>
            <label>
              <input
                type="radio"
                checked={value === level.key}
                disabled={disabled}
                onChange={() => onChange(level.key)}
              />
              <span>
                <b>{level.label}</b>
                <span className={styles.summary}>{level.summary}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
    </section>
  )
}
