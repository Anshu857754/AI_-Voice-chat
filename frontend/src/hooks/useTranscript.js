// useTranscript - the OPEN conversation's turn-by-turn transcript.
//
//   history  loaded from the API when a conversation opens (latest page, then
//            "load earlier" pages on demand) - never the whole history at once
//   live     new turns from the `roxstar.transcript` data topic, accepted only
//            if they carry THIS conversation's id
//
// The two are merged by turn id, so a turn that arrives while history is still
// loading is never duplicated. Interim (partial) transcripts are ephemeral and
// cleared once a final turn commits.

import { useCallback, useEffect, useRef, useState } from 'react'
import { onBus, BUS_EVENTS } from '../services/livekit'

const MAX_TURNS = 600
const PAGE = 50

const toTurn = (m, restored) => ({
  id: m.turn_id,
  role: m.role || 'human',
  bot: m.bot || null,
  identity: m.identity,
  name: m.name,
  text: m.text,
  source: m.source || 'text',
  language: m.language || null,
  interrupted: !!m.interrupted,
  createdAt: m.created_at,
  restored,
})

export function useTranscript(room, { api, conversationId, reloadKey = 0 } = {}) {
  const [turns, setTurns] = useState([])
  const [interimBySpeaker, setInterimBySpeaker] = useState({})
  const [status, setStatus] = useState('loading') // loading | ready | error
  const [error, setError] = useState(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const seen = useRef(new Set())
  const idRef = useRef(conversationId)
  useEffect(() => {
    idRef.current = conversationId
  }, [conversationId])

  const handleEvent = useCallback((data) => {
    if (data.kind === 'interim') {
      setInterimBySpeaker((prev) => ({ ...prev, [data.identity]: { text: data.text, name: data.name } }))
      return
    }
    if (data.kind !== 'turn' && data.kind !== 'chat') return
    // Only this conversation's turns: a late event from the previous chat is dropped.
    if (!idRef.current || data.conversation_id !== idRef.current) return
    const turnId = data.turn_id || `${data.identity}-${data.created_at}`
    if (seen.current.has(turnId)) return
    seen.current.add(turnId)
    setInterimBySpeaker((prev) => {
      if (!(data.identity in prev)) return prev
      const next = { ...prev }
      delete next[data.identity]
      return next
    })
    setTurns((prev) => {
      const next = [...prev, toTurn({ ...data, turn_id: turnId, created_at: data.created_at || Date.now() / 1000 }, false)]
      return next.length > MAX_TURNS ? next.slice(next.length - MAX_TURNS) : next
    })
  }, [])

  const load = useCallback(() => {
    let cancelled = false
    seen.current = new Set()
    setTurns([])
    setInterimBySpeaker({})
    setHasMore(false)
    setError(null)
    if (!conversationId || !api) {
      setStatus('loading')
      return () => {}
    }
    setStatus('loading')
    api
      .messages(conversationId, { limit: PAGE })
      .then((r) => {
        if (cancelled) return
        const restored = []
        for (const m of r.messages) {
          if (seen.current.has(m.turn_id)) continue
          seen.current.add(m.turn_id)
          restored.push(toTurn(m, true))
        }
        // Keep any live turn that raced in while history was loading.
        setTurns((prev) => [...restored, ...prev].sort((a, b) => a.createdAt - b.createdAt))
        setHasMore(r.has_more)
        setStatus('ready')
      })
      .catch((err) => {
        if (cancelled) return
        setError(err.message)
        setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [api, conversationId])

  // (Re)load when the conversation changes, or after it was cleared (`reloadKey`).
  useEffect(() => load(), [load, reloadKey])

  useEffect(() => {
    if (!room) return undefined
    const offTranscript = onBus(room, BUS_EVENTS.TRANSCRIPT, handleEvent)
    return offTranscript
  }, [room, handleEvent])

  const loadEarlier = useCallback(async () => {
    if (loadingEarlier || !hasMore || !conversationId) return
    const oldest = turns[0]?.createdAt
    setLoadingEarlier(true)
    try {
      const r = await api.messages(conversationId, { limit: PAGE, before: oldest })
      if (idRef.current !== conversationId) return
      const older = []
      for (const m of r.messages) {
        if (seen.current.has(m.turn_id)) continue
        seen.current.add(m.turn_id)
        older.push(toTurn(m, true))
      }
      setTurns((prev) => [...older, ...prev])
      setHasMore(r.has_more)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoadingEarlier(false)
    }
  }, [api, conversationId, hasMore, loadingEarlier, turns])

  return { turns, interimBySpeaker, status, error, hasMore, loadingEarlier, loadEarlier, retry: load }
}
