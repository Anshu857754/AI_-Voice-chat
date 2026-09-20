// Room - the open conversation (header, messages, composer) plus the right-hand
// context panel (Context | Transcript | People).
//
//   >= 1280px   conversation | context panel (collapsible)
//   <  1280px   conversation, context panel as a drawer / bottom sheet
//
// One LiveKit room stays connected across conversations. Opening a conversation
// tells the worker which one is active (control message `conversation`); the
// composer stays disabled until the worker confirms, so a message can never be
// filed under the previous chat. Opening a conversation ALWAYS starts in text
// mode with the mic off, even for a chat that was a voice chat before.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Header from './Header'
import ParticipantList from './ParticipantList'
import ContextPanel from './ContextPanel'
import MessageList from './MessageList'
import VoiceControls from './VoiceControls'
import VoiceMode from './VoiceMode'
import DebugPanel from './DebugPanel'
import Drawer from './Drawer'
import Notice from './Notice'
import { RestoreIcon } from './Icons'
import { useParticipants } from '../hooks/useParticipants'
import { useChat } from '../hooks/useChat'
import { useTranscript } from '../hooks/useTranscript'
import { useDebugState } from '../hooks/useDebugState'
import { useMicPause } from '../hooks/useMicPause'
import { useInteractionMode } from '../hooks/useInteractionMode'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { sendControl } from '../services/livekit'
import { BOTS, BUSY_STATES } from '../lib/botMeta'

/** The single most relevant thing to tell the user right now (or null). */
function pickNotice({ lastError, sendError, micError, onRetryVoice, onRetryStt }) {
  if (lastError?.kind === 'stt') {
    return {
      id: `err-${lastError.at}`,
      tone: 'warn',
      title: "Couldn't understand the audio",
      body: 'Voice input stopped. Text chat still works.',
      autoHideMs: 12000,
      action: { label: 'Retry', onClick: onRetryStt },
    }
  }
  if (lastError) {
    const tts = lastError.kind === 'tts'
    return {
      id: `err-${lastError.at}`,
      tone: tts ? 'warn' : 'error',
      title: tts ? 'Voice unavailable' : 'AI response failed',
      body: tts ? 'Voice unavailable — text response shown.' : lastError.message,
      autoHideMs: tts ? 10000 : 0,
      action: tts ? { label: 'Retry', onClick: onRetryVoice } : null,
    }
  }
  if (sendError) return { id: `send-${sendError}`, tone: 'warn', title: 'Message nahi gaya', body: sendError, autoHideMs: 8000 }
  if (micError) return { id: `mic-${micError}`, tone: 'warn', title: 'Mic problem', body: micError, autoHideMs: 10000 }
  return null
}

