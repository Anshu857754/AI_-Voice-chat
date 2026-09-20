// MessageList - CENTER panel: the live, shared conversation transcript.
//
// Renders every committed turn (voice AND text, human AND bot - the same
// single-context requirement the backend enforces) in order, plus a live
// interim caption line per currently-speaking human. This is the room's
// record of what was actually said, not a chat log of what was typed.

import { useEffect, useRef } from 'react'

const BOT_COLOR = { dost: 'var(--color-dost)', sathi: 'var(--color-sathi)' }
const BOT_LABEL = { dost: 'AI Dost', sathi: 'AI Sathi' }

function SourceIcon({ source }) {
  if (source === 'text') {
    return (
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M4 4h16v12H8l-4 4V4z" strokeLinejoin="round" />
      </svg>
    )
  }
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0M12 19v3" strokeLinecap="round" />
    </svg>
  )
}

function formatTime(epochSeconds) {
  const d = new Date(epochSeconds * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function TurnRow({ turn }) {
  const isBot = turn.role === 'bot'
  const accent = isBot ? BOT_COLOR[turn.bot] || 'var(--color-accent)' : 'var(--color-live)'
  const name = isBot ? BOT_LABEL[turn.bot] || turn.name : turn.name

  return (
    <div className="flex gap-3 px-4 py-2.5">
      <div
        className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold"
        style={{ background: `${accent}22`, color: accent }}
      >
        {isBot ? '\u{1F916}' : (name || '?').slice(0, 1).toUpperCase()}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium" style={{ color: accent }}>
            {name}
          </span>
          <span className="flex items-center gap-1 text-[10px] text-[var(--color-text-dim)]">
            <SourceIcon source={turn.source} />
          </span>
          <span className="text-[10px] text-[var(--color-text-dim)]">{formatTime(turn.createdAt)}</span>
          {turn.interrupted && (
            <span className="rounded-full bg-[var(--color-danger)]/15 px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-danger)]">
              interrupted
            </span>
          )}
        </div>
        <p className="mt-0.5 text-sm leading-relaxed text-[var(--color-text)]">{turn.text}</p>
      </div>
    </div>
  )
}

function InterimRow({ identity, data }) {
  return (
    <div key={identity} className="flex gap-3 px-4 py-2 opacity-60">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-live)]/15 text-[11px] font-semibold text-[var(--color-live)]">
        {(data.name || '?').slice(0, 1).toUpperCase()}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-[var(--color-text-dim)]">{data.name}</span>
          <span className="text-[10px] text-[var(--color-text-dim)] italic">listening…</span>
        </div>
        <p className="mt-0.5 text-sm text-[var(--color-text-dim)] italic">{data.text}</p>
      </div>
    </div>
  )
}

export default function MessageList({ turns, interimBySpeaker }) {
  const bottomRef = useRef(null)
  const interimEntries = Object.entries(interimBySpeaker || {})

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length, interimEntries.length])

  const isEmpty = turns.length === 0 && interimEntries.length === 0

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[var(--color-bg)]">
      <div className="border-b border-[var(--color-border)] px-4 py-3">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">Conversation</h2>
        <p className="text-xs text-[var(--color-text-dim)]">Live transcript · voice and text, shared by everyone</p>
      </div>
      <div className="flex-1 overflow-y-auto py-2">
        {isEmpty ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center">
            <p className="text-sm text-[var(--color-text-dim)]">
              No one has said anything yet. Try: &ldquo;AI kya hota hai?&rdquo;
            </p>
          </div>
        ) : (
          <>
            {turns.map((turn) => (
              <TurnRow key={turn.id} turn={turn} />
            ))}
            {interimEntries.map(([identity, data]) => (
              <InterimRow key={identity} identity={identity} data={data} />
            ))}
          </>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
