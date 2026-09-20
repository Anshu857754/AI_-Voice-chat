// MessageList - the conversation, the hero of the screen.
//
// AI messages sit left with an avatar and a soft bubble; yours sit right in a
// tinted bubble. Consecutive messages from one speaker are grouped under a
// single header. Live interim captions and a "thinking" row are shown inline.

import { useEffect, useRef } from 'react'
import Avatar from './Avatar'
import { BOTS, botColor, botLabel, formatClock } from '../lib/botMeta'
import { MicIcon } from './Icons'

const SUGGESTIONS = ['AI kya hota hai?', 'Cloud computing simple mein samjhao', 'Sathi, aaj ka din kaisa raha?', 'Dost, ek mazedaar fact batao']

// Identities look like `u<accountId>-<tab>`; a reload gets a new tab suffix, so
// compare the account part to keep your earlier messages on your side.
function sameSender(a, b) {
  if (!a || !b) return false
  if (a === b) return true
  const acct = (id) => /^(u\d+)-/.exec(id)?.[1]
  return !!acct(a) && acct(a) === acct(b)
}

function senderKey(turn, localIdentity) {
  if (turn.role === 'bot') return `bot:${turn.bot}`
  return sameSender(turn.identity, localIdentity) ? 'me' : `human:${turn.identity}`
}

function Header({ name, color, time, voice, interrupted, align = 'left' }) {
  return (
    <div className={`mb-1.5 flex items-baseline gap-2 ${align === 'right' ? 'justify-end' : ''}`}>
      <span className="text-sm font-medium" style={{ color }}>
        {name}
      </span>
      <span className="text-[11px] text-[var(--color-text-faint)]">{time}</span>
      {voice && <MicIcon size={11} className="self-center text-[var(--color-text-faint)]" aria-label="voice message" />}
      {interrupted && <span className="text-[11px] text-[var(--color-warn)]">interrupted</span>}
    </div>
  )
}

function TurnRow({ turn, localIdentity, first }) {
  const isBot = turn.role === 'bot'
  const isMe = !isBot && sameSender(turn.identity, localIdentity)
  const gap = first ? 'mt-7' : 'mt-1.5'

  if (isBot) {
    return (
      <div className={`${turn.restored ? '' : 'msg-in'} flex gap-3 ${gap}`}>
        <div className="w-9 shrink-0">{first && <Avatar bot={turn.bot} size={36} />}</div>
        <div className="min-w-0 flex-1">
          {first && <Header name={botLabel(turn.bot, turn.name)} color={botColor(turn.bot)} time={formatClock(turn.createdAt)} interrupted={turn.interrupted} />}
          <p className="inline-block max-w-[min(100%,42rem)] rounded-2xl rounded-tl-md bg-[var(--color-panel-raised)] px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap text-[var(--color-text)]">
            {turn.text}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className={`${turn.restored ? '' : 'msg-in'} flex flex-col ${isMe ? 'items-end' : 'items-start'} ${gap}`}>
      {first && <Header name={isMe ? 'You' : turn.name} color="var(--color-live)" time={formatClock(turn.createdAt)} voice={turn.source === 'voice'} align={isMe ? 'right' : 'left'} />}
      <p className="max-w-[min(85%,36rem)] rounded-2xl rounded-tr-md bg-[color-mix(in_srgb,var(--color-live)_13%,var(--color-surface))] px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap text-[var(--color-text)]">
        {turn.text}
      </p>
    </div>
  )
}

function InterimRow({ data }) {
  return (
    <div className="mt-2 flex flex-col items-end gap-1">
      <span className="text-[11px] text-[var(--color-text-faint)]">Transcribing…</span>
      <p className="max-w-[min(85%,36rem)] rounded-2xl border border-dashed border-white/10 px-4 py-2.5 text-[15px] text-[var(--color-text-dim)] italic">
        {data.text}
      </p>
    </div>
  )
}

function Thinking({ bots }) {
  const b = bots.find((x) => x.botState === 'thinking' || x.botState === 'generating')
  if (!b) return null
  const label = BOTS[b.botId]?.label || b.name
  return (
    <div className="msg-in mt-7 flex items-center gap-3" role="status" aria-label={`${label} soch raha hai`}>
      <Avatar bot={b.botId} size={36} />
      <div>
        <p className="text-sm font-medium" style={{ color: botColor(b.botId) }}>
          {label}
        </p>
        <p className="flex items-center gap-2 text-xs text-[var(--color-text-dim)]">
          {b.botState === 'generating' ? 'Writing' : 'Thinking'}
          <span className="inline-flex gap-1" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <span key={i} className="typing-dot h-1 w-1 rounded-full bg-[var(--color-text-dim)]" style={{ animationDelay: `${i * 0.18}s` }} />
            ))}
          </span>
        </p>
      </div>
    </div>
  )
}

