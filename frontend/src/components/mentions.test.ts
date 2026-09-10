/**
 * What counts as tagging somebody or something, and what only looks like it.
 *
 * The matcher is the whole of the feature that can be got wrong quietly: a
 * tag that fails to be recognised is a highlight nobody misses, and a tag
 * recognised where none was meant turns an email address into a person, or a
 * greater-than sign into a link.
 */

import { describe, expect, it } from 'vitest'

import type { FiledItem, Person } from '../api/client'
import {
  applyMention,
  matchingFiles,
  matchingMembers,
  mentionQuery,
  splitMentions,
} from './mentions'

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

function file(name: string, folder = '', kind: 'file' | 'link' = 'file'): FiledItem {
  return {
    id: `${folder}/${name}`,
    folder_id: folder,
    kind,
    name,
    url: kind === 'link' ? 'https://drive.example/doc' : null,
    source: kind === 'link' ? 'gdrive' : 'upload',
    size: kind === 'link' ? null : 4096,
    mime: kind === 'link' ? null : 'application/pdf',
    added_by: null,
    folder_path: folder,
    created_at: '2026-01-01T00:00:00Z',
  }
}

const ADITI = person('Aditi K')
const ROHAN = person('Rohan S')
const MEMBERS = [ADITI, ROHAN]

const BRIEF = file('brief.pdf', 'Briefs')
const NUMBERS = file('Q3 numbers.xlsx', 'Finance inputs/2026')
const PORTAL = file('Tax portal', 'Finance inputs', 'link')
const FILES = [BRIEF, NUMBERS, PORTAL]

/**
 * The runs as `@Name` or `>name` for a tag and the bare text for anything
 * else — so a test says which of the three a stretch of text came back as, not
 * merely what it read.
 */
function runs(text: string, members: Person[] = MEMBERS, files: FiledItem[] = FILES): string[] {
  return splitMentions(text, members, files).map((run) => {
    if (run.kind === 'mention') return `@${run.person.name}`
    if (run.kind === 'file') return `>${run.file.name}`
    return run.text
  })
}

