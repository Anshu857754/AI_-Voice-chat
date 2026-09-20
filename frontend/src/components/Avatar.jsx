// Avatar - AI participants get a tinted robot mark, humans their initial.
// `ring` shows one soft ring while that participant is speaking.

import { BOTS } from '../lib/botMeta'

function RobotMark({ size }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4" y="8" width="16" height="11" rx="4" />
      <path d="M12 8V5M9 13v1M15 13v1" />
    </svg>
  )
}

export default function Avatar({ bot, name = '?', size = 36, ring = false }) {
  const color = bot ? BOTS[bot]?.color || 'var(--color-system)' : 'var(--color-live)'
  const soft = bot ? BOTS[bot]?.soft || 'var(--color-panel-raised)' : 'var(--color-live-soft)'
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-full font-medium ${ring ? 'speaking-ring' : ''}`}
      style={{ width: size, height: size, background: soft, color, '--ring': color, fontSize: size * 0.4 }}
      aria-hidden="true"
    >
      {bot ? <RobotMark size={Math.round(size * 0.56)} /> : (name || '?').slice(0, 1).toUpperCase()}
    </span>
  )
}
