// Notice - one compact, dismissible notification (top right, never in the
// layout flow). Amber = recoverable, red = serious. Recoverable notices hide
// themselves after a while.

import { useEffect } from 'react'
import { CloseIcon, WarnIcon } from './Icons'

export default function Notice({ notice, onDismiss }) {
  useEffect(() => {
    if (!notice?.autoHideMs) return undefined
    const t = setTimeout(() => onDismiss(notice.id), notice.autoHideMs)
    return () => clearTimeout(t)
  }, [notice, onDismiss])

  if (!notice) return null
  const tone = notice.tone === 'error' ? 'var(--color-danger)' : 'var(--color-warn)'

  return (
    <div className="pointer-events-none fixed inset-x-0 top-16 z-50 flex justify-center px-3 sm:justify-end sm:px-5">
      <div
        role={notice.tone === 'error' ? 'alert' : 'status'}
        key={notice.id}
        className="toast-in pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl bg-[var(--color-panel-raised)] py-3 pr-2 pl-4 shadow-xl ring-1 ring-white/8"
      >
        <span className="mt-0.5 shrink-0" style={{ color: tone }}>
          <WarnIcon size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-[var(--color-text)]">{notice.title}</p>
          {notice.body && <p className="mt-0.5 text-xs leading-relaxed text-[var(--color-text-dim)]">{notice.body}</p>}
        </div>
        {notice.action && (
          <button
            type="button"
            onClick={() => {
              notice.action.onClick()
              onDismiss(notice.id)
            }}
            className="mt-0.5 shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-medium text-[var(--color-text)] transition-colors hover:bg-[var(--color-hover)]"
          >
            {notice.action.label}
          </button>
        )}
        <button
          type="button"
          onClick={() => onDismiss(notice.id)}
          aria-label="Dismiss notification"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
        >
          <CloseIcon size={16} />
        </button>
      </div>
    </div>
  )
}
