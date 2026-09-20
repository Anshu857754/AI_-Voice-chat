// useRoom - owns the LiveKit connection lifecycle for the whole app.
//
// Downstream state (participants, chat, transcript, bot status) is NOT
// threaded through here. Each of those hooks subscribes directly to
// `room.roxstarBus` (see services/livekit.js) once it has a `room` instance,
// which is what lets them mount after connection without missing events.

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  connectToRoom,
  disconnectRoom,
  setMicrophoneEnabled,
  onBus,
  BUS_EVENTS,
  ConnectionState,
} from '../services/livekit'

const IDENTITY_STORAGE_KEY = 'roxstar.identity'

function loadStoredIdentity() {
  try {
    return sessionStorage.getItem(IDENTITY_STORAGE_KEY) || undefined
  } catch {
    return undefined
  }
}

function describeMicError(err) {
  const name = err?.name || ''
  if (!window.isSecureContext) {
    return 'Mic sirf http://localhost ya https par chalta hai. Address bar mein localhost:5173 use karo (IP address nahi).'
  }
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
    return 'Browser ne mic permission block kar rakhi hai. Address bar ke lock/mic icon par click karke Microphone ko Allow karo, phir page reload karo.'
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return 'Koi microphone device nahi mila. Mic plug in karo ya Windows sound settings check karo.'
  }
  if (name === 'NotReadableError') {
    return 'Mic kisi aur app (Zoom/Meet/Teams) ne use kar rakha hai. Use band karke dobara try karo.'
  }
  return `Mic on nahi ho paya: ${err?.message || name || 'unknown error'}`
}

function storeIdentity(identity) {
  try {
    sessionStorage.setItem(IDENTITY_STORAGE_KEY, identity)
  } catch {
    // Private browsing / storage disabled - not fatal, just means a refresh
    // will be treated as a new participant.
  }
}

export function useRoom({ tokenEndpoint }) {
  const [room, setRoom] = useState(null)
  const [connectionState, setConnectionState] = useState(ConnectionState.Disconnected)
  const [error, setError] = useState(null)
  const [micEnabled, setMicEnabled] = useState(false)
  const [micError, setMicError] = useState(null)
  const [localIdentity, setLocalIdentity] = useState(null)
  const roomRef = useRef(null)

  const join = useCallback(
    async (displayName, roomName) => {
      setError(null)
      try {
        const { room: lkRoom, identity } = await connectToRoom({
          tokenEndpoint,
          displayName,
          room: roomName,
          identity: loadStoredIdentity(),
        })
        storeIdentity(identity)
        roomRef.current = lkRoom
        setLocalIdentity(identity)
        setRoom(lkRoom)
        setConnectionState(ConnectionState.Connected)
        return lkRoom
      } catch (err) {
        setError(err.message || 'Failed to join the room')
        throw err
      }
    },
    [tokenEndpoint],
  )

  const leave = useCallback(async () => {
    if (roomRef.current) await disconnectRoom(roomRef.current)
    roomRef.current = null
    setRoom(null)
    setConnectionState(ConnectionState.Disconnected)
    setMicEnabled(false)
    setMicError(null)
  }, [])

  const toggleMic = useCallback(async () => {
    if (!room) return
    const next = !micEnabled
    try {
      await setMicrophoneEnabled(room, next)
      setMicEnabled(room.localParticipant.isMicrophoneEnabled)
      setMicError(null)
    } catch (err) {
      // getUserMedia failures used to be swallowed silently, so the button
      // just looked dead. Surface a readable reason instead.
      console.error('Microphone toggle failed', err)
      setMicEnabled(false)
      setMicError(describeMicError(err))
    }
  }, [room, micEnabled])

  // Track connection state / disconnection for the room this hook itself just
  // opened. Safe to subscribe immediately after join() sets `room`.
  useEffect(() => {
    if (!room) return undefined
    const offState = onBus(room, BUS_EVENTS.CONNECTION_STATE, setConnectionState)
    const offDisconnected = onBus(room, BUS_EVENTS.DISCONNECTED, () => {
      roomRef.current = null
      setRoom(null)
      setConnectionState(ConnectionState.Disconnected)
      setMicEnabled(false)
    })
    return () => {
      offState()
      offDisconnected()
    }
  }, [room])

  useEffect(() => {
    return () => {
      if (roomRef.current) disconnectRoom(roomRef.current)
    }
  }, [])

  return {
    room,
    connectionState,
    isConnected: connectionState === ConnectionState.Connected,
    isReconnecting: connectionState === ConnectionState.Reconnecting,
    error,
    micEnabled,
    micError,
    localIdentity,
    join,
    leave,
    toggleMic,
  }
}
