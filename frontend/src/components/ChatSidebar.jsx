// ChatSidebar - the app's main navigation: New Chat, search, pinned + recent
// conversations (grouped by real dates), Archived, Settings and Account.
//
// Desktop: full (280px) or collapsed to an icon rail. Mobile: rendered inside a
// drawer (variant="drawer"). It only ever holds conversation summaries; full
// messages are loaded when a chat is opened.

import { useEffect, useRef, useState } from 'react'
import Menu from './Menu'
import Avatar from './Avatar'
import ConversationItem, { ParticipantBadge } from './ConversationItem'
import { ArchiveIcon, ChevronIcon, GearIcon, LogoutIcon, PlusIcon, SearchIcon, SidebarIcon, SparkIcon } from './Icons'
import { useConversationSearch } from '../hooks/useConversations'
import { groupConversations } from '../lib/dates'

const NEW_KINDS = [
  { kind: 'text', label: 'Text Chat', hint: 'Type karo, jawab text mein' },
  { kind: 'voice', label: 'Voice Room', hint: 'Voice tabhi jab tum Voice dabao' },
  { kind: 'collab', label: 'AI Collaboration', hint: 'Dost aur Sathi aapas mein bhi baat karein' },
]

function Skeleton() {
  return (
    <ul className="space-y-1 px-1 py-2" aria-hidden="true">
      {[70, 55, 80, 60, 75].map((w, i) => (
        <li key={i} className="flex items-center gap-2.5 px-2 py-2">
          <span className="h-[30px] w-[30px] shrink-0 animate-pulse rounded-full bg-white/8" />
          <span className="flex-1 space-y-1.5">
            <span className="block h-3 animate-pulse rounded bg-white/8" style={{ width: `${w}%` }} />
            <span className="block h-2.5 w-2/5 animate-pulse rounded bg-white/5" />
          </span>
        </li>
      ))}
    </ul>
  )
}

function Section({ label, children }) {
  return (
    <section className="mt-3 first:mt-0">
      <h3 className="px-3 pb-1 text-[11px] font-medium tracking-wider text-[var(--color-text-faint)] uppercase">{label}</h3>
      <ul className="space-y-0.5 px-1">{children}</ul>
    </section>
  )
}

const shownEmail = (user) => (user?.email?.endsWith('@guest.local') ? '' : user?.email || '')

const railBtn =
  'flex h-10 w-10 items-center justify-center rounded-xl text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'

