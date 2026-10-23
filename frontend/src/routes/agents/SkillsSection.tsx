/** The skills an agent on this project can be handed: uploaded, listed and deleted here. */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { api, type Skill } from '../../api/client'
import { affects, invalidates, queryKeys, useInvalidate } from '../../api/queries'
import { formatSize, formatStamp } from '../../components/format'
import { Button, EmptyState, ErrorNotice, cardStyles } from '../../components/ui'
import { usePermissions } from '../usePermissions'
import styles from './Agents.module.css'
import { Section } from './AgentsSection'
import { handlesOwnErrors } from '../../api/mutationErrors'

export function Skills({
  projectKey,
  announce,
}: {
  projectKey: string
  announce: (message: string) => void
}) {
  const invalidate = useInvalidate()
  const may = usePermissions(projectKey)
  const filePicker = useRef<HTMLInputElement>(null)
  const folderPicker = useRef<HTMLInputElement>(null)

  const skills = useQuery({
    queryKey: queryKeys.skills(projectKey),
    queryFn: ({ signal }) => api.listSkills(projectKey, signal),
  })

  async function refresh() {
    await invalidate(queryKeys.skills(projectKey))
  }

  const upload = useMutation({
    meta: { ...handlesOwnErrors, ...invalidates(...affects.skills) },
    // One at a time rather than in parallel: two uploads of the same name
    // would race to replace each other, and the survivor would be whichever
    // request the server happened to finish second.
    mutationFn: async (files: File[]) => {
      const replaced: string[] = []
      for (const file of files) {
        const alreadyThere = skills.data?.some((skill) => skill.name === file.name) ?? false
        await api.uploadSkill(projectKey, file)
        if (alreadyThere) replaced.push(file.name)
      }
      return { count: files.length, replaced }
    },
    onSuccess: async ({ count, replaced }) => {
      await refresh()
      const noun = count === 1 ? 'skill' : 'skills'
      announce(
        replaced.length
          ? `${count} ${noun} uploaded. ${replaced.join(', ')} replaced an earlier version.`
          : `${count} ${noun} uploaded.`,
      )
    },
  })

  const uploadFolder = useMutation({
    meta: { ...handlesOwnErrors, ...invalidates(...affects.skills) },
    // One skill, not one per file: a folder is a skill precisely because its
    // SKILL.md points at the other files in it.
    mutationFn: async (files: File[]) => {
      const folder = files[0]?.webkitRelativePath.split('/')[0] ?? ''
      if (!folder) {
        // Every browser that offers a directory picker fills this in. One
        // that somehow did not would otherwise send a nameless folder.
        throw new Error(
          'This browser did not say which folder those files came from. ' +
            'Upload a zip of the folder instead.',
        )
      }
      const alreadyThere = skills.data?.some((skill) => skill.name === `${folder}.zip`) ?? false
      return { skill: await api.uploadSkillFolder(projectKey, folder, files), alreadyThere }
    },
    onSuccess: async ({ skill, alreadyThere }) => {
      await refresh()
      announce(
        alreadyThere
          ? `${skill.name} uploaded, replacing the version that was there.`
          : `${skill.name} uploaded.`,
      )
    },
  })

  const deleteSkill = useMutation({
    meta: { ...handlesOwnErrors, ...invalidates(...affects.skills) },
    mutationFn: ({ id }: { id: string; name: string }) => api.deleteSkill(id),
    onSuccess: async (_gone, { name }) => {
      await refresh()
      announce(`${name} deleted.`)
    },
  })

  return (
    <Section
      title="Skills"
      blurb="Packaged jobs an agent can be handed — a page of markdown, or a whole folder of instructions, scripts and references. Uploading a name that already exists replaces it: the name is the skill, so a second upload is a new version of it."
      count={skills.data?.length}
      actions={
        may('agents') ? (
          <>
            {/* The real input, driven by the button beside it: a bare file
                input cannot be styled to match anything else on the page. */}
            <input
              ref={filePicker}
              type="file"
              multiple
              className="visually-hidden"
              onChange={(event) => {
                const chosen = Array.from(event.target.files ?? [])
                if (chosen.length) upload.mutate(chosen)
                // Cleared so that picking the same file twice fires twice —
                // which is exactly what re-uploading a corrected skill is.
                event.target.value = ''
              }}
            />
            {/* Directory picking is an attribute, not a mode: the same input
                with webkitdirectory set opens a folder chooser, and each
                File it hands back carries its path inside the folder. */}
            <input
              ref={folderPicker}
              type="file"
              multiple
              webkitdirectory=""
              className="visually-hidden"
              onChange={(event) => {
                const chosen = Array.from(event.target.files ?? [])
                if (chosen.length) uploadFolder.mutate(chosen)
                event.target.value = ''
              }}
            />
            <Button
              variant="go"
              disabled={upload.isPending || uploadFolder.isPending}
              onClick={() => filePicker.current?.click()}
            >
              {upload.isPending ? 'Uploading…' : '+ Upload a skill'}
            </Button>
            <Button
              disabled={upload.isPending || uploadFolder.isPending}
              onClick={() => folderPicker.current?.click()}
            >
              {uploadFolder.isPending ? 'Uploading…' : '+ Upload a folder'}
            </Button>
          </>
        ) : null
      }
    >
      <ErrorNotice error={upload.error ?? uploadFolder.error ?? deleteSkill.error} />
      <ErrorNotice error={skills.error} />

      {skills.isPending ? <EmptyState>Loading skills…</EmptyState> : null}

      {skills.data?.length === 0 ? (
        <EmptyState>
          No skills yet.
          <br />
          Upload the markdown an agent should follow here — a board to tidy, a report to write — or
          a whole folder, SKILL.md and the scripts it uses, as one skill.
        </EmptyState>
      ) : null}

      {skills.data?.length ? (
        <div className={styles.skills}>
          {skills.data.map((skill) => (
            <SkillRow
              key={skill.id}
              skill={skill}
              busy={deleteSkill.isPending}
              onDelete={() => deleteSkill.mutate({ id: skill.id, name: skill.name })}
            />
          ))}
        </div>
      ) : null}
    </Section>
  )
}

function SkillRow({
  skill,
  busy,
  onDelete,
}: {
  skill: Skill
  busy: boolean
  onDelete: () => void
}) {
  const [confirming, setConfirming] = useState(false)

  return (
    <div className={`${cardStyles.card} ${styles.skill}`}>
      <span className={styles.skillMark} aria-hidden="true">
        {fileTypeMark(skill.name)}
      </span>
      <div className={styles.skillText}>
        <a className={styles.skillName} href={api.skillDownloadUrl(skill.id)} download={skill.name}>
          {skill.name}
        </a>
        {skill.description ? <span className={styles.skillNote}>{skill.description}</span> : null}
        <span className={styles.skillMeta}>
          {formatSize(skill.size)} · {formatStamp(skill.created_at)}
          {skill.added_by ? <> · {skill.added_by.name}</> : null}
        </span>
      </div>
      {confirming ? (
        <div className={styles.confirm}>
          <span>Delete it?</span>
          <Button small danger disabled={busy} onClick={onDelete}>
            Delete
          </Button>
          <Button small onClick={() => setConfirming(false)}>
            Keep
          </Button>
        </div>
      ) : (
        <Button small onClick={() => setConfirming(true)}>
          Delete
        </Button>
      )}
    </div>
  )
}

/** The four-or-fewer characters that stand in for a file's type. */
function fileTypeMark(name: string): string {
  const extension = name.includes('.') ? (name.split('.').pop() ?? '') : ''
  return extension ? extension.slice(0, 4).toUpperCase() : 'FILE'
}
