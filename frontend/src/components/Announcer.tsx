import { useAnnouncement } from './announcements'
import { Overlay } from './Overlay'

/** The app's one live region, where `announce` is spoken. Renders nothing visible. */
export function Announcer() {
  const message = useAnnouncement()
  return (
    <Overlay>
      <p className="visually-hidden" role="status" aria-live="polite">
        {message}
      </p>
    </Overlay>
  )
}
