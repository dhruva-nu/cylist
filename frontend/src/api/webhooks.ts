/**
 * A project's hooks: its board's changes POSTed to a URL.
 *
 * Named for what they are on the wire rather than `hooks.ts`, which in a React
 * codebase reads as somewhere `use…` functions live.
 */

import type { TaskType } from './tasks'
import { jsonBody, request } from './http'

/**
 * One change a hook can fire on — `task.moved` — how the page words it, and
 * the group the picker offers it in. Listed grouped, groups in order.
 */
export interface HookEvent {
  verb: string
  label: string
  /** One line on when it happens. Empty where the label says it all. */
  hint: string
  category: string
  category_name: string
}

export type DeliveryState = 'pending' | 'delivered' | 'failed'

/**
 * A project's rule for POSTing its board's changes to a URL.
 *
 * `verbs` empty means every change. Each filter narrows to cards: the column a
 * card is in after the change (on a move, only a move *into* it), the column a
 * move left, its template, its type. A filter naming a column or template
 * since deleted comes back with a null name and matches nothing.
 */
export interface Hook {
  id: string
  name: string
  enabled: boolean
  url: string
  verbs: string[]
  to_column_id: string | null
  to_column_name: string | null
  from_column_id: string | null
  from_column_name: string | null
  template_id: string | null
  template_name: string | null
  task_type: TaskType | null
  /** The signing secret's last four characters. The secret itself is shown once. */
  secret_hint: string
  last_delivery: {
    state: DeliveryState
    event: string
    created_at: string
    last_status_code: number | null
  } | null
  created_at: string
  updated_at: string
}

/** A hook as it comes back from being made: the one time it carries its secret. */
export interface HookCreated extends Hook {
  secret: string
}

/**
 * A hook as it is written. On an edit every field is optional, and a filter
 * sent as `null` is cleared — one left out is left alone.
 */
export interface HookInput {
  name: string
  url: string
  enabled?: boolean
  verbs: string[]
  to_column_id: string | null
  from_column_id: string | null
  template_id: string | null
  task_type: TaskType | null
  /** Only on creation. Leave out to have one generated. */
  secret?: string
}

export interface DeliveryAttempt {
  at: string
  status_code: number | null
  error: string | null
  duration_ms: number
}

/** One event on its way to a hook, and every attempt at getting it there. */
export interface HookDelivery {
  id: string
  hook_id: string
  activity_id: string | null
  event: string
  state: DeliveryState
  attempt_count: number
  next_attempt_at: string | null
  delivered_at: string | null
  last_status_code: number | null
  last_error: string | null
  attempts: DeliveryAttempt[]
  /** Exactly the body that was sent. */
  payload: Record<string, unknown>
  created_at: string
}

export const webhooksApi = {
  listHookEvents: () => request<HookEvent[]>('/hooks/events'),
  listHooks: (projectKey: string) => request<Hook[]>(`/projects/${projectKey}/hooks`),
  createHook: (projectKey: string, input: HookInput) =>
    request<HookCreated>(`/projects/${projectKey}/hooks`, {
      method: 'POST',
      body: jsonBody(input),
    }),
  updateHook: (projectKey: string, id: string, input: Partial<HookInput>) =>
    request<Hook>(`/projects/${projectKey}/hooks/${id}`, {
      method: 'PATCH',
      body: jsonBody(input),
    }),
  deleteHook: (projectKey: string, id: string) =>
    request<void>(`/projects/${projectKey}/hooks/${id}`, { method: 'DELETE' }),
  rotateHookSecret: (projectKey: string, id: string) =>
    request<{ secret: string; secret_hint: string }>(`/projects/${projectKey}/hooks/${id}/secret`, {
      method: 'POST',
      body: jsonBody({}),
    }),
  testHook: (projectKey: string, id: string) =>
    request<HookDelivery>(`/projects/${projectKey}/hooks/${id}/test`, { method: 'POST' }),
  listHookDeliveries: (projectKey: string, id: string) =>
    request<HookDelivery[]>(`/projects/${projectKey}/hooks/${id}/deliveries`),
  redeliverHook: (projectKey: string, id: string, deliveryId: string) =>
    request<HookDelivery>(
      `/projects/${projectKey}/hooks/${id}/deliveries/${deliveryId}/redeliver`,
      { method: 'POST' },
    ),
}