function HistorySkeleton() {
  return (
    <div className="mt-7 space-y-6" aria-hidden="true">
      {[['w-2/3', false], ['w-1/3', true], ['w-3/4', false]].map(([w, right], i) => (
        <div key={i} className={`flex gap-3 ${right ? 'flex-row-reverse' : ''}`}>
          <span className="h-9 w-9 shrink-0 animate-pulse rounded-full bg-white/8" />
          <span className={`h-14 animate-pulse rounded-2xl bg-white/6 ${w}`} />
        </div>
      ))}
    </div>
  )
}

export default function MessageList({
  turns,
  interimBySpeaker,
  bots = [],
  localIdentity,
  onSuggest,
  status = 'ready',
  error,
  hasMore = false,
  loadingEarlier = false,
  onLoadEarlier,
  onRetry,
}) {
  const bottomRef = useRef(null)
  const scrollRef = useRef(null)
  const lastId = useRef(null)
  const interimEntries = Object.entries(interimBySpeaker || {})
  const thinkingKey = bots.map((b) => b.botState).join(',')
  const newestId = turns[turns.length - 1]?.id

  // Follow the conversation only when something NEW arrives (or a chat opens) -
  // never when older messages are prepended, which would jump the scroll position.
  useEffect(() => {
    if (lastId.current !== newestId) {
      const first = lastId.current === null
      lastId.current = newestId ?? null
      bottomRef.current?.scrollIntoView({ behavior: first ? 'auto' : 'smooth', block: 'end' })
    }
  }, [newestId])
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [interimEntries.length, thinkingKey])

  const isEmpty = status === 'ready' && turns.length === 0 && interimEntries.length === 0

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto" role="log" aria-live="polite" aria-busy={status === 'loading'} aria-label="Conversation">
      <div className="mx-auto max-w-3xl px-4 pt-2 pb-8 sm:px-6">
        {status === 'loading' && turns.length === 0 && <HistorySkeleton />}
        {status === 'error' && (
          <div className="mt-10 text-center" role="alert">
            <p className="mb-3 text-sm text-[var(--color-text-dim)]">Messages load nahi ho paye. {error}</p>
            <button type="button" onClick={onRetry} className="rounded-lg bg-[var(--color-panel-raised)] px-4 py-2 text-sm text-[var(--color-text)] hover:bg-white/10">
              Retry
            </button>
          </div>
        )}
        {hasMore && (
          <div className="mt-2 flex justify-center">
            <button type="button" onClick={onLoadEarlier} disabled={loadingEarlier} className="rounded-full bg-[var(--color-panel-raised)] px-4 py-1.5 text-xs text-[var(--color-text-dim)] transition-colors hover:text-[var(--color-text)]">
              {loadingEarlier ? 'Loading…' : 'Load earlier messages'}
            </button>
          </div>
        )}
        {isEmpty ? (
          <div className="flex min-h-[46vh] flex-col items-center justify-center gap-5 text-center">
            <h2 className="text-xl font-semibold text-[var(--color-text)]">Aaj kya baat karein?</h2>
            <p className="max-w-sm text-sm text-[var(--color-text-dim)]">Type karo, jawab text mein aayega. Awaaz chahiye to Voice dabao. Kisi ka naam lo (Dost / Sathi) to wahi jawab dega.</p>
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => onSuggest?.(s)}
                  className="rounded-full bg-[var(--color-panel-raised)] px-4 py-2 text-sm text-[var(--color-text-dim)] transition-colors hover:text-[var(--color-text)]"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {turns.map((turn, i) => (
              <TurnRow
                key={turn.id}
                turn={turn}
                localIdentity={localIdentity}
                first={i === 0 || senderKey(turns[i - 1], localIdentity) !== senderKey(turn, localIdentity)}
              />
            ))}
            {interimEntries.map(([identity, data]) => (
              <InterimRow key={identity} data={data} />
            ))}
          </>
        )}
        <Thinking bots={bots} />
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
