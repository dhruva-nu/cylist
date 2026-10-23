import { describe, expect, it } from 'vitest'
import type { AgentPresence } from './agents'
import type { Board } from './board'
import type { components } from './schema'
import type { Person } from './people'
import type { Project } from './projects'
import type { Goal } from './goals'
import type { Task, TaskDetail } from './tasks'
import type { Hook, HookDelivery } from './webhooks'
import type { Identity, SetupInfo } from './auth'
import type { DayReport } from './reports'
import type { Folder, FileItem, FolderChildren } from './files'
import type { Template } from './board'
import type { Role } from './roles'
import type { Skill } from './skills'
import type { Doc, DocTree } from './docs'
import type { VaultNode, VaultTree } from './vault'
import type { TodayPlan } from './today'
import type { GoalDetail } from './goals'

type S = components['schemas']

/**
 * A hand-written type with its string unions opened to `string`. The client
 * names the values it knows (`'active' | 'hold'`); the server need only send
 * strings, and a value this client has not heard of yet is not a failure.
 */
type Open<T> = T extends string
  ? string
  : T extends readonly (infer Item)[]
    ? Open<Item>[]
    : T extends object
      ? { [Key in keyof T]: Open<T[Key]> }
      : T

/**
 * A schema type as the server sends it. The generated types mark a field
 * optional when the model gives it a default, but a response always carries
 * every field, so what a client reads is the schema with those made required.
 */
type Sent<T> = T extends readonly (infer Item)[]
  ? Sent<Item>[]
  : T extends object
    ? { [Key in keyof T]-?: Sent<T[Key]> }
    : T

/**
 * What the server promises, held against what this client expects.
 *
 * The client's types are written by hand and `request<T>` believes them, so a
 * field renamed or retyped on the server is a `undefined` somewhere in the app
 * and nothing says so. Each line assigns a schema type — generated from
 * `android/api/openapi.json`, which `make check` keeps equal to the backend — to
 * the hand-written type the client reads it as, so the build fails where they
 * part. The direction is the one that matters: everything the client reads must
 * be something the schema says arrives.
 */
describe('hand-written types against the generated schema', () => {
  it('agree', () => {
    const agree = {
      project: {} as Sent<S['ProjectRead']> satisfies Open<Project>,
      person: {} as Sent<S['PersonRead']> satisfies Open<Person>,
      goal: {} as Sent<S['GoalRead']> satisfies Open<Goal>,
      task: {} as Sent<S['TaskSummary']> satisfies Open<Task>,
      taskDetail: {} as Sent<S['TaskDetail']> satisfies Open<TaskDetail>,
      hook: {} as Sent<S['HookRead']> satisfies Open<Hook>,
      delivery: {} as Sent<S['HookDeliveryRead']> satisfies Open<HookDelivery>,
      identity: {} as Sent<S['Identity']> satisfies Open<Identity>,
      setup: {} as Sent<S['SetupInfo']> satisfies Open<SetupInfo>,
      board: {} as Sent<S['Board']> satisfies Open<Board>,
      report: {} as Sent<S['DayReport']> satisfies Open<DayReport>,
      folderChildren: {} as Sent<S['FolderChildren']> satisfies Open<FolderChildren>,
      folder: {} as Sent<S['FolderRead']> satisfies Open<Folder>,
      item: {} as Sent<S['ItemRead']> satisfies Open<FileItem>,
      template: {} as Sent<S['TemplateRead']> satisfies Open<Template>,
      role: {} as Sent<S['RoleRead']> satisfies Open<Role>,
      skill: {} as Sent<S['SkillRead']> satisfies Open<Skill>,
      doc: {} as Sent<S['DocRead']> satisfies Open<Doc>,
      docTree: {} as Sent<S['DocTree']> satisfies Open<DocTree>,
      vaultTree: {} as Sent<S['VaultTreeRead']> satisfies Open<VaultTree>,
      vaultNode: {} as Sent<S['VaultNodeRead']> satisfies Open<VaultNode>,
      today: {} as Sent<S['TodayPlan']> satisfies Open<TodayPlan>,
      goalDetail: {} as Sent<S['GoalDetail']> satisfies Open<GoalDetail>,
      presence: {} as Sent<S['AgentPresence']> satisfies Open<AgentPresence>,
    }
    // The assertions are the `satisfies` above, checked by `tsc`; this only keeps
    // them from being dead code.
    expect(Object.keys(agree).length).toBeGreaterThan(0)
  })
})
