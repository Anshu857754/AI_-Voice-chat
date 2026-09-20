// useParticipants - live roster of humans + AI Dost + AI Sathi.
//
// Bot participants are never synthesized here: they only appear once the
// agent worker has actually joined LiveKit and published attributes with
// role="bot" (see app/agents/base.py). If the worker is down, the bot simply
// is not in this list - matching the "do not fake AI participants" rule.
//
// Initial state comes from a direct snapshot (not a push event), so this
// still works correctly even though the hook mounts after the room already
// connected and possibly already has participants in it.

import { useEffect, useState } from 'react'
import { onBus, snapshotParticipants, BUS_EVENTS } from '../services/livekit'

export function useParticipants(room) {
  const [participants, setParticipants] = useState(() => (room ? snapshotParticipants(room) : []))
  const [activeSpeakers, setActiveSpeakers] = useState(new Set())

  useEffect(() => {
    if (!room) {
      setParticipants([])
      setActiveSpeakers(new Set())
      return undefined
    }
    setParticipants(snapshotParticipants(room))
    const offParticipants = onBus(room, BUS_EVENTS.PARTICIPANTS, setParticipants)
    const offSpeakers = onBus(room, BUS_EVENTS.ACTIVE_SPEAKERS, (identities) =>
      setActiveSpeakers(new Set(identities)),
    )
    return () => {
      offParticipants()
      offSpeakers()
    }
  }, [room])

  const humans = participants.filter((p) => !p.isBot)
  const bots = participants.filter((p) => p.isBot)

  return {
    participants,
    humans,
    bots,
    activeSpeakers,
    isSpeaking: (identity) => activeSpeakers.has(identity),
  }
}
