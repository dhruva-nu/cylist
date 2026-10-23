import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

/** The card's components, each of which reaches its classes through a few stylesheets. */
const COMPONENTS = ['TaskCard', 'CardFace', 'CardMarks', 'CardSlotLists']
const DIR = 'src/routes/board'

function classesIn(file: string): Set<string> {
  const css = readFileSync(`${DIR}/${file}`, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  return new Set([...css.matchAll(/\.([A-Za-z_][\w-]*)/g)].map((match) => match[1] as string))
}

/** The stylesheet each name in a component stands for: `styles`, `shared`, `layout`. */
function importsOf(source: string): Map<string, string> {
  const found = new Map<string, string>()
  for (const match of source.matchAll(/import (\w+) from '\.\/([\w]+\.module\.css)'/g)) {
    found.set(match[1] as string, match[2] as string)
  }
  return found
}

describe('the card’s stylesheets', () => {
  it.each(COMPONENTS)('%s only asks for classes its stylesheets define', (component) => {
    // A class that moved to another stylesheet when this one was split from it
    // is `undefined` at run time, which draws as a card with no border and no
    // error anywhere. Nothing type-checks a CSS module, so this does.
    const source = readFileSync(`${DIR}/${component}.tsx`, 'utf8')
    const sheets = importsOf(source)
    expect(sheets.size).toBeGreaterThan(0)

    const missing: string[] = []
    for (const [name, file] of sheets) {
      const defined = classesIn(file)
      const pattern = new RegExp(
        `\\b${name}\\.(\\w+)|\\b${name}\\[\\s*['"]([\\w-]+)['"]\\s*\\]`,
        'g',
      )
      for (const match of source.matchAll(pattern)) {
        const wanted = (match[1] ?? match[2]) as string
        if (!defined.has(wanted)) missing.push(`${name}.${wanted} (${file})`)
      }
      for (const match of source.matchAll(new RegExp(`\\b${name}\\[\`(\\w+?_)\\$\\{`, 'g'))) {
        const prefix = match[1] as string
        if (![...defined].some((cls) => cls.startsWith(prefix)))
          missing.push(`${name}[\`${prefix}…\`]`)
      }
    }

    expect(missing).toEqual([])
  })

  it('has no class in two stylesheets, which would be two answers to what it looks like', () => {
    const files = [
      'TaskCard.module.css',
      'CardLayout.module.css',
      'CardFace.module.css',
      'CardMarks.module.css',
      'CardSlotLists.module.css',
    ]
    const seen = new Map<string, string>()
    const twice: string[] = []
    for (const file of files) {
      for (const cls of classesIn(file)) {
        const earlier = seen.get(cls)
        if (earlier) twice.push(`${cls}: ${earlier}, ${file}`)
        seen.set(cls, file)
      }
    }

    expect(twice).toEqual([])
  })
})
