// VoiceMode - full-screen voice assistant, in the style of ChatGPT voice.
//
// A big orb reflects what is happening right now: the user speaking, an AI
// thinking, or an AI speaking (tinted with that AI's colour). Everything is
// driven by real LiveKit state (active speakers + bot_state attributes); the
// live caption is the user's interim transcript or the AI's latest reply.

const BOT_COLOR = { dost: 'var(--color-dost)', sathi: 'var(--color-sathi)' }
const BOT_LABEL = { dost: 'AI Dost', sathi: 'AI Sathi' }

function describe({ bots, userSpeaking, micEnabled }) {
  const speaking = bots.find((b) => b.botState === 'speaking')
  if (speaking) {
    return { key: 'speaking', label: `${BOT_LABEL[speaking.botId] || speaking.name} is speaking`, color: BOT_COLOR[speaking.botId] }
  }
  const thinking = bots.find((b) => ['thinking', 'generating', 'synthesizing'].includes(b.botState))
  if (thinking) {
    return { key: 'thinking', label: `${BOT_LABEL[thinking.botId] || thinking.name} is thinking…`, color: BOT_COLOR[thinking.botId] }
  }
  if (!micEnabled) return { key: 'muted', label: 'Mic is off. Tap the mic to talk', color: 'var(--color-text-dim)' }
  if (userSpeaking) return { key: 'user', label: 'Listening…', color: 'var(--color-live)' }
  return { key: 'idle', label: 'Bolo, main sun raha hoon', color: 'var(--color-accent)' }
}

function MicIcon({ off }) {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0M12 19v3M8 22h8" strokeLinecap="round" />
      {off && <path d="M3 3l18 18" strokeLinecap="round" />}
    </svg>
  )
}

export default function VoiceMode({ bots, userSpeaking, micEnabled, micPaused, onToggleMic, onClose, caption }) {
  const state = describe({ bots, userSpeaking, micEnabled })
  const active = state.key === 'speaking' || state.key === 'user'

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-between bg-[var(--color-bg)] px-6 py-10">
      <div className="flex w-full max-w-md items-center justify-between text-xs text-[var(--color-text-dim)]">
        <span>Voice mode</span>
        <div className="flex gap-3">
          {bots.map((b) => (
            <span key={b.identity} style={{ color: BOT_COLOR[b.botId] }}>
              {BOT_LABEL[b.botId] || b.name}
            </span>
          ))}
        </div>
      </div>

      <div className="flex flex-col items-center gap-8">
        <div
          aria-hidden="true"
          className={`h-44 w-44 rounded-full transition-all duration-500 ${active ? 'orb-fast' : 'orb'}`}
          style={{
            background: `radial-gradient(circle at 35% 30%, rgba(255,255,255,0.55), ${state.color} 55%, color-mix(in srgb, ${state.color} 35%, transparent))`,
            boxShadow: `0 0 ${active ? 90 : 40}px color-mix(in srgb, ${state.color} 55%, transparent)`,
          }}
        />
        <p className="text-base font-medium text-[var(--color-text)]" role="status">
          {state.label}
        </p>
        {micPaused && <p className="-mt-6 text-xs text-[var(--color-warn)]">Mic paused while AI speaks</p>}
        <p className="min-h-[3.5rem] max-w-md text-center text-sm leading-relaxed text-[var(--color-text-dim)]">
          {caption}
        </p>
      </div>

      <div className="flex items-center gap-6">
        <button
          type="button"
          onClick={onToggleMic}
          aria-pressed={micEnabled}
          title={micEnabled ? 'Mute microphone' : 'Unmute microphone'}
          className={`flex h-16 w-16 items-center justify-center rounded-full transition-colors ${
            micEnabled ? 'bg-white text-black' : 'bg-[var(--color-panel-raised)] text-[var(--color-text)]'
          }`}
        >
          <MicIcon off={!micEnabled} />
        </button>
        <button
          type="button"
          onClick={onClose}
          title="End voice mode"
          className="flex h-16 w-16 items-center justify-center rounded-full bg-[var(--color-danger)] text-white"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
          </svg>
        </button>
      </div>
    </div>
  )
}
