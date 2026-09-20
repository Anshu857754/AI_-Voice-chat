// Screens - the small non-chat screens: top bar, welcome / empty state,
// status cards (connecting, not found, errors) and the Archived list.

import { MenuIcon, PlusIcon, RestoreIcon, SparkIcon, TrashIcon } from './Icons'
import { ParticipantBadge } from './ConversationItem'
import { shortTime } from '../lib/dates'
import { chatPath } from '../hooks/useRoute'

export function TopBar({ title, showNavButton, onOpenNav, children }) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 px-3 sm:px-4">
      {showNavButton && (
        <button type="button" onClick={onOpenNav} aria-label="Open menu" className="flex h-9 w-9 items-center justify-center rounded-lg text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]">
          <MenuIcon size={19} />
        </button>
      )}
      <h1 className="min-w-0 flex-1 truncate text-base font-semibold text-[var(--color-text)]">{title}</h1>
      {children}
    </header>
  )
}

export function CenterCard({ icon, title, body, action, secondary, busy }) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center" role={busy ? 'status' : undefined}>
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
        {icon || <SparkIcon size={22} />}
      </div>
      <h2 className="mb-1.5 text-xl font-semibold text-[var(--color-text)]">{title}</h2>
      {body && <p className="mb-5 max-w-sm text-sm leading-relaxed text-[var(--color-text-dim)]">{body}</p>}
      <div className="flex gap-2">
        {action}
        {secondary}
      </div>
    </div>
  )
}

export const primaryAction =
  'flex h-10 items-center gap-2 rounded-xl bg-[var(--color-accent)] px-4 text-sm font-medium text-white transition hover:brightness-110'
export const quietAction =
  'flex h-10 items-center gap-2 rounded-xl bg-[var(--color-panel-raised)] px-4 text-sm text-[var(--color-text)] transition-colors hover:bg-white/10'

export function Welcome({ onNew }) {
  return (
    <CenterCard
      title="Welcome to Roxstar AI"
      body="Start a conversation with AI Dost and AI Sathi."
      action={
        <button type="button" onClick={() => onNew('text')} className={primaryAction}>
          <PlusIcon size={17} />
          New Chat
        </button>
      }
    />
  )
}

export function ArchivedView({ archived, onOpen, onRestore, onDelete, showNavButton, onOpenNav }) {
  return (
    <div className="flex h-full flex-col bg-[var(--color-surface)]">
      <TopBar title="Archived" showNavButton={showNavButton} onOpenNav={onOpenNav} />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-8 sm:px-6">
        <div className="mx-auto max-w-2xl">
          <p className="mb-4 text-sm text-[var(--color-text-dim)]">Archived chats Recent list mein nahi dikhte, par delete nahi hote. Restore karke wapas la sakte ho.</p>
          {archived.status === 'loading' ? (
            <ul className="space-y-2" aria-hidden="true">
              {[1, 2, 3].map((i) => (
                <li key={i} className="h-16 animate-pulse rounded-xl bg-white/5" />
              ))}
            </ul>
          ) : archived.status === 'error' ? (
            <div role="alert" className="rounded-xl bg-[var(--color-panel)] p-4 text-sm text-[var(--color-text-dim)]">
              Couldn&apos;t load archived conversations.{' '}
              <button type="button" onClick={archived.reload} className="ml-1 text-[var(--color-accent)] underline">
                Retry
              </button>
            </div>
          ) : archived.items.length === 0 ? (
            <p className="rounded-xl bg-[var(--color-panel)] p-4 text-sm text-[var(--color-text-dim)]" role="status">
              Koi archived conversation nahi hai.
            </p>
          ) : (
            <ul className="space-y-2">
              {archived.items.map((c) => (
                <li key={c.id} className="fade-in flex items-center gap-3 rounded-xl bg-[var(--color-panel)] p-3">
                  <ParticipantBadge conversation={c} size={34} />
                  <a
                    href={chatPath(c.id)}
                    onClick={(e) => {
                      if (e.metaKey || e.ctrlKey || e.button !== 0) return
                      e.preventDefault()
                      onOpen(c.id)
                    }}
                    className="min-w-0 flex-1"
                  >
                    <span className="block truncate text-sm text-[var(--color-text)]">{c.title}</span>
                    <span className="block truncate text-xs text-[var(--color-text-dim)]">
                      {shortTime(c.updated_at)} · {c.last_preview || 'Khaali'}
                    </span>
                  </a>
                  <button type="button" onClick={() => onRestore(c)} className={`${quietAction} h-9`}>
                    <RestoreIcon size={15} />
                    Restore
                  </button>
                  <button type="button" onClick={() => onDelete(c)} aria-label={`Delete ${c.title}`} title="Delete" className="flex h-9 w-9 items-center justify-center rounded-lg text-[var(--color-text-dim)] hover:bg-[var(--color-danger)]/15 hover:text-[var(--color-danger)]">
                    <TrashIcon size={16} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
