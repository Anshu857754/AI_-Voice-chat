// Shared identity + status metadata, so colours and wording are defined once.

export const BOTS = {
  dost: { label: 'AI Dost', short: 'Dost', tagline: 'Friendly · Hindi', color: 'var(--color-dost)', soft: 'var(--color-dost-soft)' },
  sathi: { label: 'AI Sathi', short: 'Sathi', tagline: 'Thoughtful · Hindi', color: 'var(--color-sathi)', soft: 'var(--color-sathi-soft)' },
}

export const botLabel = (id, fallback = 'AI') => BOTS[id]?.label || fallback
export const botColor = (id) => BOTS[id]?.color || 'var(--color-system)'

// `kind` drives the indicator shape, so state never relies on colour alone.
const STATUS = {
  idle: { label: 'Ready', kind: 'ring', color: 'var(--color-text-faint)' },
  waiting: { label: 'Waiting', kind: 'ring', color: 'var(--color-text-faint)' },
  offline: { label: 'Offline', kind: 'ring', color: 'var(--color-text-faint)' },
  listening: { label: 'Listening', kind: 'dot', color: 'var(--color-live)' },
  thinking: { label: 'Thinking', kind: 'dots', color: 'var(--color-warn)' },
  generating: { label: 'Writing', kind: 'dots', color: 'var(--color-warn)' },
  synthesizing: { label: 'Preparing voice…', kind: 'dots', color: 'var(--color-accent)' },
  speaking: { label: 'Speaking', kind: 'wave', color: null },
  interrupted: { label: 'Interrupted', kind: 'ring', color: 'var(--color-text-dim)' },
  cancelled: { label: 'Ready', kind: 'ring', color: 'var(--color-text-faint)' },
  error: { label: 'Voice unavailable', kind: 'warn', color: 'var(--color-warn)' },
}

/**
 * Status of one AI participant. `voiceError` is the last TTS failure (only ever
 * passed while in voice mode). While the user is speaking in voice mode, idle AIs read "Waiting".
 */
export function botStatus(bot, voiceError, { userSpeaking = false } = {}) {
  const lastError = voiceError
  const state = bot.botState || 'offline'
  const base = STATUS[state] || STATUS.offline
  if (userSpeaking && (state === 'idle' || state === 'cancelled' || state === 'listening')) {
    return { key: 'waiting', ...STATUS.waiting }
  }
  const ttsBroken = lastError?.kind === 'tts' && lastError.bot === bot.botId
  if (ttsBroken && (state === 'idle' || state === 'listening' || state === 'cancelled')) {
    return { key: 'error', ...STATUS.error }
  }
  return { key: state, ...base, color: base.color ?? botColor(bot.botId) }
}

/** States in which an AI is working on (or delivering) a reply. */
export const BUSY_STATES = ['thinking', 'generating', 'synthesizing', 'speaking']

export function formatClock(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
