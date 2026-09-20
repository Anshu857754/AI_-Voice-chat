// ParticipantList - left sidebar: searchable AI participants and people,
// with the signed-in account at the bottom.

import { useState } from 'react'
import { BotRow, HumanRow } from './ParticipantCard'
import { SearchIcon } from './Icons'

function Section({ title, count, children }) {
  return (
    <section className="mt-5 first:mt-2">
      <h3 className="mb-1 flex items-center justify-between px-3 text-[11px] font-medium tracking-wider text-[var(--color-text-faint)] uppercase">
        {title}
        <span className="tabular-nums">{count}</span>
      </h3>
      <div className="space-y-0.5">{children}</div>
    </section>
  )
}

export default function ParticipantList({ humans, bots, isSpeaking, lastError, userSpeaking, onSelectBot }) {
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const match = (p) => !q || (p.name || '').toLowerCase().includes(q)
  const shownBots = bots.filter(match)
  const shownHumans = humans.filter(match)

  return (
    <div className="flex h-full w-full flex-col bg-[var(--color-panel)]" aria-label="Participants">
      <div className="px-3 pt-3">
        <label className="flex items-center gap-2 rounded-lg bg-[var(--color-input)] px-3 py-2 text-[var(--color-text-faint)] focus-within:ring-1 focus-within:ring-[var(--color-accent)]/60">
          <SearchIcon size={15} />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search participants"
            aria-label="Search participants"
            className="min-w-0 flex-1 bg-transparent text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-faint)]"
          />
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        <Section title="AI participants" count={bots.length}>
          {bots.length === 0 ? (
            <p className="px-3 py-2 text-xs text-[var(--color-text-dim)]">AI Dost aur AI Sathi abhi join nahi hue.</p>
          ) : shownBots.length === 0 ? (
            <p className="px-3 py-2 text-xs text-[var(--color-text-dim)]">Koi match nahi.</p>
          ) : (
            shownBots.map((b) => <BotRow key={b.identity} bot={b} lastError={lastError} userSpeaking={userSpeaking} onSelect={onSelectBot} />)
          )}
        </Section>

        <Section title="People" count={humans.length}>
          {shownHumans.length === 0 ? (
            <p className="px-3 py-2 text-xs text-[var(--color-text-dim)]">{humans.length === 0 ? 'Waiting for participants…' : 'Koi match nahi.'}</p>
          ) : (
            shownHumans.map((p) => <HumanRow key={p.identity} person={p} speaking={isSpeaking(p.identity)} />)
          )}
        </Section>
      </div>

    </div>
  )
}
