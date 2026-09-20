// ContextPanel - the quiet right-hand panel: what the room is about (Context)
// and the running record of the conversation (Transcript).

import { useEffect, useRef, useState } from 'react'
import { botColor, botLabel, formatClock } from '../lib/botMeta'

const TABS = [
  ['context', 'Context'],
  ['transcript', 'Transcript'],
  ['people', 'People'],
]

function Field({ title, children }) {
  return (
    <div className="mb-6">
      <h3 className="mb-1.5 text-[11px] font-medium tracking-wider text-[var(--color-text-faint)] uppercase">{title}</h3>
      {children}
    </div>
  )
}

// The backend summary may contain markdown emphasis; show it as plain text.
const plain = (s) => (s || '').replace(/\*\*/g, '').trim()

function ContextTab({ topic, summary, collabOn, people }) {
  const [expanded, setExpanded] = useState(false)
  const text = plain(summary)
  return (
    <div className="p-4">
      <Field title="What we're discussing">
        {topic ? (
          <p className="text-[15px] leading-relaxed text-[var(--color-text)]">{plain(topic)}</p>
        ) : (
          <p className="text-sm text-[var(--color-text-dim)]">Abhi koi topic nahi. Baat shuru karo, yahan dikhega.</p>
        )}
      </Field>
      {text && (
        <Field title="Summary so far">
          <p className={`text-sm leading-relaxed text-[var(--color-text-dim)] ${expanded ? '' : 'line-clamp-5'}`}>{text}</p>
          {text.length > 220 && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="mt-1.5 text-xs text-[var(--color-accent)] hover:underline"
            >
              {expanded ? 'Kam dikhao' : 'Poora dikhao'}
            </button>
          )}
        </Field>
      )}
      <Field title="Room">
        <p className="text-sm text-[var(--color-text-dim)]">{people}</p>
        <p className="mt-1 text-sm text-[var(--color-text-dim)]">
          AI Collaboration: <span className={collabOn ? 'text-[var(--color-text)]' : ''}>{collabOn ? 'Active' : 'Paused'}</span>
        </p>
      </Field>
    </div>
  )
}

function TranscriptTab({ turns, localIdentity }) {
  const endRef = useRef(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [turns.length])

  if (turns.length === 0) return <p className="p-4 text-sm text-[var(--color-text-dim)]">Abhi kuch nahi bola gaya.</p>
  const isMe = (t) => t.role !== 'bot' && (t.identity === localIdentity || t.identity?.split('-')[0] === localIdentity?.split('-')[0])
  return (
    <ol className="space-y-4 p-4">
      {turns.map((t) => (
        <li key={t.id}>
          <p className="flex items-baseline gap-2 text-xs">
            <span className="font-medium" style={{ color: t.role === 'bot' ? botColor(t.bot) : 'var(--color-live)' }}>
              {t.role === 'bot' ? botLabel(t.bot, t.name) : isMe(t) ? 'You' : t.name}
            </span>
            <span className="text-[var(--color-text-faint)]">{formatClock(t.createdAt)}</span>
          </p>
          <p className="mt-0.5 text-sm leading-relaxed whitespace-pre-wrap text-[var(--color-text-dim)]">{t.text}</p>
        </li>
      ))}
      <div ref={endRef} />
    </ol>
  )
}

export default function ContextPanel({ turns, localIdentity, topic, summary, collabOn, people, peoplePanel }) {
  const [tab, setTab] = useState('context')
  return (
    <aside className="flex h-full min-h-0 w-full flex-col bg-[var(--color-panel)]" aria-label="Room context">
      <div role="tablist" aria-label="Room context views" className="flex gap-1 px-3 pt-3">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            onClick={() => setTab(id)}
            className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
              tab === id ? 'bg-[var(--color-panel-raised)] text-[var(--color-text)]' : 'text-[var(--color-text-dim)] hover:text-[var(--color-text)]'
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <div key={tab} role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'context' && <ContextTab topic={topic} summary={summary} collabOn={collabOn} people={people} />}
        {tab === 'transcript' && <TranscriptTab turns={turns} localIdentity={localIdentity} />}
        {tab === 'people' && peoplePanel}
      </div>
    </aside>
  )
}
