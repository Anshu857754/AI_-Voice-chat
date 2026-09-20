// DebugPanel - optional developer panel (spec section 19).
//
// Shows REAL measured latency stages and the most recent routing decision,
// sourced entirely from the `roxstar.state` topic the backend publishes from
// its own perf_counter() stamps (app/models/conversation.py::LatencyTrace).
// Nothing here is invented client-side.

const STAGE_LABELS = {
  stt_latency_ms: 'STT',
  router_latency_ms: 'Router',
  llm_ttft_ms: 'LLM (first token)',
  llm_latency_ms: 'LLM (full)',
  tts_latency_ms: 'TTS (first audio)',
  total_latency_ms: 'Total (speech end → audio out)',
}

function StatRow({ label, stat }) {
  if (!stat || stat.count === 0) {
    return (
      <div className="flex items-center justify-between py-1 text-xs">
        <span className="text-[var(--color-text-dim)]">{label}</span>
        <span className="text-[var(--color-text-dim)]">no samples yet</span>
      </div>
    )
  }
  return (
    <div className="flex items-center justify-between py-1 text-xs">
      <span className="text-[var(--color-text-dim)]">{label}</span>
      <span className="font-mono text-[var(--color-text)]">
        p50 {stat.p50_ms}ms · p95 {stat.p95_ms}ms · n={stat.count}
      </span>
    </div>
  )
}

function ConnectionInfo({ room, connectionState, participants, lastError }) {
  const bots = participants.filter((p) => p.isBot)
  const label = 'mb-1 text-[10px] font-medium tracking-wider text-[var(--color-text-dim)] uppercase'
  return (
    <div className="mb-4 grid grid-cols-1 gap-4 border-b border-[var(--color-border)] pb-3 md:grid-cols-3">
      <div>
        <p className={label}>Connection</p>
        <p className="text-xs text-[var(--color-text)]">token: issued</p>
        <p className="text-xs text-[var(--color-text)]">room: {room?.name || '-'}</p>
        <p className="text-xs text-[var(--color-text)]">state: {connectionState}</p>
      </div>
      <div>
        <p className={label}>Participants ({participants.length})</p>
        {participants.map((p) => (
          <p key={p.identity} className="truncate text-xs text-[var(--color-text)]">
            {p.name}
            {p.isLocal ? ' (you)' : ''}
            {p.isBot ? ' · AI' : ''}
          </p>
        ))}
      </div>
      <div>
        <p className={label}>Agent status</p>
        {bots.length === 0 ? (
          <p className="text-xs text-[var(--color-danger)]">not joined - is `python -m app.agent_worker` running?</p>
        ) : (
          bots.map((b) => (
            <p key={b.identity} className="text-xs text-[var(--color-text)]">
              {b.name}: {b.botState || 'unknown'}
            </p>
          ))
        )}
        <p className={`${label} mt-3`}>Last error</p>
        <p className={`text-xs ${lastError ? 'text-[var(--color-danger)]' : 'text-[var(--color-text-dim)]'}`}>
          {lastError || 'none'}
        </p>
      </div>
    </div>
  )
}

export default function DebugPanel({ state, room, connectionState, participants = [], lastError, onClose }) {
  const conn = (
    <ConnectionInfo room={room} connectionState={connectionState} participants={participants} lastError={lastError} />
  )
  if (!state) {
    return (
      <div className="max-h-72 overflow-y-auto border-t border-[var(--color-border)] bg-[var(--color-panel)] px-4 py-3 text-xs text-[var(--color-text-dim)]">
        {conn}
        No debug data yet — waiting for the agent worker to publish state.
      </div>
    )
  }

  const { routing, metrics, context, interruptions, providers } = state

  return (
    <div className="max-h-72 overflow-y-auto border-t border-[var(--color-border)] bg-[var(--color-panel)] px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold tracking-wide text-[var(--color-text)] uppercase">
          Debug · Latency &amp; Routing
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
        >
          Close
        </button>
      </div>

      {conn}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div>
          <p className="mb-1 text-[10px] font-medium tracking-wider text-[var(--color-text-dim)] uppercase">
            Providers
          </p>
          <p className="text-xs text-[var(--color-text)]">LLM: {providers?.llm_model || providers?.llm}</p>
          <p className="text-xs text-[var(--color-text)]">STT: {providers?.stt}</p>
          <p className="text-xs text-[var(--color-text)]">
            TTS: dost={providers?.tts?.dost} sathi={providers?.tts?.sathi}
          </p>

          <p className="mt-3 mb-1 text-[10px] font-medium tracking-wider text-[var(--color-text-dim)] uppercase">
            Last routing decision
          </p>
          {routing ? (
            <>
              <p className="text-xs text-[var(--color-text)]">
                should_respond={String(routing.should_respond)} bot={routing.selected_bot || '-'}
              </p>
              <p className="text-xs text-[var(--color-text-dim)]">
                reason={routing.reason} conf={routing.confidence}
              </p>
              {routing.queued?.length > 0 && (
                <p className="text-xs text-[var(--color-text-dim)]">queued={routing.queued.join(', ')}</p>
              )}
            </>
          ) : (
            <p className="text-xs text-[var(--color-text-dim)]">no decision yet</p>
          )}

          <p className="mt-3 mb-1 text-[10px] font-medium tracking-wider text-[var(--color-text-dim)] uppercase">
            Interruptions
          </p>
          <p className="text-xs text-[var(--color-text)]">count={interruptions?.count ?? 0}</p>
        </div>

        <div>
          <p className="mb-1 text-[10px] font-medium tracking-wider text-[var(--color-text-dim)] uppercase">
            Latency (observed)
          </p>
          {metrics?.stages &&
            Object.entries(STAGE_LABELS).map(([key, label]) => (
              <StatRow key={key} label={label} stat={metrics.stages[key]} />
            ))}
        </div>

        <div>
          <p className="mb-1 text-[10px] font-medium tracking-wider text-[var(--color-text-dim)] uppercase">
            Context
          </p>
          <p className="text-xs text-[var(--color-text)]">turns={context?.turns}</p>
          <p className="text-xs text-[var(--color-text)]">summary_chars={context?.summary_chars}</p>
          <p className="text-xs text-[var(--color-text)]">topic={context?.current_topic || '-'}</p>
          <p className="text-xs text-[var(--color-text)]">last_responder={context?.last_responder || '-'}</p>
        </div>
      </div>
    </div>
  )
}
