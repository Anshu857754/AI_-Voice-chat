// BotStatus - compact strip above the transcript showing both bots' live
// pipeline state at a glance (listening / thinking / speaking / interrupted).

const STATE_META = {
  listening: { label: 'Listening', color: 'var(--color-live)' },
  thinking: { label: 'Thinking…', color: 'var(--color-warn)' },
  speaking: { label: 'Speaking', color: 'var(--color-accent)' },
  interrupted: { label: 'Interrupted', color: 'var(--color-danger)' },
  cancelled: { label: 'Cancelled', color: 'var(--color-text-dim)' },
  error: { label: 'Retrying…', color: 'var(--color-danger)' },
  idle: { label: 'Idle', color: 'var(--color-text-dim)' },
  offline: { label: 'Offline', color: 'var(--color-text-dim)' },
}

const BOT_LABEL = { dost: 'AI Dost', sathi: 'AI Sathi' }
const BOT_COLOR = { dost: 'var(--color-dost)', sathi: 'var(--color-sathi)' }

export default function BotStatus({ bots }) {
  if (bots.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-[var(--color-border)] bg-[var(--color-panel)] px-4 py-2">
      {bots.map((bot) => {
        const stateMeta = STATE_META[bot.botState] || STATE_META.offline
        return (
          <div key={bot.identity} className="flex items-center gap-1.5 text-xs">
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ background: stateMeta.color, boxShadow: `0 0 0 2px ${stateMeta.color}22` }}
            />
            <span className="font-medium" style={{ color: BOT_COLOR[bot.botId] }}>
              {BOT_LABEL[bot.botId] || bot.name}
            </span>
            <span className="text-[var(--color-text-dim)]">{stateMeta.label}</span>
          </div>
        )
      })}
    </div>
  )
}
