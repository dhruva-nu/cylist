import type { PersonKind, Role, Sensitivity } from '../../api/client'
import styles from './ui.module.css'

export function KindTag({ kind }: { kind: PersonKind }) {
  return <span className={`${styles.tag} ${styles[kind]}`}>{kind}</span>
}

/**
 * What somebody is on a project, worn beside their name.
 *
 * Coloured inline rather than by class, because a role is invented by whoever
 * runs the board and so has no name this stylesheet could have known. The
 * role's own colour is a dot in front of the name rather than a ground behind
 * it: the colour can be any hex at all, and a row of people each wearing a
 * block of a different one is a row of shouting. The name stays in the
 * ordinary ink, so it reads whatever the colour turns out to be.
 */
export function RoleTag({ role }: { role: Pick<Role, 'name' | 'colour' | 'description'> }) {
  return (
    <span className={`${styles.tag} ${styles.roleTag}`} title={role.description || undefined}>
      <span className={styles.roleDot} style={{ background: role.colour }} aria-hidden="true" />
      {role.name}
    </span>
  )
}

/**
 * How far something may travel, when that is worth saying.
 *
 * Drawn for `public` and `restricted` and *not* for `internal`, which is what
 * an upload is unless somebody said otherwise: a tag on every row of every
 * listing would be noise the eye learns to skip, and the whole job of this is
 * to be the thing that catches it.
 */
export function LevelTag({ level }: { level: Sensitivity }) {
  if (level === 'internal') return null
  return (
    <span className={`${styles.levelTag} ${level === 'restricted' ? styles.restricted : ''}`}>
      {level === 'restricted' ? 'Restricted' : 'Public'}
    </span>
  )
}
