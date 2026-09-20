// useInteractionMode - the explicit "text" | "voice" switch.
//
//   text  (default)  typed chat; AI answers in TEXT ONLY; mic is off
//   voice            the user pressed Voice; AI answers in text + audio
//
// The mode is never inferred (not from the room name, not from the AI's reply).
// It changes only when the user presses Voice / Stop Voice. The worker is told
// through a `voice_mode` control message and makes the final call on whether a
// reply is spoken (app/routing/voice_policy.py). The worker publishes the set
// of identities it believes are in voice mode (`voice_users`); if that ever
// disagrees with the UI (e.g. the worker restarted) we re-send our mode, so the
// worker can never be in voice mode when the UI says text.

import { useCallback, useEffect, useRef, useState } from 'react'
import { sendControl } from '../services/livekit'

export function useInteractionMode({ room, micEnabled, onToggleMic, voiceUsers, resetKey }) {
  const [mode, setMode] = useState('text')
  const identity = room?.localParticipant?.identity

  const sentAt = useRef(0)

  const tell = useCallback(
    (enabled) => {
      sentAt.current = Date.now()
      return sendControl(room, { type: 'voice_mode', enabled }).catch(() => {})
    },
    [room],
  )

  const enterVoice = useCallback(() => {
    setMode('voice')
    tell(true)
    if (!micEnabled) onToggleMic()
  }, [tell, micEnabled, onToggleMic])

  const exitVoice = useCallback(() => {
    setMode('text')
    tell(false)
    if (micEnabled) onToggleMic()
  }, [tell, micEnabled, onToggleMic])

  // Opening / switching conversations never carries voice over: back to text, mic off.
  // (The worker clears its voice users on a conversation switch as well.)
  const lastReset = useRef(resetKey)
  useEffect(() => {
    if (lastReset.current === resetKey) return
    lastReset.current = resetKey
    setMode('text')
    if (micEnabled) onToggleMic()
  }, [resetKey, micEnabled, onToggleMic])

  // Reconcile with the worker's view (only once it has published one).
  useEffect(() => {
    if (!Array.isArray(voiceUsers) || !identity) return
    // The worker's published state lags our own message; give it a moment.
    if (Date.now() - sentAt.current < 3000) return
    const workerThinksVoice = voiceUsers.includes(identity)
    if (workerThinksVoice !== (mode === 'voice')) tell(mode === 'voice')
  }, [voiceUsers, identity, mode, tell])

  return { mode, enterVoice, exitVoice }
}
