// AppShell - the signed-in application: sidebar navigation + the current screen.
//
//   /                  most recent conversation (or the welcome screen if none)
//   /chat/:id          a conversation (refresh, back and forward all restore it)
//   /archived          archived conversations
//
// It owns the conversation list, every conversation action (new, rename, pin,
// archive, delete, clear, export, settings) and the dialogs. The LiveKit room is
// created once above (App) and stays connected while you move between chats.

import { useCallback, useEffect, useRef, useState } from 'react'
import ChatSidebar from './ChatSidebar'
import Drawer from './Drawer'
import Notice from './Notice'
import Room from './Room'
import { ConfirmDialog, RenameDialog } from './Dialogs'
import { ChatSettingsDialog, SettingsDialog } from './SettingsDialogs'
import { ArchivedView, CenterCard, TopBar, Welcome, primaryAction, quietAction } from './Screens'
import { PlusIcon } from './Icons'
import { useArchived, useConversations, useConversationsApi } from '../hooks/useConversations'
import { chatPath, useRoute } from '../hooks/useRoute'
import { usePrefs } from '../hooks/usePrefs'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { downloadBlob } from '../services/conversations'
import { sendControl } from '../services/livekit'

/** Metadata of the open conversation: from the list if present, else fetched by id. */
function useConversationMeta(api, id, items, nonce) {
  const fromList = items.find((c) => c.id === id) || null
  const [fetched, setFetched] = useState({ id: null, conv: null, status: 'idle' })
  useEffect(() => {
    if (!id || fromList) return undefined
    let cancelled = false
    api
      .get(id)
      .then((conv) => !cancelled && setFetched({ id, conv, status: 'ready' }))
      .catch((err) => !cancelled && setFetched({ id, conv: null, status: err.status === 404 ? 'notfound' : 'error' }))
    return () => {
      cancelled = true
    }
  }, [api, id, !!fromList, nonce]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!id) return { conv: null, status: 'idle' }
  if (fromList) return { conv: fromList, status: 'ready' }
  if (fetched.id === id) return { conv: fetched.conv, status: fetched.status }
  return { conv: null, status: 'loading' }
}

