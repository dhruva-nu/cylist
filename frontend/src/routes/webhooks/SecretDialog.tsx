import { useState } from 'react'
import { Modal, ModalBody } from '../../components/Modal'
import { Button } from '../../components/ui'
import styles from './ProjectHooks.module.css'

/**
 * A hook's signing secret, the one time it is shown: just after the hook is
 * made, or just after the secret is rotated.
 */
export function SecretDialog({
  name,
  secret,
  onClose,
}: {
  name: string
  secret: string
  onClose: () => void
}) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(secret)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <Modal
      title={`${name}'s signing secret`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={() => void copy()}>{copied ? 'Copied' : 'Copy'}</Button>
          <Button variant="go" onClick={onClose}>
            Done
          </Button>
        </>
      }
    >
      <ModalBody>
        <p>
          This is the only time it is shown. Give it to whatever receives this hook, so it can check
          each request&rsquo;s <code>X-Cylist-Signature</code> header.
        </p>
        <pre className={styles.secret}>{secret}</pre>
        <p className={styles.quiet}>
          The header is <code>t=&lt;unix time&gt;,v1=&lt;hex&gt;</code>, where the hex is
          HMAC-SHA256 of <code>&lt;t&gt;.&lt;body&gt;</code> under this secret.
        </p>
      </ModalBody>
    </Modal>
  )
}
