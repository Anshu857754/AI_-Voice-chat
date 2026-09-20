// Chat - RIGHT panel: room chat + shared context.
//
// Shows the `lk.chat` stream: every typed message plus every bot reply
// mirrored to chat (bots always mirror their spoken answer to chat, so this
// panel also serves as a readable log when TTS is degraded or muted).

import { useEffect, useRef } from 'react'

const BOT_COLOR = { dost: 'var(--color-dost)', sathi: 'var(--color-sathi)' }

function bubbleAccent(msg) {
  if (msg.identity?.includes('dost')) return BOT_COLOR.dost
  if (msg.identity?.includes('sathi')) return BOT_COLOR.sathi
  return 'var(--color-live)'
}

function formatTime(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function Chat({ messages, contextSummary }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length])

  return (
    <aside className="flex h-full w-full flex-col overflow-hidden border-l border-[var(--color-border)] bg-[var(--color-panel)]">
      <div className="border-b border-[var(--color-border)] px-4 py-3.5">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">Room Chat</h2>
        <p className="text-xs text-[var(--color-text-dim)]">Text in, spoken replies mirrored here</p>
      </div>

      {contextSummary ? (
        <div className="border-b border-[var(--color-border)] bg-[var(--color-panel-raised)] px-4 py-3">
          <p className="mb-1 text-[10px] font-medium tracking-wider text-[var(--color-text-dim)] uppercase">
            Room context
          </p>
          <p className="line-clamp-4 text-xs leading-relaxed text-[var(--color-text-dim)]">{contextSummary}</p>
        </div>
      ) : null}

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <p className="px-1 py-2 text-xs text-[var(--color-text-dim)]">No messages yet.</p>
        ) : (
          <div className="space-y-2.5">
            {messages.map((msg) => (
              <div key={msg.id} className={`flex flex-col ${msg.isLocal ? 'items-end' : 'items-start'}`}>
                <div className="mb-0.5 flex items-center gap-1.5 px-1">
                  <span className="text-[11px] font-medium" style={{ color: bubbleAccent(msg) }}>
                    {msg.isLocal ? 'You' : msg.name}
                  </span>
                  <span className="text-[10px] text-[var(--color-text-dim)]">{formatTime(msg.at)}</span>
                </div>
                <div
                  className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
                    msg.isLocal
                      ? 'rounded-tr-sm bg-[var(--color-accent-soft)] text-[var(--color-text)]'
                      : 'rounded-tl-sm bg-[var(--color-panel-raised)] text-[var(--color-text)]'
                  }`}
                >
                  {msg.text}
                </div>
              </div>
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </aside>
  )
}
