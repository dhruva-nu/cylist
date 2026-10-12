/**
 * The rules a rename is held to before it is sent.
 *
 * Pinned here because they are a restatement of the server's `clean_name`,
 * and a restatement drifts silently: a rule dropped from this side does not
 * break anything visible, it just lets a name through to come back 422 with a
 * message written for an API client rather than for the person typing.
 */

import { describe, expect, it } from 'vitest'

import { extensionOf, extensionWarning, renameIsAChange, renameProblem } from './fileRename'

describe('renameProblem', () => {
  it('accepts an ordinary rename', () => {
    expect(renameProblem('brief.pdf', 'brief-v2.pdf')).toBeNull()
  })

  it('refuses a blank name', () => {
    expect(renameProblem('brief.pdf', '')).toBe('A name is needed.')
    expect(renameProblem('brief.pdf', '   ')).toBe('A name is needed.')
  })

  it('refuses anything that is trying to be a path', () => {
    expect(renameProblem('brief.pdf', '../secrets')).toBe('A name cannot be a path.')
    expect(renameProblem('brief.pdf', 'a/b.pdf')).toBe('A name cannot be a path.')
    expect(renameProblem('brief.pdf', 'a\\b.pdf')).toBe('A name cannot be a path.')
    expect(renameProblem('brief.pdf', '.')).toBe('A name cannot be a path.')
    expect(renameProblem('brief.pdf', '..')).toBe('A name cannot be a path.')
  })

  it('refuses control characters, which a paste can carry in unseen', () => {
    expect(renameProblem('brief.pdf', 'brief\npdf')).toBe(
      'A name cannot contain control characters.',
    )
    expect(renameProblem('brief.pdf', 'brief\x7f.pdf')).toBe(
      'A name cannot contain control characters.',
    )
  })

  it('refuses a name past the column width', () => {
    expect(renameProblem('brief.pdf', 'x'.repeat(200))).toBeNull()
    expect(renameProblem('brief.pdf', 'x'.repeat(201))).toBe(
      'A name can be at most 200 characters.',
    )
  })

  // Not a problem to report: Save is off for it anyway, and telling someone
  // their file is already called what it is called reads as an accusation.
  it('has nothing to say about a name left as it was', () => {
    expect(renameProblem('brief.pdf', 'brief.pdf')).toBeNull()
    expect(renameProblem('brief.pdf', '  brief.pdf  ')).toBeNull()
  })
})

describe('renameIsAChange', () => {
  it('is true only for a different, non-blank name', () => {
    expect(renameIsAChange('brief.pdf', 'brief-v2.pdf')).toBe(true)
    expect(renameIsAChange('brief.pdf', 'brief.pdf')).toBe(false)
    expect(renameIsAChange('brief.pdf', '')).toBe(false)
  })

  // The server strips the name it is given, so sending a padded copy of the
  // same name is a request that does nothing.
  it('does not count padding as a change', () => {
    expect(renameIsAChange('brief.pdf', ' brief.pdf ')).toBe(false)
  })
})

describe('extensionOf', () => {
  it('reads the part after the last dot, lowercased', () => {
    expect(extensionOf('brief.PDF')).toBe('pdf')
    expect(extensionOf('archive.tar.gz')).toBe('gz')
  })

  it('has none for a name without one, or a dotfile', () => {
    expect(extensionOf('README')).toBe('')
    expect(extensionOf('.gitignore')).toBe('')
  })
})

describe('extensionWarning', () => {
  it('says nothing while the ending is kept', () => {
    expect(extensionWarning('brief.pdf', 'brief-v2.pdf')).toBeNull()
    expect(extensionWarning('brief.pdf', 'brief.PDF')).toBeNull()
  })

  it('warns when the ending is dropped', () => {
    expect(extensionWarning('brief.pdf', 'brief')).toBe(
      'This drops the .pdf ending, which is what it downloads as.',
    )
  })

  it('warns when the ending is swapped', () => {
    expect(extensionWarning('brief.pdf', 'brief.txt')).toBe(
      'This changes .pdf to .txt, which is what it downloads as.',
    )
  })

  // A file that never had one cannot lose one, and a rename that gives it one
  // is the fix rather than the mistake.
  it('says nothing about a file that had no ending', () => {
    expect(extensionWarning('README', 'NOTES')).toBeNull()
    expect(extensionWarning('README', 'README.md')).toBeNull()
  })

  it('says nothing about a name that has not changed', () => {
    expect(extensionWarning('brief.pdf', 'brief.pdf')).toBeNull()
  })
})
