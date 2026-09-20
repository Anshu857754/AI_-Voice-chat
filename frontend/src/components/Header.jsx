// Header - the open conversation: its title, who is in it, AI Collaboration,
// Voice, and the ⋯ menu with the conversation's actions.
// (The app brand and navigation live in the sidebar.)

import Menu from './Menu'
import {
  ArchiveIcon,
  DownloadIcon,
  EditIcon,
  GearIcon,
  MenuIcon,
  MoreIcon,
  PanelRightIcon,
  PauseIcon,
  PinIcon,
  RestoreIcon,
  StopIcon,
  TrashIcon,
  WaveIcon,
} from './Icons'
import { botLabel } from '../lib/botMeta'

const ghostBtn =
  'flex h-9 w-9 items-center justify-center rounded-lg text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'

function CollabControl({ on, busy, disabled, onToggle, onPause }) {
  return (
    <div className="hidden items-center gap-1 md:flex">
      <button
        type="button"
        onClick={onToggle}
        role="switch"
        aria-checked={on}
        disabled={disabled}
        title={disabled ? 'AI Collaboration ke liye dono AI chahiye (chat settings)' : on ? 'AI Collaboration band karo' : 'AI Collaboration chalu karo (max 6 turns)'}
        className="flex flex-col items-start rounded-lg px-3 py-1 text-left transition-colors hover:bg-[var(--color-hover)] disabled:opacity-40"
      >
        <span className="text-[11px] leading-tight whitespace-nowrap text-[var(--color-text-faint)]">AI Collaboration</span>
        <span className="flex items-center gap-1.5 text-xs leading-tight font-medium text-[var(--color-text)]">
          <span
            className={`h-2 w-2 rounded-full ${on ? 'bg-[var(--color-live)]' : 'border-[1.5px] border-[var(--color-text-faint)]'}`}
            aria-hidden="true"
          />
          {on ? 'Active' : 'Paused'}
        </span>
      </button>
      {on && busy && (
        <button
          type="button"
          onClick={onPause}
          className="flex h-8 items-center gap-1.5 rounded-lg bg-[var(--color-panel-raised)] px-2.5 text-xs text-[var(--color-text)] transition-colors hover:bg-white/10"
        >
          <PauseIcon size={13} />
          Pause
        </button>
      )}
    </div>
  )
}