export default function Room({
  room,
  connectionState,
  micEnabled,
  micError,
  onToggleMic,
  api,
  prefs,
  conversation,
  reloadKey,
  showNavButton,
  onOpenNav,
  onRename,
  onPin,
  onArchive,
  onDelete,
  onExport,
  onChatSettings,
  onSaved,
}) {
  const conversationId = conversation.id
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [fullscreenVoice, setFullscreenVoice] = useState(false)
  const [contextOpen, setContextOpen] = useState(true) // inline panel (xl+)
  const [contextDrawer, setContextDrawer] = useState(false)
  const [dismissed, setDismissed] = useState(() => new Set())
  const [draft, setDraft] = useState('')
  const inputRef = useRef(null)
  const activatedAt = useRef(0)
  const [displaced, setDisplaced] = useState(false)

  const isXl = useMediaQuery('(min-width: 1280px)')

  const { humans, bots, isSpeaking } = useParticipants(room)
  const transcript = useTranscript(room, { api, conversationId, reloadKey })
  const { turns, interimBySpeaker } = transcript
  const { send, sending, sendError } = useChat(room)
  const debugState = useDebugState(room)
  const micPaused = useMicPause(room, bots, micEnabled, prefs.micAutoPause)
  const { mode, enterVoice, exitVoice } = useInteractionMode({
    room,
    micEnabled,
    onToggleMic,
    voiceUsers: debugState?.voice_users,
    resetKey: conversationId,
  })
  const voiceMode = mode === 'voice'
  const localIdentity = room.localParticipant.identity
  const online = connectionState === 'connected'
  const voiceAllowed = conversation.settings.voice_enabled !== false

  // ---- tell the worker which conversation is open ----
  const activate = useCallback(
    (reload = false) => {
      activatedAt.current = Date.now()
      setDisplaced(false)
      return sendControl(room, { type: 'conversation', id: conversationId, ...(reload ? { reload: true } : {}) }).catch(() => {})
    },
    [room, conversationId],
  )
  useEffect(() => {
    if (online) activate()
  }, [online, activate])
  useEffect(() => {
    if (reloadKey > 0) activate(true) // cleared: rebuild the worker's context from the (now empty) chat
  }, [reloadKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const workerConv = debugState?.conversation
  const confirmed = workerConv?.id === conversationId
  // The worker serves ONE conversation at a time. If a different session (another tab /
  // device) opened another chat, do not fight over it: say so and let the user take over.
  // Only when the worker restarted (nothing active) or missed OUR message do we re-send.
  // ...but only if that other session is still in the room; a closed tab must not keep the AI "busy".
  const ownerElsewhere =
    !!workerConv?.id && !confirmed && !!workerConv.by && workerConv.by !== localIdentity && humans.some((p) => p.identity === workerConv.by)
  useEffect(() => {
    if (!workerConv || confirmed || Date.now() - activatedAt.current < 3000) return
    if (ownerElsewhere) setDisplaced(true)
    else activate()
  }, [workerConv, confirmed, ownerElsewhere, activate])

  // Sidebar preview / title / ordering follow every saved message (worker bumps `seq`).
  const seq = workerConv?.seq
  useEffect(() => {
    if (seq === undefined) return undefined
    const t = setTimeout(() => onSaved?.(), 350)
    return () => clearTimeout(t)
  }, [seq, onSaved])

  // An unsent draft belongs to the chat it was typed in.
  useEffect(() => setDraft(''), [conversationId])

  // A fresh conversation is ready to type into.
  useEffect(() => {
    if (confirmed) inputRef.current?.focus()
  }, [confirmed, conversationId])

  // Settings the worker applied (participants, language...) come from the saved conversation.
  const aiModeOn = confirmed ? !!debugState?.ai_mode?.enabled : !!conversation.settings.ai_collab
  const aiBusy = bots.some((b) => BUSY_STATES.includes(b.botState))
  // A TTS error can only exist if audio was actually requested (voice mode, a "talk to me" preference
  // or an explicit ask) - text-only replies never call TTS - and the worker clears it on the next good reply.
  const lastError = debugState?.last_error || null
  const responsePref = confirmed ? workerConv?.response_pref || 'auto' : 'auto'

  const retryVoice = useCallback(() => sendControl(room, { type: 'retry_voice' }).catch(() => {}), [room])
  const setResponsePref = useCallback((value) => sendControl(room, { type: 'response_pref', value }).catch(() => {}), [room])
  // Retry after an STT failure = restart voice input (leave and re-enter voice mode -> a fresh STT session).
  const retryStt = useCallback(() => {
    exitVoice()
    setTimeout(() => enterVoice(), 500)
  }, [exitVoice, enterVoice])
  const notice = useMemo(() => {
    const n = pickNotice({ lastError, sendError, micError, onRetryVoice: retryVoice, onRetryStt: retryStt })
    return n && !dismissed.has(n.id) ? n : null
  }, [lastError, sendError, micError, dismissed, retryVoice, retryStt])
  const dismissNotice = useCallback((id) => setDismissed((prev) => new Set(prev).add(id)), [])

  const toggleAiMode = () => sendControl(room, { type: 'ai_mode', enabled: !aiModeOn }).catch(() => {})
  const pauseAi = () => sendControl(room, { type: 'stop' }).catch(() => {})

  // Voice is entered and left only by the user, from the buttons below.
  const leaveVoice = () => {
    setFullscreenVoice(false)
    exitVoice()
  }
  const toggleVoice = () => (voiceMode ? leaveVoice() : enterVoice())

  const toggleContext = () => (isXl ? setContextOpen((v) => !v) : setContextDrawer((v) => !v))
  const contextShown = isXl ? contextOpen : contextDrawer

  // Picking an AI in the People tab starts a message addressed to it.
  const selectBot = (bot) => {
    const short = BOTS[bot.botId]?.short
    if (short && !voiceMode) {
      setDraft(`${short}, `)
      setTimeout(() => inputRef.current?.focus(), 0)
    }
    setContextDrawer(false)
  }

  // Desktop notification for an AI reply while the tab is in the background.
  const notified = useRef(new Set())
  useEffect(() => {
    if (!prefs.notifyReplies || !document.hidden || !('Notification' in window) || Notification.permission !== 'granted') return
    const last = turns[turns.length - 1]
    if (!last || last.role !== 'bot' || last.restored || notified.current.has(last.id)) return
    notified.current.add(last.id)
    new Notification(BOTS[last.bot]?.label || 'Roxstar AI', { body: last.text.slice(0, 120), tag: last.id })
  }, [turns, prefs.notifyReplies])

  const lastBotTurn = [...turns].reverse().find((t) => t.role === 'bot')
  const caption = interimBySpeaker[localIdentity]?.text || lastBotTurn?.text || ''

  const people = `${bots.length} AI · ${humans.length} ${humans.length === 1 ? 'Human' : 'Humans'}`
  const context = (
    <ContextPanel
      turns={turns}
      localIdentity={localIdentity}
      topic={confirmed ? debugState?.context?.current_topic : ''}
      summary={confirmed ? debugState?.context?.summary : ''}
      collabOn={aiModeOn}
      people={people}
      peoplePanel={
        <ParticipantList
          humans={humans}
          bots={bots}
          isSpeaking={isSpeaking}
          lastError={lastError}
          userSpeaking={voiceMode && isSpeaking(localIdentity)}
          onSelectBot={selectBot}
        />
      }
    />
  )

  const composerPlaceholder = !online
    ? undefined
    : bots.length === 0
      ? 'AI participants abhi join nahi hue…'
      : displaced
        ? 'AI abhi doosre session mein use ho raha hai…'
        : !confirmed
          ? 'Conversation khul rahi hai…'
          : undefined

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-[var(--color-bg)]">
      <Header
        conversation={conversation}
        bots={bots}
        humans={humans}
        connectionState={connectionState}
        aiModeOn={aiModeOn}
        aiBusy={aiBusy}
        voiceMode={voiceMode}
        voiceAllowed={voiceAllowed}
        onToggleAiMode={toggleAiMode}
        onPauseAi={pauseAi}
        onToggleVoice={toggleVoice}
        contextOpen={contextShown}
        onToggleContext={toggleContext}
        showNavButton={showNavButton}
        onOpenNav={onOpenNav}
        onRename={() => onRename(conversation)}
        onPin={() => onPin(conversation)}
        onArchive={() => onArchive(conversation)}
        onDelete={() => onDelete(conversation)}
        onExport={(fmt) => onExport(conversation, fmt)}
        onChatSettings={() => onChatSettings(conversation)}
        onOpenDiagnostics={() => setSettingsOpen((v) => !v)}
      />

      {conversation.archived && (
        <div className="flex items-center justify-between gap-3 bg-white/5 px-4 py-2 text-xs text-[var(--color-text-dim)]" role="status">
          <span>Yeh conversation archived hai. Aap ise padh aur continue kar sakte ho.</span>
          <button type="button" onClick={() => onArchive(conversation)} className="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-[var(--color-text)] hover:bg-white/10">
            <RestoreIcon size={14} />
            Restore
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col bg-[var(--color-surface)]" aria-label="Conversation">
          <div className="min-h-0 flex-1 pt-3">
            <MessageList
              turns={turns}
              interimBySpeaker={interimBySpeaker}
              bots={bots}
              localIdentity={localIdentity}
              onSuggest={send}
              status={transcript.status}
              error={transcript.error}
              hasMore={transcript.hasMore}
              loadingEarlier={transcript.loadingEarlier}
              onLoadEarlier={transcript.loadEarlier}
              onRetry={transcript.retry}
            />
          </div>
          {displaced && !confirmed && (
            <div className="mx-auto mb-2 flex w-full max-w-3xl items-center justify-between gap-3 rounded-xl bg-[var(--color-warn)]/10 px-4 py-2.5 text-xs text-[var(--color-warn)]" role="status">
              <span>AI abhi kisi aur tab / device ki conversation ke saath use ho raha hai. Ek waqt mein ek hi conversation active rehti hai.</span>
              <button type="button" onClick={() => activate()} className="shrink-0 rounded-lg bg-[var(--color-warn)]/20 px-3 py-1.5 font-medium text-[var(--color-text)] hover:bg-[var(--color-warn)]/30">
                Yahan use karo
              </button>
            </div>
          )}
          <VoiceControls
            mode={mode}
            draft={draft}
            onDraftChange={setDraft}
            inputRef={inputRef}
            micEnabled={micEnabled}
            micPaused={micPaused}
            onToggleMic={onToggleMic}
            onEnterVoice={enterVoice}
            onExitVoice={leaveVoice}
            onOpenFullscreen={() => setFullscreenVoice(true)}
            onSend={send}
            sending={sending}
            connectionState={connectionState}
            bots={bots}
            userSpeaking={isSpeaking(localIdentity)}
            ready={confirmed && bots.length > 0}
            placeholder={composerPlaceholder}
            voiceAllowed={voiceAllowed}
            responsePref={responsePref}
            onSetResponsePref={setResponsePref}
          />
        </main>

        {isXl && contextOpen && <div className="w-80 shrink-0">{context}</div>}
      </div>

      {settingsOpen && (
        <div className="fixed inset-x-0 bottom-0 z-30 max-h-[45dvh] overflow-y-auto shadow-2xl">
          <DebugPanel
            state={debugState}
            room={room}
            connectionState={connectionState}
            participants={[...humans, ...bots]}
            lastError={sendError || micError}
            onClose={() => setSettingsOpen(false)}
          />
        </div>
      )}

      <Drawer open={!isXl && contextDrawer} onClose={() => setContextDrawer(false)} title="Room context" side="right">
        {context}
      </Drawer>

      <Notice notice={notice} onDismiss={dismissNotice} />

      {voiceMode && fullscreenVoice && (
        <VoiceMode
          bots={bots}
          userSpeaking={isSpeaking(localIdentity)}
          micEnabled={micEnabled}
          micPaused={micPaused}
          onToggleMic={onToggleMic}
          onClose={leaveVoice}
          caption={caption}
        />
      )}
      {!online && <span className="sr-only" role="status">Connection {connectionState}</span>}
    </div>
  )
}
