/**
 * Shared presentational primitives, styled from the design tokens.
 *
 * Each lives in `primitives/` beside the one stylesheet they share; this file
 * is the single place every screen imports them from.
 */

import styles from './primitives/ui.module.css'

export { Avatar, initials, readableInkOn } from './primitives/Avatar'
export { Button } from './primitives/Button'
export {
  EmptyState,
  ErrorBanner,
  ErrorNotice,
  Eyebrow,
  LiveRegion,
  useAnnouncer,
} from './primitives/feedback'
export {
  AlertIcon,
  CalendarIcon,
  ClockIcon,
  CommentIcon,
  JiraIcon,
  MailIcon,
  PrIcon,
  PriorityIcon,
  StatusIcon,
  TypeIcon,
} from './primitives/icons'
export { LevelPicker } from './primitives/LevelPicker'
export { SubStatusBar } from './primitives/SubStatusBar'
export { Tagged } from './primitives/Tagged'
export { KindTag, LevelTag, RoleTag } from './primitives/tags'
export { TaskRef, jiraRefLabel, prRefLabel, prRefRepo } from './primitives/TaskRef'

/** The shared stylesheet itself, for a screen that wears its card or button look on its own element. */
export const cardStyles = styles