export default function Header({
  conversation,
  bots,
  humans,
  connectionState,
  aiModeOn,
  aiBusy,
  voiceMode,
  voiceAllowed,
  onToggleAiMode,
  onPauseAi,
  onToggleVoice,
  contextOpen,
  onToggleContext,
  showNavButton,
  onOpenNav,
  onRename,
  onPin,
  onArchive,
  onDelete,
  onExport,
  onChatSettings,
  onOpenDiagnostics,
}) {
  const online = connectionState === 'connected'
  const participants = conversation.participants.map((p) => botLabel(p)).join(' + ')
  const counts = `${bots.length} AI · ${humans.length} ${humans.length === 1 ? 'Human' : 'Humans'}`
  const bothAIs = conversation.participants.length > 1

  const menuItems = [
    { key: 'rename', label: 'Rename', icon: <EditIcon size={16} />, onSelect: onRename },
    { key: 'pin', label: conversation.pinned ? 'Unpin' : 'Pin', icon: <PinIcon size={16} />, disabled: conversation.archived, onSelect: onPin },
    conversation.archived
      ? { key: 'archive', label: 'Restore from archive', icon: <RestoreIcon size={16} />, onSelect: onArchive }
      : { key: 'archive', label: 'Archive', icon: <ArchiveIcon size={16} />, onSelect: onArchive },
    { key: 'sep1', separator: true },
    { key: 'settings', label: 'Chat settings', icon: <GearIcon size={16} />, onSelect: onChatSettings },
    { key: 'collab', label: 'AI Collaboration', hint: aiModeOn ? 'Active' : 'Paused', checked: aiModeOn, disabled: !bothAIs, onSelect: onToggleAiMode },
    { key: 'pause', label: 'Pause AI conversation', hint: 'Abhi ka jawab aur AI ↔ AI turns roko', disabled: !aiBusy, onSelect: onPauseAi },
    { key: 'sep2', separator: true },
    { key: 'txt', label: 'Export as TXT', icon: <DownloadIcon size={16} />, onSelect: () => onExport('txt') },
    { key: 'md', label: 'Export as Markdown', icon: <DownloadIcon size={16} />, onSelect: () => onExport('md') },
    { key: 'json', label: 'Export as JSON', icon: <DownloadIcon size={16} />, onSelect: () => onExport('json') },
    { key: 'sep3', separator: true },
    { key: 'ctx', label: contextOpen ? 'Room context chhupao' : 'Room context dikhao', onSelect: onToggleContext },
    { key: 'debug', label: 'Diagnostics', onSelect: onOpenDiagnostics },
    { key: 'sep4', separator: true },
    { key: 'delete', label: 'Delete', icon: <TrashIcon size={16} />, danger: true, onSelect: onDelete },
  ]

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 bg-[var(--color-bg)] px-3 sm:gap-3 sm:px-4">
      {showNavButton && (
        <button type="button" onClick={onOpenNav} aria-label="Open menu" title="Menu" className={ghostBtn}>
          <MenuIcon size={19} />
        </button>
      )}

      <div className="min-w-0 flex-1">
        <h1 className="flex items-center gap-2 text-base leading-tight font-semibold text-[var(--color-text)]">
          <span className="truncate">{conversation.title}</span>
          {conversation.archived && (
            <span className="shrink-0 rounded-full bg-white/8 px-2 py-0.5 text-[11px] font-medium text-[var(--color-text-dim)]">Archived</span>
          )}
          {!online && (
            <span className="shrink-0 rounded-full bg-[var(--color-warn)]/15 px-2 py-0.5 text-[11px] font-medium text-[var(--color-warn)]" role="status">
              {connectionState === 'reconnecting' || connectionState === 'signalReconnecting' ? 'Reconnecting…' : connectionState}
            </span>
          )}
        </h1>
        <p className="hidden truncate text-xs leading-tight text-[var(--color-text-dim)] sm:block">
          {participants} · {counts}
        </p>
      </div>

      <CollabControl on={aiModeOn} busy={aiBusy} disabled={!bothAIs} onToggle={onToggleAiMode} onPause={onPauseAi} />

      <button
        type="button"
        onClick={onToggleVoice}
        disabled={!online || (!voiceAllowed && !voiceMode)}
        aria-pressed={voiceMode}
        aria-label={voiceMode ? 'Stop voice mode' : 'Start voice mode'}
        title={!voiceAllowed && !voiceMode ? 'Is chat mein voice band hai (chat settings)' : voiceMode ? 'Stop voice mode' : 'Start voice mode'}
        className={`flex h-9 items-center gap-2 rounded-lg px-2.5 text-sm transition-colors disabled:opacity-40 sm:px-3 ${
          voiceMode
            ? 'bg-[var(--color-danger)]/15 text-[var(--color-danger)] hover:bg-[var(--color-danger)]/25'
            : 'bg-[var(--color-panel-raised)] text-[var(--color-text)] hover:bg-white/10'
        }`}
      >
        {voiceMode ? <StopIcon size={14} /> : <WaveIcon size={16} />}
        <span className="hidden sm:inline">{voiceMode ? 'Stop Voice' : 'Voice'}</span>
      </button>

      <button
        type="button"
        onClick={onToggleContext}
        aria-pressed={contextOpen}
        aria-label="Room context and people"
        title="Room context & people"
        className={`${ghostBtn} ${contextOpen ? 'bg-[var(--color-hover)] text-[var(--color-text)]' : ''}`}
      >
        <PanelRightIcon size={18} />
      </button>

      <Menu label="Conversation options" icon={<MoreIcon size={18} />} items={menuItems} width={260} />
    </header>
  )
}
