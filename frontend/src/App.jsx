import { useCallback, useState } from 'react'
import JoinScreen from './components/JoinScreen'
import Room from './components/Room'
import { useRoom } from './hooks/useRoom'
import { ConnectionState } from './services/livekit'

const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT || 'http://localhost:8000'
const DEFAULT_ROOM = import.meta.env.VITE_DEFAULT_ROOM || ''

export default function App() {
  const { room, connectionState, error, micEnabled, micError, join, leave, toggleMic } = useRoom({
    tokenEndpoint: TOKEN_ENDPOINT,
  })
  const [joining, setJoining] = useState(false)

  const handleJoin = useCallback(
    async (name, roomName) => {
      setJoining(true)
      try {
        await join(name, roomName)
      } catch {
        // error state is already surfaced by useRoom
      } finally {
        setJoining(false)
      }
    },
    [join],
  )

  // Stay in the room while LiveKit is transparently reconnecting; only fall
  // back to the join screen once we are truly disconnected.
  const inRoom =
    room && (connectionState === ConnectionState.Connected || connectionState === ConnectionState.Reconnecting || connectionState === ConnectionState.SignalReconnecting)
  if (!inRoom) {
    return (
      <JoinScreen onJoin={handleJoin} joining={joining} error={error} defaultRoom={DEFAULT_ROOM} />
    )
  }

  return (
    <Room
      room={room}
      connectionState={connectionState}
      micEnabled={micEnabled}
      micError={micError}
      onToggleMic={toggleMic}
      onLeave={leave}
    />
  )
}
