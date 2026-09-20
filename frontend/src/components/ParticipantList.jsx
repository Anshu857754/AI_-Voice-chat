// ParticipantList - LEFT panel: everyone in the room, humans then bots.

import ParticipantCard from './ParticipantCard'

export default function ParticipantList({ humans, bots, isSpeaking }) {
  return (
    <aside className="flex h-full w-full flex-col overflow-hidden border-r border-[var(--color-border)] bg-[var(--color-panel)]">
      <div className="border-b border-[var(--color-border)] px-4 py-3.5">
        <h2 className="text-sm font-semibold tracking-wide text-[var(--color-text)]">Participants</h2>
        <p className="text-xs text-[var(--color-text-dim)]">
          {humans.length} human{humans.length === 1 ? '' : 's'} · {bots.length} AI
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        <div className="space-y-2">
          {humans.length === 0 ? (
            <p className="px-1 py-2 text-xs text-[var(--color-text-dim)]">Waiting for participants…</p>
          ) : (
            humans.map((p) => (
              <ParticipantCard key={p.identity} participant={p} isSpeaking={isSpeaking(p.identity)} />
            ))
          )}
        </div>

        <div className="mt-4 mb-2 flex items-center gap-2 px-1">
          <div className="h-px flex-1 bg-[var(--color-border)]" />
          <span className="text-[10px] font-medium tracking-wider text-[var(--color-text-dim)] uppercase">
            AI Participants
          </span>
          <div className="h-px flex-1 bg-[var(--color-border)]" />
        </div>

        <div className="space-y-2">
          {bots.length === 0 ? (
            <p className="px-1 py-2 text-xs text-[var(--color-text-dim)]">
              AI Dost and AI Sathi have not joined yet.
            </p>
          ) : (
            bots.map((p) => (
              <ParticipantCard key={p.identity} participant={p} isSpeaking={isSpeaking(p.identity)} />
            ))
          )}
        </div>
      </div>
    </aside>
  )
}
