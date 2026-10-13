/** The pure half of the template dialog: what a template says, and how its stages are edited. */

import type { BoardColumn, Template } from '../api/client'

/** A stage while it is being edited: the same shape, minus the column's name. */
export interface DraftStage {
  column_id: string
  sub_stage_labels: string[]
  allowed_outcomes: string[]
}

/** What a template amounts to on this board, in one line. */
export function summariseTemplate(template: Template, columns: BoardColumn[]): string {
  if (!template.stages.length) return 'Any column — unrestricted.'
  return template.stages
    .map(
      (stage) => columns.find((column) => column.id === stage.column_id)?.name ?? stage.column_name,
    )
    .join(' → ')
}

/** A template's stages as the editor holds them: copies, so editing leaves the template alone. */
export function draftStagesOf(template: Template | null): DraftStage[] {
  return (template?.stages ?? []).map((stage) => ({
    column_id: stage.column_id,
    sub_stage_labels: [...stage.sub_stage_labels],
    allowed_outcomes: [...stage.allowed_outcomes],
  }))
}

/**
 * A stage with one of its column's outcomes turned on or off.
 *
 * An empty list means all of them, so the toggles start lit and the first
 * click has to take one *out* of the full set rather than add one to
 * nothing. Choosing every one is the same rule as choosing none and is
 * stored as none, which is the form that survives an outcome being renamed;
 * choosing none at all is refused, because it would light them all straight
 * back up and read as a click that did nothing.
 */
export function withOutcomeToggled(stage: DraftStage, outcome: string, all: string[]): DraftStage {
  const chosen = new Set(stage.allowed_outcomes.length ? stage.allowed_outcomes : all)
  if (chosen.has(outcome)) chosen.delete(outcome)
  else chosen.add(outcome)
  if (chosen.size === 0) return stage
  // In the column's own order, so the template reads the way the board
  // draws it however the toggles were clicked.
  const next = all.filter((one) => chosen.has(one))
  return { ...stage, allowed_outcomes: next.length === all.length ? [] : next }
}
