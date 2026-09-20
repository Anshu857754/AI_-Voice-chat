// ParticipantCard - one row in the participant list.
//
// Humans show name + live mic status. Bots show their persona icon, voice
// label and current pipeline state (listening/thinking/speaking/...). Nothing
// here is synthesized: a bot only ever renders if it is a genuine connected
// LiveKit participant (see hooks/useParticipants.js).

const BOT_META = {
  dost: { label: 'Roxstar AI Dost', icon: '\u{1F916}', accent: 'dost', voice: 'Male · Hindi/Hinglish' },
  sathi: { label: 'Roxstar AI Sathi', icon: '\u{1F916}', accent: 'sathi', voice: 'Female · Hindi/Hinglish' },
}

const STATE_META = {
  listening: { label: 'Listening', dot: 'bg-[var(--color-live)]' },
  thinking: { label: 'Thinking…', dot: 'bg-[var(--color-warn)] animate-pulse' },
  speaking: { label: 'Speaking', dot: 'bg-[var(--color-accent)] animate-pulse' },
  interrupted: { label: 'Interrupted', dot: 'bg-[var(--color-danger)]' },
  cancelled: { label: 'Cancelled', dot: 'bg-[var(--color-text-dim)]' },
  error: { label: 'Issue – retrying', dot: 'bg-[var(--color-danger)]' },
  idle: { label: 'Idle', dot: 'bg-[var(--color-text-dim)]' },
  offline: { label: 'Offline', dot: 'bg-[var(--color-text-dim)]' },
}

function MicIcon({ enabled }) {
  return enabled ? (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0M12 19v3" strokeLinecap="round" />
    </svg>
  ) : (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 3l18 18M9 9v3a3 3 0 0 0 4.6 2.55M15 9.34V5a3 3 0 0 0-5.94-.6" strokeLinecap="round" />
      <path d="M5 10a7 7 0 0 0 10.3 6.16M19 10a7 7 0 0 1-1.02 3.66M12 19v3" strokeLinecap="round" />
    </svg>
  )
}

export default function ParticipantCard({ participant, isSpeaking }) {
  if (participant.isBot) {
    const meta = BOT_META[participant.botId] || { label: participant.name, icon: '\u{1F916}', accent: 'accent', voice: participant.voice }
    const stateMeta = STATE_META[participant.botState] || STATE_META.offline
    const accentVar = `var(--color-${meta.accent})`
    const accentSoftVar = `var(--color-${meta.accent}-soft)`
    return (
      <div
        className="relative flex items-center gap-3 rounded-xl border p-3 transition-colors"
        style={{
          borderColor: isSpeaking ? accentVar : 'var(--color-border)',
          background: isSpeaking ? accentSoftVar : 'var(--color-panel-raised)',
        }}
      >
        <div
          className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-lg ${isSpeaking ? 'speaking-ring' : ''}`}
          style={{ background: accentSoftVar, color: accentVar }}
        >
          {meta.icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-sm font-medium text-[var(--color-text)]">{meta.label}</p>
          </div>
          <p className="truncate text-xs text-[var(--color-text-dim)]">{meta.voice}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 rounded-full bg-black/20 px-2 py-1">
          <span className={`h-1.5 w-1.5 rounded-full ${stateMeta.dot}`} />
          <span className="text-[11px] text-[var(--color-text-dim)]">{stateMeta.label}</span>
        </div>
      </div>
    )
  }

  return (
    <div
      className="flex items-center gap-3 rounded-xl border p-3 transition-colors"
      style={{
        borderColor: isSpeaking ? 'var(--color-live)' : 'var(--color-border)',
        background: isSpeaking ? 'color-mix(in srgb, var(--color-live) 12%, var(--color-panel-raised))' : 'var(--color-panel-raised)',
      }}
    >
      <div
        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent-soft)] text-sm font-semibold text-[var(--color-accent)] ${isSpeaking ? 'speaking-ring' : ''}`}
        style={{ color: 'var(--color-live)' }}
      >
        {(participant.name || '?').slice(0, 1).toUpperCase()}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-[var(--color-text)]">
          {participant.name}
          {participant.isLocal && <span className="ml-1.5 text-xs text-[var(--color-text-dim)]">(You)</span>}
        </p>
        <p className="text-xs text-[var(--color-text-dim)]">Human</p>
      </div>
      <div
        className={`flex shrink-0 items-center gap-1 rounded-full px-2 py-1 ${
          participant.micEnabled ? 'text-[var(--color-live)]' : 'text-[var(--color-text-dim)]'
        }`}
        style={{ background: 'rgba(0,0,0,0.2)' }}
        title={participant.micEnabled ? 'Mic on' : 'Mic off'}
      >
        <MicIcon enabled={participant.micEnabled} />
      </div>
    </div>
  )
}
