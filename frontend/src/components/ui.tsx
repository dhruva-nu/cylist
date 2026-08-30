/** Shared presentational primitives, styled from the design tokens. */

import type { ButtonHTMLAttributes, ReactNode } from 'react'
import type { PersonKind } from '../api/client'
import styles from './ui.module.css'

type ButtonVariant = 'plain' | 'go' | 'ghost'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  small?: boolean
  danger?: boolean
}

export function Button({
  variant = 'plain',
  small = false,
  danger = false,
  className,
  type = 'button',
  ...rest
}: ButtonProps) {
  const classes = [
    styles.button,
    variant === 'go' && styles.go,
    variant === 'ghost' && styles.ghost,
    small && styles.small,
    danger && styles.danger,
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return <button type={type} className={classes} {...rest} />
}

/** Initials from a name — "Aditi K" becomes AK. */
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? '')
    .join('')
}

export function Avatar({
  name,
  colour,
  large = false,
}: {
  name: string
  colour: string
  large?: boolean
}) {
  return (
    <span
      className={`${styles.avatar} ${large ? styles.avatarLarge : ''}`}
      style={{ background: colour }}
      title={name}
      aria-hidden="true"
    >
      {initials(name)}
    </span>
  )
}

export function KindTag({ kind }: { kind: PersonKind }) {
  return <span className={`${styles.tag} ${styles[kind]}`}>{kind}</span>
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <span className={styles.eyebrow}>{children}</span>
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className={styles.empty}>{children}</div>
}

export function ErrorBanner({ children }: { children: ReactNode }) {
  return (
    <p className={styles.banner} role="alert">
      {children}
    </p>
  )
}

export const cardStyles = styles
