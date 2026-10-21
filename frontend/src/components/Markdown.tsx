/**
 * A doc's markdown, drawn as a page.
 *
 * Through `react-markdown`, which builds React elements rather than an HTML
 * string: raw HTML in the source is shown as text and never parsed, so a doc
 * an agent wrote cannot put a script or a form on the page of whoever reads
 * it. GFM on top, because the docs agents write are full of tables, task lists
 * and fenced code, and plain CommonMark draws all three as punctuation.
 *
 * Links open in a new tab — a doc is something you read alongside the board,
 * and following a link out of it should not lose your place.
 */

import { Children, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { FiledItem, Person } from '../api/client'
import styles from './Markdown.module.css'
import { Tagged } from './primitives/Tagged'

const COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
}

/** The people and files whose `@` and `>` tags are drawn as tags in the prose. */
interface Tags {
  members: readonly Person[]
  files?: readonly FiledItem[] | undefined
}

/** Every plain run of text among `children`, drawn through `Tagged`. */
function tagged(children: ReactNode, tags: Tags): ReactNode {
  return Children.map(children, (child) =>
    typeof child === 'string' ? (
      <Tagged text={child} members={tags.members} files={tags.files} />
    ) : (
      child
    ),
  )
}

type TextProps = { node?: unknown; children?: ReactNode }

function taggedComponents(tags: Tags): Components {
  const wrap =
    <T extends keyof JSX.IntrinsicElements>(Tag: T) =>
    ({ node: _node, children, ...props }: TextProps) => {
      const Element = Tag as React.ElementType
      return <Element {...props}>{tagged(children, tags)}</Element>
    }
  return {
    ...COMPONENTS,
    p: wrap('p'),
    li: wrap('li'),
    td: wrap('td'),
    th: wrap('th'),
    h1: wrap('h1'),
    h2: wrap('h2'),
    h3: wrap('h3'),
    h4: wrap('h4'),
    h5: wrap('h5'),
    h6: wrap('h6'),
    strong: wrap('strong'),
    em: wrap('em'),
    del: wrap('del'),
  }
}

/**
 * `tags`, when given, keeps `@person` and `>file` drawn as tags inside the
 * rendered markdown, as they are in every other piece of prose on the board.
 */
export function Markdown({ source, tags }: { source: string; tags?: Tags }) {
  return (
    <div className={styles.prose}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={tags ? taggedComponents(tags) : COMPONENTS}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
