// useMicPause - pause the local mic while an AI is speaking.
//
// With speakers and an open mic, the AI's own voice leaks back into the mic and
// gets transcribed as if the user had said it ("the AI talks to itself").
// Like a walkie-talkie, the published mic track is muted while any AI is in the
// `speaking` state and un-muted ~0.6s after it stops. A mic the user muted
// themselves is never un-muted here (we only undo our own mute).
//
// Returns `paused` so the UI can say "mic paused while AI speaks".

import { useEffect, useRef, useState } from 'react'
import { Track } from '../services/livekit'

const RESUME_DELAY_MS = 600

const micPublication = (room) => room.localParticipant.getTrackPublication(Track.Source.Microphone)

export function useMicPause(room, bots, micEnabled, enabled = true) {
  const aiSpeaking = bots.some((b) => b.botState === 'speaking')
  const [paused, setPaused] = useState(false)
  const autoMuted = useRef(false)

  useEffect(() => {
    if (!room || !enabled) return undefined

    if (aiSpeaking && micEnabled) {
      const pub = micPublication(room)
      if (pub && !pub.isMuted) {
        pub.mute().catch(() => {})
        autoMuted.current = true
        setPaused(true)
      }
      return undefined
    }

    if (!aiSpeaking && autoMuted.current) {
      const timer = setTimeout(() => {
        autoMuted.current = false
        setPaused(false)
        if (micEnabled) micPublication(room)?.unmute().catch(() => {})
      }, RESUME_DELAY_MS)
      return () => clearTimeout(timer)
    }
    return undefined
  }, [room, aiSpeaking, micEnabled, enabled])

  return paused && micEnabled
}
