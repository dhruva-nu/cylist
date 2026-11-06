/**
 * A card's description, with images (CYLIST-182).
 *
 * Paste a screenshot, drop a file, or pick one: each is uploaded to the
 * project and its markdown — `![name](/api/v1/images/{id})` — goes into the
 * text where the caret was. The description only ever holds that reference;
 * the bytes are in the blob store, not the card.
 */

import { useMutation } from '@tanstack/react-query'
import { useRef } from 'react'
import { api, type FiledItem, type Person } from '../../api/client'
import { handlesOwnErrors } from '../../api/mutationErrors'
import { IMAGE_TYPES, imageFiles, imageMarkdown, insertBlock } from '../descriptionImages'
import { Field } from '../Modal'
import { MentionBox } from '../Mentions'
import { Button, ErrorNotice } from '../ui'
import styles from './FormFields.module.css'

export function DescriptionField({
  projectKey,
  value,
  onChange,
  members,
  files,
}: {
  projectKey: string
  value: string
  onChange: (value: string) => void
  members: Person[]
  files: readonly FiledItem[]
}) {
  const picker = useRef<HTMLInputElement>(null)
  // An upload finishes after the person has gone on typing, so what it is
  // inserted into is the text as it is then, not as it was when it began.
  const latest = useRef(value)
  latest.current = value

  const upload = useMutation({
    meta: handlesOwnErrors,
    // One at a time, each inserted as it lands: two screenshots pasted
    // together read top to bottom in the order they were given.
    mutationFn: async ({ images, at }: { images: File[]; at: number }) => {
      let caret = at
      for (const image of images) {
        const stored = await api.uploadImage(projectKey, image)
        const next = insertBlock(latest.current, caret, imageMarkdown(image.name, stored.url))
        latest.current = next.value
        caret = next.caret
        onChange(next.value)
      }
    },
  })

  return (
    <Field
      label="Description"
      required
      hint={
        upload.isPending
          ? 'Uploading image…'
          : 'Type @ to tag someone on the project, > to tag one of its files. Paste or drop an image to add it.'
      }
    >
      <MentionBox
        multiline
        value={value}
        onChange={onChange}
        members={members}
        files={files}
        placeholder="What done looks like"
        onImages={(images, at) => upload.mutate({ images, at })}
      />
      <div className={styles.imageRow}>
        <input
          ref={picker}
          type="file"
          accept={IMAGE_TYPES}
          multiple
          className="visually-hidden"
          tabIndex={-1}
          onChange={(event) => {
            const chosen = imageFiles(event.target.files)
            if (chosen.length) upload.mutate({ images: chosen, at: latest.current.length })
            event.target.value = ''
          }}
        />
        <Button small disabled={upload.isPending} onClick={() => picker.current?.click()}>
          {upload.isPending ? 'Uploading…' : '+ Add image'}
        </Button>
      </div>
      <ErrorNotice error={upload.error} />
    </Field>
  )
}
