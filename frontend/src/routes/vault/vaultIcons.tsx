/** The two marks a vault is drawn with: a secret's padlock and a branch's folder. */

import { FolderIcon } from '../../components/ui'

export function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="4" y="10" width="16" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  )
}

/** A node's mark, by what kind of node it is. */
export function NodeIcon({ kind }: { kind: 'branch' | 'secret' }) {
  return kind === 'branch' ? <FolderIcon /> : <LockIcon />
}