export default function AppShell({ user, token, tokenEndpoint, onLogout, rtc }) {
  const [route, go] = useRoute()
  const [prefs, setPref] = usePrefs()
  const api = useConversationsApi({ tokenEndpoint, authToken: token })
  const list = useConversations(api)
  const archived = useArchived(api, { enabled: route.name === 'archived' })
  const isLg = useMediaQuery('(min-width: 1024px)')
  const [drawer, setDrawer] = useState(false)
  const [dialog, setDialog] = useState(null)
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [metaNonce, setMetaNonce] = useState(0)
  const searchRef = useRef(null)

  const activeId = route.name === 'chat' ? route.id : null
  const meta = useConversationMeta(api, activeId, list.items, metaNonce)
  const say = useCallback((title, body, tone = 'warn', extra = {}) => setNotice({ id: `${Date.now()}`, tone, title, body, autoHideMs: 7000, ...extra }), [])
  const dismissNotice = useCallback(() => setNotice(null), [])
  const fail = useCallback((err) => say('Kuch gadbad ho gayi', err.message), [say])
  const closeDialog = () => {
    setDialog(null)
    setBusy(false)
    setDialogError(null)
  }

  // "/" -> the most recent conversation, never a brand-new chat per refresh.
  useEffect(() => {
    if (route.name !== 'home' || list.status !== 'ready' || list.items.length === 0) return
    const latest = list.items.reduce((a, b) => (b.updated_at > a.updated_at ? b : a))
    go(chatPath(latest.id), { replace: true })
  }, [route.name, list.status, list.items, go])

  // Ctrl/Cmd+K focuses conversation search.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        if (isLg && prefs.sidebarCollapsed) setPref('sidebarCollapsed', false)
        else if (!isLg) setDrawer(true)
        setTimeout(() => searchRef.current?.focus(), 80)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isLg, prefs.sidebarCollapsed, setPref])

  const afterRemoval = useCallback(
    (id) => {
      if (activeId !== id) return
      const next = list.items.find((c) => c.id !== id)
      go(next ? chatPath(next.id) : '/', { replace: true })
    },
    [activeId, list.items, go],
  )

  // ---- actions ---------------------------------------------------------------
  const openChat = useCallback(
    (id) => {
      go(chatPath(id))
      setDrawer(false)
    },
    [go],
  )

  const newChat = useCallback(
    async (kind = 'text') => {
      setDrawer(false)
      const current = meta.conv
      // Already in an empty chat: reuse it (and focus the input) instead of piling up blanks.
      if (kind === 'text' && route.name === 'chat' && current && current.message_count === 0 && !current.archived) {
        document.querySelector('input[aria-label="Message"]')?.focus()
        return
      }
      try {
        const conv = await list.create({
          kind,
          participants: kind === 'collab' ? undefined : current?.participants,
          settings: { language: prefs.defaultLanguage, reply_length: prefs.defaultReplyLength },
        })
        go(chatPath(conv.id))
      } catch (err) {
        fail(err)
      }
    },
    [meta.conv, route.name, list, prefs.defaultLanguage, prefs.defaultReplyLength, go, fail],
  )

  const restoreConversation = useCallback(
    async (c) => {
      try {
        await api.patch(c.id, { archived: false })
        await list.refresh({ silent: true })
        archived.reload()
        setMetaNonce((n) => n + 1)
        say('Restored', `“${c.title}” wapas Recent mein hai.`, 'warn', { autoHideMs: 4000 })
      } catch (err) {
        fail(err)
      }
    },
    [api, list, archived, say, fail],
  )

  const togglePin = useCallback((c) => list.patch(c.id, { pinned: !c.pinned }).then(() => setMetaNonce((n) => n + 1)).catch(fail), [list, fail])

  const toggleArchive = useCallback(
    async (c) => {
      if (c.archived) return restoreConversation(c)
      try {
        await list.patch(c.id, { archived: true })
        afterRemoval(c.id)
        setMetaNonce((n) => n + 1)
        say('Archived', `“${c.title}” archive ho gayi.`, 'warn', { autoHideMs: 8000, action: { label: 'Undo', onClick: () => restoreConversation({ ...c, archived: true }) } })
      } catch (err) {
        fail(err)
      }
    },
    [list, afterRemoval, restoreConversation, say, fail],
  )

  const exportConversation = useCallback(
    async (c, fmt) => {
      try {
        const { blob, name } = await api.exportFile(c.id, fmt)
        downloadBlob(blob, name)
      } catch (err) {
        fail(err)
      }
    },
    [api, fail],
  )

  const confirmDelete = async () => {
    const c = dialog.conv
    setBusy(true)
    setDialogError(null)
    try {
      if (c.archived && route.name === 'archived') await archived.remove(c.id)
      else await list.remove(c.id)
      afterRemoval(c.id)
      closeDialog()
    } catch (err) {
      setDialogError(err.message)
      setBusy(false)
    }
  }

  const confirmClear = async () => {
    const c = dialog.conv
    setBusy(true)
    setDialogError(null)
    try {
      await api.clear(c.id)
      setReloadKey((k) => k + 1)
      list.refresh({ silent: true })
      closeDialog()
    } catch (err) {
      setDialogError(err.message)
      setBusy(false)
    }
  }

  const patchSettings = async (c, patch) => {
    await list.patch(c.id, patch)
    setMetaNonce((n) => n + 1)
    if (rtc.room) sendControl(rtc.room, { type: 'conversation_settings' }).catch(() => {})
  }

  const liveConv = (id) => list.items.find((c) => c.id === id) || (meta.conv?.id === id ? meta.conv : null)

  // ---- screens ---------------------------------------------------------------
  const showNav = !isLg
  const openNav = () => setDrawer(true)
  let content
  if (route.name === 'archived') {
    content = (
      <ArchivedView
        archived={archived}
        onOpen={openChat}
        onRestore={restoreConversation}
        onDelete={(c) => setDialog({ type: 'delete', conv: c })}
        showNavButton={showNav}
        onOpenNav={openNav}
      />
    )
  } else if (route.name === 'chat') {
    if (!rtc.room) {
      content = (
        <div className="flex h-full flex-col">
          <TopBar title="Roxstar AI" showNavButton={showNav} onOpenNav={openNav} />
          <div className="min-h-0 flex-1">
            {rtc.error ? (
              <CenterCard
                title="Connect nahi ho paya"
                body={rtc.error}
                action={<button type="button" onClick={rtc.reconnect} className={primaryAction}>Retry</button>}
              />
            ) : (
              <CenterCard busy title="Connecting…" body="AI Dost aur AI Sathi se jud rahe hain." />
            )}
          </div>
        </div>
      )
    } else if (meta.status === 'loading' || meta.status === 'idle') {
      content = (
        <div className="flex h-full flex-col">
          <TopBar title="" showNavButton={showNav} onOpenNav={openNav} />
          <CenterCard busy title="Opening conversation…" />
        </div>
      )
    } else if (!meta.conv) {
      content = (
        <div className="flex h-full flex-col">
          <TopBar title="Roxstar AI" showNavButton={showNav} onOpenNav={openNav} />
          <div className="min-h-0 flex-1">
            <CenterCard
              title={meta.status === 'notfound' ? 'Conversation nahi mili' : 'Conversation load nahi hui'}
              body={meta.status === 'notfound' ? 'Yeh delete ho gayi ho sakti hai, ya kisi aur account ki hai.' : 'Thodi der baad dobara try karo.'}
              action={
                <button type="button" onClick={() => newChat('text')} className={primaryAction}>
                  <PlusIcon size={17} />
                  New Chat
                </button>
              }
              secondary={
                meta.status === 'error' ? (
                  <button type="button" onClick={() => setMetaNonce((n) => n + 1)} className={quietAction}>
                    Retry
                  </button>
                ) : null
              }
            />
          </div>
        </div>
      )
    } else {
      content = (
        <Room
          room={rtc.room}
          connectionState={rtc.connectionState}
          micEnabled={rtc.micEnabled}
          micError={rtc.micError}
          onToggleMic={rtc.toggleMic}
          api={api}
          prefs={prefs}
          conversation={meta.conv}
          reloadKey={reloadKey}
          showNavButton={showNav}
          onOpenNav={openNav}
          onRename={(c) => setDialog({ type: 'rename', conv: c })}
          onPin={togglePin}
          onArchive={toggleArchive}
          onDelete={(c) => setDialog({ type: 'delete', conv: c })}
          onExport={exportConversation}
          onChatSettings={(c) => setDialog({ type: 'chatSettings', id: c.id })}
          onSaved={() => list.refresh({ silent: true })}
        />
      )
    }
  } else if (list.status === 'ready' && list.items.length === 0) {
    content = (
      <div className="flex h-full flex-col">
        <TopBar title="" showNavButton={showNav} onOpenNav={openNav} />
        <div className="min-h-0 flex-1">
          <Welcome onNew={newChat} />
        </div>
      </div>
    )
  } else if (list.status === 'error') {
    content = (
      <div className="flex h-full flex-col">
        <TopBar title="Roxstar AI" showNavButton={showNav} onOpenNav={openNav} />
        <CenterCard title="Couldn't load conversations." body={list.error} action={<button type="button" onClick={() => list.refresh()} className={primaryAction}>Retry</button>} />
      </div>
    )
  } else {
    content = (
      <div className="flex h-full flex-col">
        <TopBar title="" showNavButton={showNav} onOpenNav={openNav} />
        <CenterCard busy title="Loading…" />
      </div>
    )
  }

  const sidebarProps = {
    api,
    list,
    activeId,
    user,
    onNew: newChat,
    onOpen: openChat,
    onRename: (c) => setDialog({ type: 'rename', conv: c }),
    onPin: togglePin,
    onArchive: toggleArchive,
    onDelete: (c) => setDialog({ type: 'delete', conv: c }),
    onOpenArchived: () => {
      go('/archived')
      setDrawer(false)
    },
    onOpenSettings: () => {
      setDialog({ type: 'settings' })
      setDrawer(false)
    },
    onLogout,
    searchRef,
  }

  const chatSettingsConv = dialog?.type === 'chatSettings' ? liveConv(dialog.id) : null

  return (
    <div className="flex h-dvh w-screen overflow-hidden bg-[var(--color-bg)]">
      {isLg && (
        <aside className={`shrink-0 transition-[width] duration-200 ${prefs.sidebarCollapsed ? 'w-[60px]' : 'w-[280px]'}`}>
          <ChatSidebar {...sidebarProps} collapsed={prefs.sidebarCollapsed} onToggleCollapse={() => setPref('sidebarCollapsed', !prefs.sidebarCollapsed)} />
        </aside>
      )}
      <div className="min-w-0 flex-1 bg-[var(--color-surface)]">{content}</div>

      <Drawer open={!isLg && drawer} onClose={() => setDrawer(false)} title="Roxstar AI" side="left">
        <ChatSidebar {...sidebarProps} variant="drawer" />
      </Drawer>

      {dialog?.type === 'rename' && (
        <RenameDialog
          initial={dialog.conv.title}
          onCancel={closeDialog}
          onSave={async (title) => {
            await list.patch(dialog.conv.id, { title })
            setMetaNonce((n) => n + 1)
            closeDialog()
          }}
        />
      )}
      {dialog?.type === 'delete' && (
        <ConfirmDialog
          title="Delete conversation?"
          body="This conversation will be permanently deleted."
          confirmLabel="Delete"
          busy={busy}
          error={dialogError}
          onCancel={closeDialog}
          onConfirm={confirmDelete}
        />
      )}
      {dialog?.type === 'clear' && (
        <ConfirmDialog
          title="Clear messages?"
          body="This removes the messages from this conversation. Title and settings stay."
          confirmLabel="Clear"
          busy={busy}
          error={dialogError}
          onCancel={closeDialog}
          onConfirm={confirmClear}
        />
      )}
      {dialog?.type === 'settings' && (
        <SettingsDialog prefs={prefs} setPref={setPref} user={user} onLogout={onLogout} onClose={closeDialog} />
      )}
      {chatSettingsConv && (
        <ChatSettingsDialog
          conversation={chatSettingsConv}
          onPatch={(patch) => patchSettings(chatSettingsConv, patch)}
          onExport={(fmt) => exportConversation(chatSettingsConv, fmt)}
          onRequestClear={() => setDialog({ type: 'clear', conv: chatSettingsConv })}
          onClose={closeDialog}
        />
      )}

      <Notice notice={notice} onDismiss={dismissNotice} />
    </div>
  )
}
