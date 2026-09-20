// JoinScreen - the entry screen where a human names themselves before
// joining the shared LiveKit room.

import { useState } from 'react'

export default function JoinScreen({ onJoin, joining, error, defaultRoom }) {
  const [name, setName] = useState('')

  const submit = (e) => {
    e.preventDefault()
    if (!name.trim()) return
    onJoin(name.trim(), defaultRoom || undefined)
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-bg)] px-4">
      <div className="w-full max-w-sm rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel)] p-8 shadow-2xl">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--color-accent-soft)] text-2xl">
            {'\u{1F3A4}'}
          </div>
          <h1 className="text-lg font-semibold text-[var(--color-text)]">Roxstar AI Voice Room</h1>
          <p className="mt-1 text-sm text-[var(--color-text-dim)]">
            Talk with people and with AI Dost &amp; AI Sathi, live.
          </p>
        </div>

        <form onSubmit={submit} className="space-y-3">
          <div>
            <label htmlFor="name" className="mb-1 block text-xs font-medium text-[var(--color-text-dim)]">
              Your name
            </label>
            <input
              id="name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Rahul"
              autoFocus
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-panel-raised)] px-3.5 py-2.5 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-dim)] focus:border-[var(--color-accent)]"
            />
          </div>

          {error && (
            <p className="rounded-lg bg-[var(--color-danger)]/10 px-3 py-2 text-xs text-[var(--color-danger)]">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={!name.trim() || joining}
            className="w-full rounded-lg bg-[var(--color-accent)] py-2.5 text-sm font-medium text-white transition-opacity disabled:opacity-40"
          >
            {joining ? 'Joining…' : 'Join room'}
          </button>
        </form>

        <p className="mt-4 text-center text-xs text-[var(--color-text-dim)]">
          Room: <span className="font-mono">{defaultRoom || 'roxstar-room'}</span> · Mic access will be requested after you join.
        </p>
      </div>
    </div>
  )
}
