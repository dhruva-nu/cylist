/**
 * What counts as tagging somebody, and what only looks like it.
 *
 * The matcher is the whole of the feature that can be got wrong quietly: a
 * tag that fails to be recognised is a highlight nobody misses, and a tag
 * recognised where none was meant turns an email address into a person.
 */

import { describe, expect, it } from 'vitest'

import type { Person } from '../api/client'
import { applyMention, matchingMembers, mentionQuery, splitMentions } from './mentions'

function person(name: string): Person {
  return {
    id: name,
    name,
    kind: 'team',
    role: 'Backend engineer',
    responsibilities: '',
    email: null,
    colour: '#1D7D46',
    archived_at: null,
    created_at: '2026-01-01T00:00:00Z',
    is_me: false,
  }
}

const ADITI = person('Aditi K')
const ROHAN = person('Rohan S')
const MEMBERS = [ADITI, ROHAN]

/** The runs as `@Name` for a tag and the bare text for anything else. */
function runs(text: string, members: Person[] = MEMBERS): string[] {
  return splitMentions(text, members).map((run) =>
    run.kind === 'mention' ? `@${run.person.name}` : run.text,
  )
}

describe('splitMentions', () => {
  it('finds a name with a space in it', () => {
    expect(runs('ping @Aditi K about this')).toEqual(['ping ', '@Aditi K', ' about this'])
  })

  it('leaves text with no tag in one piece', () => {
    expect(runs('nothing to see')).toEqual(['nothing to see'])
  })

  it('finds a tag at the very start and the very end', () => {
    expect(runs('@Aditi K')).toEqual(['@Aditi K'])
    expect(runs('over to @Rohan S')).toEqual(['over to ', '@Rohan S'])
  })

  it('finds more than one', () => {
    expect(runs('@Aditi K and @Rohan S')).toEqual(['@Aditi K', ' and ', '@Rohan S'])
  })

  it('takes the longest name that fits', () => {
    // A directory holding both would otherwise read `@Aditi K` as `@Aditi`
    // with a stray K after it.
    expect(runs('@Aditi K', [person('Aditi'), ADITI])).toEqual(['@Aditi K'])
  })

  it('will not match a name the text runs on past', () => {
    expect(runs('@Aditi Kumar')).toEqual(['@Aditi Kumar'])
  })

  it('is not fooled by an email address', () => {
    expect(runs('mail deploy@Aditi K now')).toEqual(['mail deploy@Aditi K now'])
  })

  it('ignores the case the name was typed in', () => {
    // But reports the person's real name, not the typed one: the tag is who
    // it names, and the highlight should read the way the directory does.
    expect(runs('@aditi k please')).toEqual(['@Aditi K', ' please'])
  })

  it('leaves an @ that names nobody alone', () => {
    expect(runs('@Nobody At All')).toEqual(['@Nobody At All'])
  })

  it('finds a tag against punctuation on both sides', () => {
    expect(runs('(@Aditi K), then')).toEqual(['(', '@Aditi K', '), then'])
  })

  it('finds a tag on a later line', () => {
    expect(runs('first\n@Rohan S')).toEqual(['first\n', '@Rohan S'])
  })
})

describe('mentionQuery', () => {
  it('sees a bare @ at the caret', () => {
    expect(mentionQuery('hello @', 7)).toEqual({ at: 6, query: '' })
  })

  it('reads what has been typed towards a name, spaces and all', () => {
    expect(mentionQuery('hello @Aditi K', 14)).toEqual({ at: 6, query: 'Aditi K' })
  })

  it('is nothing when the caret is not after an @', () => {
    expect(mentionQuery('nothing here', 12)).toBeNull()
  })

  it('does not reach back past a newline', () => {
    expect(mentionQuery('@Aditi\nand then', 15)).toBeNull()
  })

  it('ignores an @ that is part of an address', () => {
    expect(mentionQuery('deploy@example', 14)).toBeNull()
  })

  it('takes the @ nearest the caret', () => {
    expect(mentionQuery('@Aditi K and @Ro', 16)).toEqual({ at: 13, query: 'Ro' })
  })
})

describe('matchingMembers', () => {
  it('offers everybody for a bare @', () => {
    expect(matchingMembers('', MEMBERS)).toEqual(MEMBERS)
  })

  it('narrows as the name is typed', () => {
    expect(matchingMembers('Ad', MEMBERS)).toEqual([ADITI])
  })

  it('ignores case', () => {
    expect(matchingMembers('rohan', MEMBERS)).toEqual([ROHAN])
  })

  it('offers nobody once the query matches nobody', () => {
    // This is what closes the menu: a space no name has empties the list.
    expect(matchingMembers('Aditi Q', MEMBERS)).toEqual([])
  })
})

describe('applyMention', () => {
  it('completes a half-typed name and leaves the caret after it', () => {
    expect(applyMention('ping @Ad', { at: 5, query: 'Ad' }, 'Aditi K')).toEqual({
      value: 'ping @Aditi K ',
      caret: 14,
    })
  })

  it('keeps what comes after the caret, without doubling the space', () => {
    expect(applyMention('ping @Ad about it', { at: 5, query: 'Ad' }, 'Aditi K')).toEqual({
      value: 'ping @Aditi K about it',
      caret: 13,
    })
  })
})
