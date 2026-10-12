import { describe, expect, it } from 'vitest'
import { feedClock, feedLabel, isCodeLike } from './agentFeed'

describe('feedLabel', () => {
  it('names what the agent did, one word each', () => {
    expect(feedLabel('said')).toBe('Said')
    expect(feedLabel('read')).toBe('Read')
    expect(feedLabel('edit')).toBe('Edit')
    expect(feedLabel('run')).toBe('Ran')
    expect(feedLabel('output')).toBe('Got')
    expect(feedLabel('error')).toBe('Failed')
    expect(feedLabel('tool')).toBe('Tool')
  })
})

describe('isCodeLike', () => {
  it('sets everything but the agent’s own words in mono', () => {
    expect(isCodeLike('said')).toBe(false)
    expect(isCodeLike('run')).toBe(true)
    expect(isCodeLike('read')).toBe(true)
  })
})

describe('feedClock', () => {
  it('is hours and minutes', () => {
    expect(feedClock('2026-10-12T14:31:02Z')).toMatch(/^\d{2}:\d{2}$/)
  })

  it('is empty for a time it cannot read', () => {
    expect(feedClock('not a time')).toBe('')
  })
})
