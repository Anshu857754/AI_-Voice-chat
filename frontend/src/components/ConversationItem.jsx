// ConversationItem - one row in the sidebar.
//
// A real link (`<a href="/chat/:id">`, so middle-click / open-in-new-tab work)
// that navigates without a reload. The ⋯ menu is a sibling button (never nested
// in the link) that appears on hover / focus / selection, and is always visible
// on touch screens.

import { useState } from 'react'
import Menu from './Menu'
import { ArchiveIcon, ChatIcon, EditIcon, MicIcon, MoreIcon, PinIcon, RestoreIcon, TrashIcon } from './Icons'
import { BOTS } from '../lib/botMeta'
import { shortTime } from '../lib/dates'
import { chatPath } from '../hooks/useRoute'

export function ParticipantBadge({ conversation, size = 30 }) {
  const parts = conversation.participants?.length ? conversation.participants : ['dost', 'sathi']
  const colors = parts.map((p) => BOTS[p]?.color || 'var(--color-system)')
  const bg = colors.length > 1 ? `linear-gradient(135deg, ${colors[0]} 50%, ${colors[1]} 50%)` : colors[0]
  const Icon = conversation.mode === 'voice' ? MicIcon : ChatIcon
  return (
    <span className="relative flex shrink-0 items-center justify-center rounded-full text-[#0b1220]" style={{ width: size, height: size, background: bg, opacity: 0.9 }} aria-hidden="true">
      <Icon size={Math.round(size * 0.5)} />
    </span>
  )
}

function Highlight({ text, query }) {
  const i = query ? text.toLowerCase().indexOf(query.toLowerCase()) : -1
  if (i < 0) return text
  return (
    <>
      {text.slice(0, i)}
      <mark className="rounded bg-[var(--color-accent-soft)] text-[var(--color-text)]">{text.slice(i, i + query.length)}</mark>
      {text.slice(i + query.length)}
    </>
  )
}

export default function ConversationItem({ conversation: c, active, query = '', snippet, onOpen, onRename, onPin, onArchive, onDelete }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const items = [
    { key: 'rename', label: 'Rename', icon: <EditIcon size={16} />, onSelect: () => onRename(c) },
    { key: 'pin', label: c.pinned ? 'Unpin' : 'Pin', icon: <PinIcon size={16} />, onSelect: () => onPin(c), disabled: c.archived },
    c.archived
      ? { key: 'archive', label: 'Restore from archive', icon: <RestoreIcon size={16} />, onSelect: () => onArchive(c) }
      : { key: 'archive', label: 'Archive', icon: <ArchiveIcon size={16} />, onSelect: () => onArchive(c) },
    { key: 'sep', separator: true },
    { key: 'delete', label: 'Delete', icon: <TrashIcon size={16} />, danger: true, onSelect: () => onDelete(c) },
  ]

  return (
    <li className="group relative">
      <a
        href={chatPath(c.id)}
        aria-current={active ? 'page' : undefined}
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
          e.preventDefault()
          onOpen(c.id)
        }}
        className={`flex items-center gap-2.5 rounded-lg py-2 pr-9 pl-2 transition-colors ${
          active ? 'bg-[var(--color-panel-raised)]' : 'hover:bg-[var(--color-hover)]'
        }`}
      >
        <ParticipantBadge conversation={c} />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span className="flex min-w-0 items-center gap-1 text-sm text-[var(--color-text)]">
              {c.pinned && <PinIcon size={11} className="shrink-0 text-[var(--color-text-faint)]" />}
              <span className="truncate">
                <Highlight text={c.title} query={query} />
              </span>
            </span>
            <time className="shrink-0 text-[11px] text-[var(--color-text-faint)]" dateTime={new Date(c.updated_at * 1000).toISOString()}>
              {shortTime(c.updated_at)}
            </time>
          </span>
          <span className="block truncate text-xs text-[var(--color-text-dim)]">
            <Highlight text={snippet ?? (c.last_preview || 'Nayi conversation')} query={query} />
          </span>
        </span>
      </a>
      <div className={`absolute top-1/2 right-0.5 -translate-y-1/2 transition-opacity ${menuOpen ? 'opacity-100' : 'opacity-0'} group-focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100`}>
        <Menu
          label={`Options for ${c.title}`}
          icon={<MoreIcon size={16} />}
          items={items}
          width={208}
          onOpenChange={setMenuOpen}
          triggerClassName="flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-dim)] hover:bg-white/10 hover:text-[var(--color-text)]"
        />
      </div>
    </li>
  )
}
