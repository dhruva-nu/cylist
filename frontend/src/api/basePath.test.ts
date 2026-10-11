import { describe, expect, it } from 'vitest'
import { normaliseBasePath, readBasePath, stripBase, withBase } from './basePath'

const page = (content: string | null) =>
  ({
    querySelector: () => (content === null ? null : ({ content } as HTMLMetaElement)),
  }) as Pick<Document, 'querySelector'>

describe('normaliseBasePath', () => {
  it('is one leading slash and none trailing', () => {
    expect(normaliseBasePath('/dev_1')).toBe('/dev_1')
    expect(normaliseBasePath('dev_1/')).toBe('/dev_1')
    expect(normaliseBasePath(' //dev_1// ')).toBe('/dev_1')
  })

  it('is empty at the site root', () => {
    expect(normaliseBasePath('')).toBe('')
    expect(normaliseBasePath('/')).toBe('')
    expect(normaliseBasePath(null)).toBe('')
    expect(normaliseBasePath(undefined)).toBe('')
  })
})

describe('readBasePath', () => {
  it('reads the meta tag the backend wrote', () => {
    expect(readBasePath(page('/dev_1'))).toBe('/dev_1')
  })

  it('is the root when the tag is empty, missing, or there is no document', () => {
    expect(readBasePath(page(''))).toBe('')
    expect(readBasePath(page(null))).toBe('')
    expect(readBasePath(undefined)).toBe('')
  })
})

describe('withBase', () => {
  it('puts an address under the base path', () => {
    expect(withBase('/api/v1', '/dev_1')).toBe('/dev_1/api/v1')
    expect(withBase('/api/v1/items/x/download', '/dev_1')).toBe('/dev_1/api/v1/items/x/download')
  })

  it('leaves it alone at the root', () => {
    expect(withBase('/api/v1', '')).toBe('/api/v1')
  })
})

describe('stripBase', () => {
  it('takes the base path off a pathname', () => {
    expect(stripBase('/dev_1/invite/abc', '/dev_1')).toBe('/invite/abc')
    expect(stripBase('/dev_1', '/dev_1')).toBe('/')
  })

  it('does not take off a longer name that only starts the same', () => {
    expect(stripBase('/dev_10/invite/abc', '/dev_1')).toBe('/dev_10/invite/abc')
  })

  it('is the identity at the root', () => {
    expect(stripBase('/invite/abc', '')).toBe('/invite/abc')
  })
})
