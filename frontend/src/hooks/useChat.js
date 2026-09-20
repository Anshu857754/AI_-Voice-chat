// useChat - room text chat.
//
// Sends on the same `lk.chat` topic the backend pipeline reads (see
// app/livekit_rt/events.py: `_read_chat` -> orchestrator._handle_chat), so a
// typed message goes through STT-normalize -> router -> context -> LLM exactly
// like a spoken one. Bot replies are mirrored back onto this same topic
// (BotAgent.send_chat), so this hook also renders the bots' answers.

import { useCallback, useEffect, useRef, useState } from 'react'
import { onBus, sendChatMessage, BUS_EVENTS } from '../services/livekit'

export function useChat(room) {
  const [messages, setMessages] = useState([])
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState(null)
  const seq = useRef(0)

  useEffect(() => {
    if (!room) {
      setMessages([])
      return undefined
    }
    const offChat = onBus(room, BUS_EVENTS.CHAT, (msg) => {
      seq.current += 1
      const id = msg.streamId || `${msg.at}-${seq.current}`
      // De-dupe by the LiveKit text-stream id so a re-delivered message
      // is never rendered twice.
      setMessages((prev) => (prev.some((m) => m.id === id) ? prev : [...prev, { ...msg, id }]))
    })
    const offDisconnected = onBus(room, BUS_EVENTS.DISCONNECTED, () => setMessages([]))
    return () => {
      offChat()
      offDisconnected()
    }
  }, [room])

  const send = useCallback(
    async (text) => {
      const trimmed = text.trim()
      if (!trimmed || !room) return
      setSending(true)
      setSendError(null)
      try {
        await sendChatMessage(room, trimmed)
        // Optimistic local echo; the backend does not echo the sender's own
        // message back, it only broadcasts bot replies on this topic. This
        // keeps the chat panel responsive.
        seq.current += 1
        setMessages((prev) => [
          ...prev,
          {
            id: `local-${Date.now()}-${seq.current}`,
            identity: room.localParticipant.identity,
            name: room.localParticipant.name || 'You',
            text: trimmed,
            isLocal: true,
            at: Date.now(),
          },
        ])
      } catch (err) {
        console.error('Chat send failed', err)
        setSendError(`Message bheja nahi ja saka: ${err?.message || 'unknown error'}`)
      } finally {
        setSending(false)
      }
    },
    [room],
  )

  return { messages, send, sending, sendError }
}
