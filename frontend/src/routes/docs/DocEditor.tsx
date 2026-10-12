import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent, type KeyboardEvent } from 'react'
import { Markdown } from '../../components/Markdown'
import { Button, ErrorNotice } from '../../components/ui'
import styles from './ProjectDocs.module.css'

/** What the editor writes: a title, and the markdown under it. */
export interface DocDraft {
  title: string
  body: string
}

/**
 * Title and markdown, with the page it will be beside it.
 *
 * Side by side on a wide screen, so what is being written and how it will
 * read are one glance apart; stacked on a narrow one, where "Preview" is a
 * toggle instead. ⌘/Ctrl+S saves, because a textarea this size is where
 * people reach for it without thinking.
 */
export function DocEditor({
  heading,
  initial,
  onSave,
  onCancel,
}: {
  heading: string
  initial: DocDraft
  onSave: (doc: DocDraft) => Promise<void>
  onCancel: () => void
}) {
  const [title, setTitle] = useState(initial.title)
  const [body, setBody] = useState(initial.body)
  const [preview, setPreview] = useState(false)
  const save = useMutation({ mutationFn: onSave })

  function submit(event?: FormEvent) {
    event?.preventDefault()
    if (!title.trim() || save.isPending) return
    save.mutate({ title: title.trim(), body })
  }

  function onKeyDown(event: KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
      event.preventDefault()
      submit()
    }
  }

  const dirty = title !== initial.title || body !== initial.body

  return (
    <form className={styles.editor} onSubmit={submit} onKeyDown={onKeyDown}>
      <div className={styles.editorHead}>
        <h2 className={styles.editorHeading}>{heading}</h2>
        <div className={styles.readerTools}>
          <button
            type="button"
            className={styles.previewToggle}
            aria-pressed={preview}
            onClick={() => setPreview((shown) => !shown)}
          >
            Preview
          </button>
          <Button
            small
            variant="ghost"
            onClick={() => {
              if (dirty && !window.confirm('Discard your changes?')) return
              onCancel()
            }}
          >
            Cancel
          </Button>
          <Button small variant="go" type="submit" disabled={save.isPending || !title.trim()}>
            {save.isPending ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
      <ErrorNotice error={save.error} />
      <input
        className={styles.titleInput}
        aria-label="Title"
        placeholder="Title"
        value={title}
        maxLength={200}
        autoFocus={!initial.title}
        onChange={(event) => setTitle(event.target.value)}
      />
      <div className={[styles.split, preview && styles.previewing].filter(Boolean).join(' ')}>
        <textarea
          className={styles.bodyInput}
          aria-label="Markdown"
          placeholder={
            '# Heading\n\nWrite in markdown — tables, task lists and code blocks all work.'
          }
          value={body}
          autoFocus={Boolean(initial.title)}
          onChange={(event) => setBody(event.target.value)}
        />
        <div className={styles.preview} aria-label="Preview">
          {body.trim() ? <Markdown source={body} /> : <p className={styles.quiet}>Nothing yet.</p>}
        </div>
      </div>
    </form>
  )
}
