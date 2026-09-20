// VoiceControls - the composer, with an always-visible mode indicator.
//
//   TEXT MODE   ● Text chat        [ mic ] Kuch bhi poochho…            [ Voice ]
//   VOICE MODE  ● Voice mode       [ mic ] ● Listening… ▁▃▅▇      [ ⤢ ] [ Stop Voice ]
//
// Text mode: typed messages are answered in text only, the mic is off.
// Voice mode starts ONLY when the user presses Voice (or the mic button) and
// ends with Stop Voice. Typed messages use the same `send` from useChat.
// Press "/" to focus the input.

import { useEffect } from 'react'
import Waveform from './Waveform'
import { ExpandIcon, MicIcon, MicOffIcon, SendIcon, StopIcon, WaveIcon } from './Icons'
import { BOTS, botColor } from '../lib/botMeta'

function ModeLine({ mode, speakingBot, preparingBot, micPaused, responsePref, onSetResponsePref }) {
  const voice = mode === 'voice'
  return (
    <div className="mb-2 flex items-center gap-2 px-1 text-xs" role="status">
      <span
        className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-medium ${
          voice ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]' : 'bg-white/5 text-[var(--color-text-dim)]'
        }`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${voice ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-text-faint)]'}`} aria-hidden="true" />
        {voice ? 'Voice mode' : 'Text chat'}
      </span>
      {preparingBot && !speakingBot ? (
        <span className="fade-in inline-flex items-center gap-2 text-[var(--color-text-dim)]" role="status">
          <Waveform color={botColor(preparingBot.botId)} bars={4} height={12} />
          Preparing voice…
        </span>
      ) : speakingBot ? (
        <span className="fade-in inline-flex items-center gap-2">
          <Waveform active color={botColor(speakingBot.botId)} bars={4} height={12} />
          <span className="font-medium" style={{ color: botColor(speakingBot.botId) }}>
            {BOTS[speakingBot.botId]?.label || speakingBot.name}
          </span>
          <span className="text-[var(--color-text-dim)]">Speaking</span>
        </span>
      ) : responsePref === 'voice' && !voice ? (
        <span className="inline-flex items-center gap-2 text-[var(--color-text-dim)]">
          Replies: text + voice
          <button type="button" onClick={() => onSetResponsePref?.('text')} className="rounded-full bg-white/8 px-2 py-0.5 text-[var(--color-text)] hover:bg-white/15">
            Text only
          </button>
        </span>
      ) : responsePref === 'text' && voice ? (
        <span className="inline-flex items-center gap-2 text-[var(--color-text-dim)]">
          Replies: text only
          <button type="button" onClick={() => onSetResponsePref?.('auto')} className="rounded-full bg-white/8 px-2 py-0.5 text-[var(--color-text)] hover:bg-white/15">
            Allow voice
          </button>
        </span>
      ) : (
        <span className="text-[var(--color-text-faint)]">
          {voice ? (micPaused ? 'AI bolne ke baad mic wapas chalu hoga' : 'AI text ke saath awaaz mein bhi jawab dega') : 'AI sirf text mein jawab dega'}
        </span>
      )}
    </div>
  )
}

