import { describe, expect, it } from 'vitest'
import { joinChanged, keysToRefresh } from './liveRefresh'
import { queryKeys } from './queries'
import { parseEvent, type ChangedRef } from './realtime'

const task: ChangedRef = { entity_type: 'task', entity_id: 'a' }
const goal: ChangedRef = { entity_type: 'goal', entity_id: 'b' }

describe('keysToRefresh', () => {
  it('reads the cards, and where a card is open, for a change to a card', () => {
    const keys = keysToRefresh('ATL', [task])

    expect(keys).toContainEqual(queryKeys.tasks('ATL'))
    expect(keys).toContainEqual(queryKeys.allTasks)
  })

  it('leaves the hooks alone for a change to a card', () => {
    expect(keysToRefresh('ATL', [task])).not.toContainEqual(queryKeys.hooks('ATL'))
  })

  it('reads only the goals for a change to a goal', () => {
    expect(keysToRefresh('ATL', [goal])).toEqual([queryKeys.goals('ATL'), queryKeys.allGoals])
  })

  it('reads the hooks for a change to a hook, and not the cards', () => {
    const keys = keysToRefresh('ATL', [{ entity_type: 'hook', entity_id: 'h' }])

    expect(keys).toContainEqual(queryKeys.allHooks)
    expect(keys).not.toContainEqual(queryKeys.tasks('ATL'))
  })

  it('names each key once however many changes ask for it', () => {
    const keys = keysToRefresh('ATL', [task, { ...task, entity_id: 'b' }, task])

    expect(keys).toHaveLength(new Set(keys.map((key) => JSON.stringify(key))).size)
  })

  it('reads the cards, columns and goals when nobody says what changed', () => {
    const keys = keysToRefresh('ATL', null)

    expect(keys).toContainEqual(queryKeys.tasks('ATL'))
    expect(keys).toContainEqual(queryKeys.board('ATL'))
    expect(keys).toContainEqual(queryKeys.goals('ATL'))
  })

  it('treats a kind of thing it does not know as a card, which is what every change used to be', () => {
    expect(keysToRefresh('ATL', [{ entity_type: 'something-new', entity_id: '' }])).toContainEqual(
      queryKeys.tasks('ATL'),
    )
  })
})

describe('joinChanged', () => {
  it('collects what was named since the last refetch', () => {
    expect(joinChanged(joinChanged(undefined, [task]), [goal])).toEqual([task, goal])
  })

  it('is "anything" once either side is', () => {
    expect(joinChanged([task], null)).toBeNull()
    expect(joinChanged(null, [task])).toBeNull()
  })
})

describe('parseEvent on board.changed', () => {
  const frame = (changed: unknown) =>
    JSON.stringify({ type: 'board.changed', at: '2026-10-22T10:00:00Z', changed })

  it('reads the hint', () => {
    expect(parseEvent(frame([task]))).toMatchObject({ type: 'board.changed', changed: [task] })
  })

  it('reads a null hint, and a missing one, as "anything"', () => {
    expect(parseEvent(frame(null))).toMatchObject({ changed: null })
    expect(parseEvent(JSON.stringify({ type: 'board.changed', at: 'x' }))).toMatchObject({
      changed: null,
    })
  })

  it('reads a hint it cannot trust as "anything"', () => {
    expect(parseEvent(frame([{ entity_type: 7 }]))).toMatchObject({ changed: null })
    expect(parseEvent(frame('goal'))).toMatchObject({ changed: null })
  })
})