export default function ChatSidebar({
  api,
  list,
  activeId,
  collapsed = false,
  variant = 'desktop',
  user,
  onToggleCollapse,
  onNew,
  onOpen,
  onRename,
  onPin,
  onArchive,
  onDelete,
  onOpenArchived,
  onOpenSettings,
  onLogout,
  searchRef,
}) {
  const [query, setQuery] = useState('')
  const search = useConversationSearch(api, query)
  const sentinelRef = useRef(null)
  const searching = query.trim().length > 0
  const { pinned, groups } = groupConversations(list.items)

  // Infinite scroll: load the next page when the end of the list scrolls into view.
  useEffect(() => {
    const el = sentinelRef.current
    if (!el || !list.hasMore || searching) return undefined
    const io = new IntersectionObserver((entries) => entries[0].isIntersecting && list.loadMore(), { rootMargin: '120px' })
    io.observe(el)
    return () => io.disconnect()
  }, [list.hasMore, list.loadMore, searching, list.items.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const open = (id) => {
    setQuery('')
    onOpen(id)
  }
  const item = (c, extra = {}) => (
    <ConversationItem
      key={c.id}
      conversation={c}
      active={c.id === activeId}
      onOpen={open}
      onRename={onRename}
      onPin={onPin}
      onArchive={onArchive}
      onDelete={onDelete}
      {...extra}
    />
  )

  const accountMenu = (
    <Menu
      label="Account"
      align="left"
      width={220}
      icon={
        collapsed ? (
          <Avatar name={user?.name} size={28} />
        ) : (
          <>
            <Avatar name={user?.name} size={30} />
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium text-[var(--color-text)]">{user?.name}</span>
              <span className="block truncate text-[11px] text-[var(--color-text-faint)]">{shownEmail(user) || 'Guest (testing)'}</span>
            </span>
          </>
        )
      }
      triggerClassName={collapsed ? railBtn : 'flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-[var(--color-hover)]'}
      items={[
        { key: 'who', label: user?.name || 'Account', hint: shownEmail(user), disabled: true, onSelect: () => {} },
        { key: 'sep', separator: true },
        { key: 'settings', label: 'Settings', icon: <GearIcon size={16} />, onSelect: onOpenSettings },
        ...(onLogout ? [{ key: 'logout', label: 'Log out', icon: <LogoutIcon size={16} />, onSelect: onLogout }] : []),
      ]}
    />
  )

  // ---- collapsed rail (desktop only) ----
  if (collapsed && variant === 'desktop') {
    return (
      <nav aria-label="Conversations" className="flex h-full w-[60px] flex-col items-center gap-1 bg-[var(--color-panel)] py-3">
        <button type="button" onClick={onToggleCollapse} aria-label="Expand sidebar" title="Expand sidebar" className={railBtn}>
          <SparkIcon size={20} className="text-[var(--color-accent)]" />
        </button>
        <button type="button" onClick={() => onNew('text')} aria-label="New chat" title="New chat" className={`${railBtn} bg-[var(--color-accent)]/15 text-[var(--color-accent)]`}>
          <PlusIcon size={19} />
        </button>
        <button
          type="button"
          onClick={() => {
            onToggleCollapse()
            setTimeout(() => searchRef?.current?.focus(), 60)
          }}
          aria-label="Search conversations"
          title="Search"
          className={railBtn}
        >
          <SearchIcon size={18} />
        </button>
        <ul className="mt-2 flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto">
          {list.items.slice(0, 8).map((c) => (
            <li key={c.id}>
              <a
                href={`/chat/${c.id}`}
                title={c.title}
                aria-label={c.title}
                aria-current={c.id === activeId ? 'page' : undefined}
                onClick={(e) => {
                  e.preventDefault()
                  onOpen(c.id)
                }}
                className={`flex h-10 w-10 items-center justify-center rounded-xl transition-colors ${c.id === activeId ? 'bg-[var(--color-panel-raised)]' : 'hover:bg-[var(--color-hover)]'}`}
              >
                <ParticipantBadge conversation={c} size={26} />
              </a>
            </li>
          ))}
        </ul>
        <button type="button" onClick={onOpenArchived} aria-label="Archived" title="Archived" className={railBtn}>
          <ArchiveIcon size={18} />
        </button>
        <button type="button" onClick={onOpenSettings} aria-label="Settings" title="Settings" className={railBtn}>
          <GearIcon size={18} />
        </button>
        {accountMenu}
      </nav>
    )
  }

  // ---- full sidebar / drawer content ----
  return (
    <nav aria-label="Conversations" className="flex h-full w-full flex-col bg-[var(--color-panel)]">
      {variant === 'desktop' && (
        <div className="flex items-center justify-between px-3 pt-3 pb-1">
          <div className="flex items-center gap-2 px-1 text-[var(--color-text)]">
            <SparkIcon size={19} className="text-[var(--color-accent)]" />
            <span className="text-[15px] font-semibold">Roxstar AI</span>
          </div>
          <button type="button" onClick={onToggleCollapse} aria-label="Collapse sidebar" title="Collapse sidebar" className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]">
            <SidebarIcon size={17} />
          </button>
        </div>
      )}
      <div className="flex gap-1.5 px-3 pt-2">
        <button
          type="button"
          onClick={() => onNew('text')}
          className="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-xl bg-[var(--color-accent)] px-3 text-sm font-medium text-white transition hover:brightness-110"
        >
          <PlusIcon size={17} />
          New Chat
        </button>
        <Menu
          label="Choose chat type"
          width={270}
          align="left"
          icon={<ChevronIcon size={16} />}
          triggerClassName="flex h-10 w-10 items-center justify-center rounded-xl bg-[var(--color-panel-raised)] text-[var(--color-text)] transition-colors hover:bg-white/10"
          items={NEW_KINDS.map((k) => ({ key: k.kind, label: k.label, hint: k.hint, onSelect: () => onNew(k.kind) }))}
        />
      </div>

      <div className="px-3 pt-3">
        <label className="flex items-center gap-2 rounded-lg bg-[var(--color-input)] px-3 py-2 text-[var(--color-text-faint)] focus-within:ring-1 focus-within:ring-[var(--color-accent)]/60">
          <SearchIcon size={15} />
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setQuery('')
              if (e.key === 'Enter' && search.results[0]) open(search.results[0].id)
            }}
            placeholder="Search conversations…"
            aria-label="Search conversations"
            className="min-w-0 flex-1 bg-transparent text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-faint)]"
          />
        </label>
      </div>

      <div className="mt-3 min-h-0 flex-1 overflow-y-auto pb-2">
        {searching ? (
          search.status === 'loading' ? (
            <Skeleton />
          ) : search.status === 'error' ? (
            <p role="alert" className="px-4 py-3 text-sm text-[var(--color-text-dim)]">Search nahi ho paya. {search.error}</p>
          ) : search.results.length === 0 ? (
            <p className="px-4 py-3 text-sm text-[var(--color-text-dim)]" role="status">No conversations found.</p>
          ) : (
            <Section label={`${search.results.length} result${search.results.length === 1 ? '' : 's'}`}>
              {search.results.map((r) =>
                item(
                  { id: r.id, title: r.title, updated_at: r.updated_at, pinned: false, archived: r.archived, participants: [], mode: 'text', last_preview: '' },
                  { query: query.trim(), snippet: r.snippet },
                ),
              )}
            </Section>
          )
        ) : list.status === 'loading' ? (
          <Skeleton />
        ) : list.status === 'error' ? (
          <div className="px-4 py-3" role="alert">
            <p className="mb-2 text-sm text-[var(--color-text-dim)]">Couldn&apos;t load conversations.</p>
            <button type="button" onClick={() => list.refresh()} className="rounded-lg bg-[var(--color-panel-raised)] px-3 py-1.5 text-sm text-[var(--color-text)] hover:bg-white/10">
              Retry
            </button>
          </div>
        ) : list.items.length === 0 ? (
          <p className="px-4 py-3 text-sm text-[var(--color-text-dim)]">No conversations yet.</p>
        ) : (
          <>
            {pinned.length > 0 && <Section label="Pinned">{pinned.map((c) => item(c))}</Section>}
            {groups.map((g) => (
              <Section key={g.key} label={g.label}>
                {g.items.map((c) => item(c))}
              </Section>
            ))}
            {list.hasMore && (
              <div ref={sentinelRef} className="px-3 py-2">
                <button type="button" onClick={list.loadMore} disabled={list.loadingMore} className="w-full rounded-lg py-2 text-xs text-[var(--color-text-dim)] hover:bg-[var(--color-hover)]">
                  {list.loadingMore ? 'Loading…' : 'Show more'}
                </button>
              </div>
            )}
          </>
        )}
      </div>

      <div className="space-y-0.5 px-2 pb-1">
        <button type="button" onClick={onOpenArchived} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]">
          <ArchiveIcon size={16} />
          Archived
        </button>
        <button type="button" onClick={onOpenSettings} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]">
          <GearIcon size={16} />
          Settings
        </button>
      </div>
      <div className="flex items-center gap-1 bg-black/15 px-2 py-2">
        {accountMenu}
      </div>
    </nav>
  )
}
