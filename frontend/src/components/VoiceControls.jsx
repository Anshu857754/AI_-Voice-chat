// VoiceControls - BOTTOM bar: mic, mute, leave room, text input.
//
// The text input calls the exact same `send` from useChat that a typed room
// message uses, so voice and text really do share one send path into the
// pipeline (spec: "Do not create a completely separate context system for text").

import { useState } from 'react'

function MicOnIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0M12 19v3M8 22h8" strokeLinecap="round" />
    </svg>
  )
}

function MicOffIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 3l18 18M9 9v3a3 3 0 0 0 4.6 2.55M15 9.34V5a3 3 0 0 0-5.94-.6" strokeLinecap="round" />
      <path d="M5 10a7 7 0 0 0 10.3 6.16M19 10a7 7 0 0 1-1.02 3.66M12 19v3M8 22h8" strokeLinecap="round" />
    </svg>
  )
}

function LeaveIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M16 17l5-5-5-5M21 12H9M13 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function VoiceControls({ micEnabled, micError, onToggleMic, onLeave, onSend, sending, sendError, connectionState }) {
  const online = connectionState === 'connected'
  const [draft, setDraft] = useState('')

  const submit = (e) => {
    e.preventDefault()
    if (!draft.trim() || !online || sending) return
    onSend(draft)
    setDraft('')
  }

  return (
    <div>
      {sendError && (
        <p className="border-t border-[var(--color-danger)]/30 bg-[var(--color-danger)]/10 px-4 py-2 text-xs text-[var(--color-danger)]">
          {sendError}
        </p>
      )}
      {micError && (
        <p className="border-t border-[var(--color-danger)]/30 bg-[var(--color-danger)]/10 px-4 py-2 text-xs text-[var(--color-danger)]">
          {micError}
        </p>
      )}
    <div className="flex items-center gap-3 border-t border-[var(--color-border)] bg-[var(--color-panel)] px-4 py-3">
      <button
        type="button"
        onClick={onToggleMic}
        aria-pressed={micEnabled}
        title={micEnabled ? 'Mute microphone' : 'Unmute microphone'}
        className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full border transition-colors ${
          micEnabled
            ? 'border-[var(--color-live)] bg-[var(--color-live)]/15 text-[var(--color-live)]'
            : 'border-[var(--color-border)] bg-[var(--color-panel-raised)] text-[var(--color-text-dim)]'
        }`}
      >
        {micEnabled ? <MicOnIcon /> : <MicOffIcon />}
      </button>

      <form onSubmit={submit} className="flex min-w-0 flex-1 items-center gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={!online}
          placeholder="Type a message… (Hindi, Hinglish ya English mein)"
          className="min-w-0 flex-1 rounded-full border border-[var(--color-border)] bg-[var(--color-panel-raised)] px-4 py-2.5 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-dim)] focus:border-[var(--color-accent)]"
        />
        <button
          type="submit"
          disabled={!draft.trim() || sending || !online}
          aria-label="Send message"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)] text-white transition-opacity disabled:opacity-30"
        >
          <SendIcon />
        </button>
      </form>

      <div className="hidden items-center gap-1.5 px-2 text-xs text-[var(--color-text-dim)] sm:flex">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            connectionState === 'connected' ? 'bg-[var(--color-live)]' : 'bg-[var(--color-warn)] animate-pulse'
          }`}
        />
        {online ? 'Connected' : connectionState === 'reconnecting' ? 'Reconnecting…' : connectionState}
      </div>

      <button
        type="button"
        onClick={onLeave}
        title="Leave room"
        className="flex h-11 shrink-0 items-center gap-2 rounded-full border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 px-4 text-sm font-medium text-[var(--color-danger)] transition-colors hover:bg-[var(--color-danger)]/20"
      >
        <LeaveIcon />
        <span className="hidden sm:inline">Leave</span>
      </button>
    </div>
    </div>
  )
}
