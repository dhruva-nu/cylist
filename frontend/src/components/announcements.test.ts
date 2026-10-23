import { afterEach, describe, expect, it } from 'vitest'
import { announce, clearAnnouncement, currentAnnouncement } from './announcements'

afterEach(clearAnnouncement)

describe('announce', () => {
  it('says what it is given', () => {
    announce('Card moved to Review.')

    expect(currentAnnouncement()).toBe('Card moved to Review.')
  })

  it('makes the same words new again, so a repeat is read again', () => {
    announce('Saved.')
    const first = currentAnnouncement()

    announce('Saved.')

    expect(currentAnnouncement()).not.toBe(first)
    expect(currentAnnouncement().trim()).toBe('Saved.')
  })
})