export default function VoiceControls({
  mode,
  draft,
  onDraftChange,
  inputRef,
  micEnabled,
  micPaused,
  onToggleMic,
  onEnterVoice,
  onExitVoice,
  onOpenFullscreen,
  onSend,
  sending,
  connectionState,
  bots = [],
  userSpeaking = false,
  ready = true,
  placeholder,
  voiceAllowed = true,
  responsePref = 'auto',
  onSetResponsePref,
}) {
  const voice = mode === 'voice'
  const online = connectionState === 'connected' && ready
  const canVoice = online && voiceAllowed
  const voiceTitle = voiceAllowed ? 'Start voice mode' : 'Is chat mein voice band hai (chat settings)'
  const canSend = draft.trim() && online && !sending
  const speakingBot = bots.find((b) => b.botState === 'speaking')
  const preparingBot = bots.find((b) => b.botState === 'synthesizing')

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return
      const tag = document.activeElement?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      e.preventDefault()
      inputRef?.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [inputRef])

  const submit = (e) => {
    e.preventDefault()
    if (!canSend || voice) return
    onSend(draft)
    onDraftChange('')
  }

  const iconBtn = 'flex h-11 w-11 shrink-0 items-center justify-center rounded-full transition-colors sm:h-10 sm:w-10'
  const pillBtn =
    'flex h-11 shrink-0 items-center gap-2 rounded-full px-4 text-sm font-medium transition sm:h-10'

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pt-2 pb-4 sm:px-6">
      <ModeLine
        mode={mode}
        speakingBot={speakingBot}
        preparingBot={preparingBot}
        micPaused={micPaused}
        responsePref={responsePref}
        onSetResponsePref={onSetResponsePref}
      />
      <form
        onSubmit={submit}
        className="flex items-center gap-2 rounded-2xl bg-[var(--color-input)] p-2 shadow-lg shadow-black/20 ring-1 ring-white/6 transition-shadow focus-within:ring-[var(--color-accent)]/55"
      >
        {voice ? (
          <button
            type="button"
            onClick={onToggleMic}
            aria-pressed={micEnabled}
            aria-label={micEnabled ? 'Mic mute karo' : 'Mic unmute karo'}
            title={micEnabled ? 'Mute microphone' : 'Unmute microphone'}
            className={`${iconBtn} ${
              micPaused
                ? 'bg-[var(--color-warn)]/15 text-[var(--color-warn)]'
                : micEnabled
                  ? 'bg-[var(--color-live)]/15 text-[var(--color-live)]'
                  : 'bg-white/5 text-[var(--color-text-dim)] hover:text-[var(--color-text)]'
            }`}
          >
            {micEnabled ? <MicIcon size={18} /> : <MicOffIcon size={18} />}
          </button>
        ) : (
          <button
            type="button"
            onClick={onEnterVoice}
            disabled={!canVoice}
            aria-label="Voice mode chalu karo"
            title={voiceTitle}
            className={`${iconBtn} text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] disabled:opacity-40`}
          >
            <MicIcon size={18} />
          </button>
        )}

        {voice ? (
          <div className="flex min-w-0 flex-1 items-center gap-3 px-1 text-sm" role="status">
            {!micEnabled ? (
              <>
                <span className="h-2 w-2 shrink-0 rounded-full border-[1.5px] border-[var(--color-text-faint)]" aria-hidden="true" />
                <span className="truncate text-[var(--color-text-dim)]">Mic muted</span>
              </>
            ) : micPaused ? (
              <>
                <span className="h-2 w-2 shrink-0 rounded-full border-[1.5px] border-[var(--color-warn)]" aria-hidden="true" />
                <span className="truncate text-[var(--color-warn)]">Mic paused · AI bol raha hai</span>
              </>
            ) : (
              <>
                <span className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-live)]" aria-hidden="true" />
                <span className="truncate text-[var(--color-text)]">{userSpeaking ? 'Sun raha hoon…' : 'Listening…'}</span>
                <Waveform active={userSpeaking} color="var(--color-live)" bars={10} height={18} className="ml-auto" />
              </>
            )}
          </div>
        ) : (
          <input
            ref={inputRef}
            type="text"
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            disabled={!online}
            placeholder={placeholder ?? (online ? 'Kuch bhi poochho…' : 'Reconnecting…')}
            aria-label="Message"
            aria-keyshortcuts="/"
            autoComplete="off"
            className="min-w-0 flex-1 bg-transparent px-1 py-2 text-[15px] text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-faint)]"
          />
        )}

        {voice ? (
          <>
            <button
              type="button"
              onClick={onOpenFullscreen}
              aria-label="Full screen voice"
              title="Full screen voice"
              className={`${iconBtn} hidden text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] sm:flex`}
            >
              <ExpandIcon size={17} />
            </button>
            <button
              type="button"
              onClick={onExitVoice}
              className={`${pillBtn} bg-[var(--color-danger)]/15 text-[var(--color-danger)] hover:bg-[var(--color-danger)]/25`}
            >
              <StopIcon size={14} />
              Stop Voice
            </button>
          </>
        ) : draft.trim() ? (
          <button
            type="submit"
            disabled={!canSend}
            aria-label="Send message"
            className={`${iconBtn} bg-[var(--color-accent)] text-white hover:brightness-110 disabled:opacity-40`}
          >
            <SendIcon size={18} />
          </button>
        ) : (
          <button
            type="button"
            onClick={onEnterVoice}
            disabled={!canVoice}
            aria-label="Start voice mode"
            title={voiceTitle}
            className={`${pillBtn} bg-[var(--color-accent)] text-white hover:brightness-110 disabled:opacity-40`}
          >
            <WaveIcon size={16} />
            Voice
          </button>
        )}
      </form>
      <p className="mt-2 hidden text-center text-[11px] text-[var(--color-text-faint)] sm:block">
        AI se galti ho sakti hai
        {!voice && (
          <>
            {' · '}
            <kbd className="rounded bg-white/8 px-1">/</kbd> dabao type karne ke liye
          </>
        )}
      </p>
    </div>
  )
}
