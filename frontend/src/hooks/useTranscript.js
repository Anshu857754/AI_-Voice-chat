// useTranscript - the room's turn-by-turn conversation, voice AND text alike.
//
// Fed by the `roxstar.transcript` data topic, which the backend publishes
// from ONE place (RoomContextManager listener in app/orchestrator.py), so
// every committed turn - human or bot, spoken or typed - shows up here in the
// same order the shared context saw it. Interim (partial) transcripts are
// kept separately and are ephemeral: they are replaced by the next partial
// from that speaker and cleared once a final turn commits.

import { useCallback, useEffect, useRef, useState } from 'react'
import { onBus, BUS_EVENTS } from '../services/livekit'

const MAX_TURNS = 300

export function useTranscript(room) {
  const [turns, setTurns] = useState([])
  const [interimBySpeaker, setInterimBySpeaker] = useState({})
  const seenTurnIds = useRef(new Set())

  const handleEvent = useCallback((data) => {
    if (data.kind === 'interim') {
      setInterimBySpeaker((prev) => ({
        ...prev,
        [data.identity]: { text: data.text, name: data.name },
      }))
      return
    }
    if (data.kind === 'turn' || data.kind === 'chat') {
      const turnId = data.turn_id || `${data.identity}-${data.created_at}`
      if (seenTurnIds.current.has(turnId)) return
      seenTurnIds.current.add(turnId)

      setInterimBySpeaker((prev) => {
        if (!(data.identity in prev)) return prev
        const next = { ...prev }
        delete next[data.identity]
        return next
      })
      setTurns((prev) => {
        const next = [
          ...prev,
          {
            id: turnId,
            role: data.role || 'human',
            bot: data.bot || null,
            identity: data.identity,
            name: data.name,
            text: data.text,
            source: data.source || 'voice',
            language: data.language || null,
            interrupted: !!data.interrupted,
            createdAt: data.created_at || Date.now() / 1000,
          },
        ]
        return next.length > MAX_TURNS ? next.slice(next.length - MAX_TURNS) : next
      })
    }
  }, [])

  useEffect(() => {
    if (!room) {
      setTurns([])
      setInterimBySpeaker({})
      seenTurnIds.current.clear()
      return undefined
    }
    const offTranscript = onBus(room, BUS_EVENTS.TRANSCRIPT, handleEvent)
    const offDisconnected = onBus(room, BUS_EVENTS.DISCONNECTED, () => {
      setTurns([])
      setInterimBySpeaker({})
      seenTurnIds.current.clear()
    })
    return () => {
      offTranscript()
      offDisconnected()
    }
  }, [room, handleEvent])

  return { turns, interimBySpeaker }
}
