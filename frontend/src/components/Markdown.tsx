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

import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import styles from './Markdown.module.css'

const COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
}

export function Markdown({ source }: { source: string }) {
  return (
    <div className={styles.prose}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {source}
      </ReactMarkdown>
    </div>
  )
}
