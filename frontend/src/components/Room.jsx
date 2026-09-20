// Room - the connected room screen: layout only, per spec section 22.
//
//   LEFT:   Participants
//   CENTER: Conversation / transcript
//   RIGHT:  Room chat / context
//   BOTTOM: Mic, Mute, Leave room, Text input

import { useState } from 'react'
import ParticipantList from './ParticipantList'
import MessageList from './MessageList'
import Chat from './Chat'
import VoiceControls from './VoiceControls'
import BotStatus from './BotStatus'
import DebugPanel from './DebugPanel'
import { useParticipants } from '../hooks/useParticipants'
import { useChat } from '../hooks/useChat'
import { useTranscript } from '../hooks/useTranscript'
import { useDebugState } from '../hooks/useDebugState'

export default function Room({ room, connectionState, micEnabled, micError, onToggleMic, onLeave }) {
  const [debugOpen, setDebugOpen] = useState(false)
  const { humans, bots, isSpeaking } = useParticipants(room)
  const { turns, interimBySpeaker } = useTranscript(room)
  const { messages, send, sending, sendError } = useChat(room)
  const [tab, setTab] = useState('conversation') // mobile only; desktop shows all three
  const debugState = useDebugState(room)

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-[var(--color-bg)]">
      <header className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-panel)] px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-base">{'\u{1F3A4}'}</span>
          <span className="text-sm font-semibold text-[var(--color-text)]">Roxstar AI Voice Room</span>
        </div>
        <button
          type="button"
          onClick={() => setDebugOpen((v) => !v)}
          className="rounded-full border border-[var(--color-border)] px-3 py-1 text-xs text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
        >
          {debugOpen ? 'Hide debug' : 'Debug panel'}
        </button>
      </header>

      {connectionState !== 'connected' && (
        <p className="border-b border-[var(--color-warn)]/30 bg-[var(--color-warn)]/10 px-4 py-1.5 text-center text-xs text-[var(--color-warn)]">
          {connectionState === 'reconnecting' || connectionState === 'signalReconnecting'
            ? 'Connection lost - reconnecting…'
            : `Connection: ${connectionState}`}
        </p>
      )}

      <BotStatus bots={bots} />

      <nav className="flex border-b border-[var(--color-border)] bg-[var(--color-panel)] md:hidden">
        {[
          ['participants', `People (${humans.length + bots.length})`],
          ['conversation', 'Conversation'],
          ['chat', 'Chat'],
        ].map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`flex-1 py-2 text-xs font-medium ${
              tab === id
                ? 'border-b-2 border-[var(--color-accent)] text-[var(--color-text)]'
                : 'text-[var(--color-text-dim)]'
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[240px_1fr_280px]">
        <div className={`min-h-0 md:block ${tab === 'participants' ? '' : 'hidden'}`}>
          <ParticipantList humans={humans} bots={bots} isSpeaking={isSpeaking} />
        </div>
        <div className={`min-h-0 md:block ${tab === 'conversation' ? '' : 'hidden'}`}>
          <MessageList turns={turns} interimBySpeaker={interimBySpeaker} />
        </div>
        <div className={`min-h-0 md:block ${tab === 'chat' ? '' : 'hidden'}`}>
          <Chat messages={messages} contextSummary={debugState?.context?.current_topic} />
        </div>
      </div>

      {debugOpen && (
        <DebugPanel
          state={debugState}
          room={room}
          connectionState={connectionState}
          participants={[...humans, ...bots]}
          lastError={sendError || micError}
          onClose={() => setDebugOpen(false)}
        />
      )}

      <VoiceControls
        micEnabled={micEnabled}
        micError={micError}
        onToggleMic={onToggleMic}
        onLeave={onLeave}
        onSend={send}
        sending={sending}
        sendError={sendError}
        connectionState={connectionState}
      />
    </div>
  )
}