describe('splitMentions', () => {
  it('finds a name with a space in it', () => {
    expect(runs('ping @Aditi K about this')).toEqual(['ping ', '@Aditi K', ' about this'])
  })

  it('leaves text with no tag in one piece', () => {
    expect(runs('nothing to see')).toEqual(['nothing to see'])
  })

  it('has nothing to say about nothing', () => {
    expect(splitMentions('', MEMBERS, FILES)).toEqual([])
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

describe('splitMentions, on files', () => {
  it('finds a file behind a >', () => {
    expect(runs('see >brief.pdf for the shape')).toEqual(['see ', '>brief.pdf', ' for the shape'])
  })

  it('finds a name with a space in it', () => {
    expect(runs('>Q3 numbers.xlsx has the totals')).toEqual(['>Q3 numbers.xlsx', ' has the totals'])
  })

  it('finds a link as readily as an upload', () => {
    expect(runs('filed under >Tax portal')).toEqual(['filed under ', '>Tax portal'])
  })

  it('leaves a > that names no file alone', () => {
    expect(runs('7 > 3, and >nothing.txt')).toEqual(['7 > 3, and >nothing.txt'])
  })

  it('leaves the arrows people write in code alone', () => {
    // A `>` is a character with other jobs. What comes before it is how a tag
    // is told from one of them, so these stay text even though a file of that
    // name really is in the project.
    expect(runs('item->brief.pdf')).toEqual(['item->brief.pdf'])
    expect(runs('() =>brief.pdf')).toEqual(['() =>brief.pdf'])
    expect(runs('4>brief.pdf')).toEqual(['4>brief.pdf'])
  })

  it('finds one opened with a bracket or a quote', () => {
    expect(runs('(>brief.pdf)')).toEqual(['(', '>brief.pdf', ')'])
  })

  it('takes the longest name that fits', () => {
    const signature = file('brief.pdf.sig', 'Briefs')
    expect(runs('>brief.pdf.sig', MEMBERS, [BRIEF, signature])).toEqual(['>brief.pdf.sig'])
  })

  it('will not match a name the text runs on past', () => {
    expect(runs('>brief.pdfx')).toEqual(['>brief.pdfx'])
  })

  it('ignores the case the name was typed in', () => {
    // And reports the file, so the tag links to it however it was typed.
    expect(runs('>BRIEF.PDF please')).toEqual(['>brief.pdf', ' please'])
  })

  it('names the first of two files that share a name', () => {
    // There is nothing in the text to tell them apart; the picker shows the
    // folder, which is where the difference is worth knowing.
    const mine = file('notes.md', 'Mine')
    const theirs = file('notes.md', 'Theirs')
    const [tag] = splitMentions('>notes.md', MEMBERS, [mine, theirs])
    expect(tag).toEqual({ kind: 'file', text: '>notes.md', file: mine })
  })

  it('finds a person and a file in one sentence', () => {
    expect(runs('@Aditi K sent >brief.pdf')).toEqual(['@Aditi K', ' sent ', '>brief.pdf'])
  })

  it('finds nothing behind a > when no files are known', () => {
    expect(runs('see >brief.pdf', MEMBERS, [])).toEqual(['see >brief.pdf'])
  })
})

describe('mentionQuery', () => {
  it('sees a bare @ at the caret', () => {
    expect(mentionQuery('hello @', 7)).toEqual({ sigil: '@', at: 6, query: '' })
  })

  it('reads what has been typed towards a name, spaces and all', () => {
    expect(mentionQuery('hello @Aditi K', 14)).toEqual({
      sigil: '@',
      at: 6,
      query: 'Aditi K',
    })
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
    expect(mentionQuery('@Aditi K and @Ro', 16)).toEqual({ sigil: '@', at: 13, query: 'Ro' })
  })
})

describe('mentionQuery, on files', () => {
  it('says which sigil is being completed', () => {
    expect(mentionQuery('see >bri', 8)).toEqual({ sigil: '>', at: 4, query: 'bri' })
  })

  it('takes whichever sigil is nearest the caret', () => {
    expect(mentionQuery('@Aditi K sent >bri', 18)).toEqual({ sigil: '>', at: 14, query: 'bri' })
  })

  it('ignores a > glued to the back of a word', () => {
    expect(mentionQuery('item->bri', 9)).toBeNull()
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

describe('matchingFiles', () => {
  it('offers everything for a bare >', () => {
    expect(matchingFiles('', FILES)).toEqual(FILES)
  })

  it('matches anywhere in the name, not only its start', () => {
    // A file is called `2026-01-atlas-brief.pdf` and remembered as "brief".
    const dated = file('2026-01-atlas-brief.pdf', 'Briefs')
    expect(matchingFiles('brief', [dated])).toEqual([dated])
  })

  it('puts the names that start with the query first', () => {
    const dated = file('2026-01-brief.pdf', 'Briefs')
    expect(matchingFiles('brief', [dated, BRIEF])).toEqual([BRIEF, dated])
  })

  it('ignores case', () => {
    expect(matchingFiles('q3 NUM', FILES)).toEqual([NUMBERS])
  })

  it('offers nothing once the query matches nothing', () => {
    expect(matchingFiles('brief.pdf.sig', FILES)).toEqual([])
  })

  it('offers no more than a list anybody reads to the end of', () => {
    const many = Array.from({ length: 40 }, (_, index) => file(`note-${index}.md`))
    expect(matchingFiles('note', many)).toHaveLength(12)
  })
})

describe('applyMention', () => {
  it('completes a half-typed name and leaves the caret after it', () => {
    expect(applyMention('ping @Ad', { sigil: '@', at: 5, query: 'Ad' }, 'Aditi K')).toEqual({
      value: 'ping @Aditi K ',
      caret: 14,
    })
  })

  it('completes a file behind the sigil it was started with', () => {
    expect(applyMention('see >bri', { sigil: '>', at: 4, query: 'bri' }, 'brief.pdf')).toEqual({
      value: 'see >brief.pdf ',
      caret: 15,
    })
  })

  it('keeps what comes after the caret, without doubling the space', () => {
    expect(
      applyMention('ping @Ad about it', { sigil: '@', at: 5, query: 'Ad' }, 'Aditi K'),
    ).toEqual({
      value: 'ping @Aditi K about it',
      caret: 13,
    })
  })
})
