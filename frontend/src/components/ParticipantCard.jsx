// ParticipantCard - one compact row in the participant list.
//
// AI rows are buttons: choosing one starts a message addressed to that AI
// ("Dost, ..."), which the backend router honours. A bot only renders if it is
// a genuine connected LiveKit participant (see hooks/useParticipants.js).

import Avatar from './Avatar'
import StatusIndicator from './StatusIndicator'
import Waveform from './Waveform'
import { MicIcon, MicOffIcon } from './Icons'
import { BOTS, botStatus } from '../lib/botMeta'

export function BotRow({ bot, lastError, userSpeaking, onSelect }) {
  const meta = BOTS[bot.botId]
  const status = botStatus(bot, lastError, { userSpeaking })
  const active = status.key === 'speaking'
  return (
    <button
      type="button"
      onClick={() => onSelect?.(bot)}
      aria-label={`${meta?.label || bot.name}, ${status.label}. Isse baat karo`}
      className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors ${
        active ? 'bg-[var(--color-panel-raised)]' : 'hover:bg-[var(--color-hover)]'
      }`}
    >
      <Avatar bot={bot.botId} size={36} ring={active} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-[var(--color-text)]">{meta?.label || bot.name}</span>
        <span className="block truncate text-xs text-[var(--color-text-faint)]">{meta?.tagline}</span>
        <StatusIndicator status={status} className="mt-0.5" />
      </span>
    </button>
  )
}

export function HumanRow({ person, speaking }) {
  return (
    <div className="flex items-center gap-3 rounded-xl px-3 py-2.5">
      <Avatar name={person.name} size={36} ring={speaking} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-[var(--color-text)]">{person.name}</span>
        <span className="flex items-center gap-1.5 text-xs text-[var(--color-text-dim)]">
          {person.isLocal ? 'You' : 'Human'}
          <span aria-hidden="true">·</span>
          <span className="inline-flex items-center gap-1.5">
            {speaking ? <Waveform active color="var(--color-live)" bars={3} height={10} /> : <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-live)]" aria-hidden="true" />}
            {speaking ? 'Speaking' : 'Connected'}
          </span>
        </span>
      </span>
      <span
        className={person.micEnabled ? 'text-[var(--color-live)]' : 'text-[var(--color-text-faint)]'}
        title={person.micEnabled ? 'Mic on' : 'Mic muted'}
        role="img"
        aria-label={person.micEnabled ? 'Mic on' : 'Mic muted'}
      >
        {person.micEnabled ? <MicIcon size={15} /> : <MicOffIcon size={15} />}
      </span>
    </div>
  )
}
