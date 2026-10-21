import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Person } from '../api/client'
import { Markdown } from './Markdown'

describe('Markdown', () => {
  it('renders markdown syntax as elements', () => {
    const html = renderToStaticMarkup(<Markdown source={'# Title\n\n- one\n- **two**'} />)
    expect(html).toContain('<h1>Title</h1>')
    expect(html).toContain('<li>one</li>')
    expect(html).toContain('<strong>two</strong>')
  })

  it('shows raw html as text', () => {
    const html = renderToStaticMarkup(<Markdown source={'<script>x</script>'} />)
    expect(html).not.toContain('<script>')
  })

  it('keeps @mentions drawn as tags when given people', () => {
    const ada = { name: 'Ada', title: 'Eng' } as Person
    const html = renderToStaticMarkup(
      <Markdown source={'ping @Ada about **this**'} tags={{ members: [ada] }} />,
    )
    expect(html).toContain('title="Ada — Eng"')
    expect(html).toMatch(/<strong>.*this.*<\/strong>/)
  })
})
