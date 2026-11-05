import { describe, expect, it } from 'vitest'
import { imageFiles, imageMarkdown, imageSrc, insertBlock } from './descriptionImages'

describe('imageMarkdown', () => {
  it('uses the file name, without its extension, as the alt text', () => {
    expect(imageMarkdown('checkout error.png', '/api/v1/images/1')).toBe(
      '![checkout error](/api/v1/images/1)',
    )
  })

  it('keeps brackets out of the alt text, and never leaves it empty', () => {
    expect(imageMarkdown('[draft].png', '/u')).toBe('![draft](/u)')
    expect(imageMarkdown('.png', '/u')).toBe('![image](/u)')
  })
})

describe('insertBlock', () => {
  it('puts the text on a line of its own in the middle of a sentence', () => {
    expect(insertBlock('before after', 7, 'IMG')).toEqual({
      value: 'before \nIMG\nafter',
      caret: 12,
    })
  })

  it('adds no blank lines where there are already line breaks', () => {
    expect(insertBlock('one\n\ntwo', 4, 'IMG')).toEqual({ value: 'one\nIMG\ntwo', caret: 7 })
    expect(insertBlock('', 0, 'IMG')).toEqual({ value: 'IMG\n', caret: 4 })
  })

  it('clamps a caret that the text has since moved out from under', () => {
    expect(insertBlock('short', 99, 'IMG').value).toBe('short\nIMG\n')
  })
})

describe('imageFiles', () => {
  it('keeps the images and drops everything else', () => {
    const png = new File(['x'], 'a.png', { type: 'image/png' })
    const pdf = new File(['x'], 'b.pdf', { type: 'application/pdf' })
    expect(imageFiles([png, pdf])).toEqual([png])
    expect(imageFiles(null)).toEqual([])
  })
})

describe('imageSrc', () => {
  it('puts our own images under the base path', () => {
    expect(imageSrc('/api/v1/images/1', '/dev_1')).toBe('/dev_1/api/v1/images/1')
    expect(imageSrc('/api/v1/images/1', '')).toBe('/api/v1/images/1')
  })

  it('leaves anyone else’s alone', () => {
    expect(imageSrc('https://example.com/a.png', '/dev_1')).toBe('https://example.com/a.png')
    expect(imageSrc(undefined, '/dev_1')).toBeUndefined()
  })
})
