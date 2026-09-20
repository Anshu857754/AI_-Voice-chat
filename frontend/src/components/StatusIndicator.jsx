// StatusIndicator - glyph + word for an AI's state. The glyph *shape* differs
// per state (ring / dot / dots / waveform / warning), so it never depends on
// colour alone.

import Waveform from './Waveform'
import { WarnIcon } from './Icons'

function Glyph({ status }) {
  switch (status.kind) {
    case 'wave':
      return <Waveform active color={status.color} bars={4} height={12} />
    case 'dots':
      return (
        <span className="inline-flex items-center gap-[3px]" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <span key={i} className="typing-dot h-1 w-1 rounded-full" style={{ background: status.color, animationDelay: `${i * 0.18}s` }} />
          ))}
        </span>
      )
    case 'warn':
      return <WarnIcon size={12} style={{ color: status.color }} />
    case 'dot':
      return <span className="h-2 w-2 rounded-full" style={{ background: status.color }} aria-hidden="true" />
    default:
      return (
        <span className="h-2 w-2 rounded-full border-[1.5px]" style={{ borderColor: status.color }} aria-hidden="true" />
      )
  }
}

export default function StatusIndicator({ status, className = '' }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs text-[var(--color-text-dim)] ${className}`}>
      <Glyph status={status} />
      <span style={status.key === 'speaking' || status.key === 'error' ? { color: status.color } : undefined}>{status.label}</span>
    </span>
  )
}
