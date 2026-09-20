// Drawer - a bottom sheet on phones, a side drawer from `md` up.
// Closes on Esc / backdrop; focus moves into the panel while it is open.

import { useEffect, useRef } from 'react'
import { CloseIcon } from './Icons'

export default function Drawer({ open, onClose, title, side = 'left', children }) {
  const panelRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const previous = document.activeElement
    panelRef.current?.focus()
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [open, onClose])

  if (!open) return null

  const sideClass =
    side === 'left' ? 'md:right-auto md:left-0 sheet-left' : 'md:left-auto md:right-0 sheet-right'

  return (
    <div className="fixed inset-0 z-40">
      <div className="fade-in absolute inset-0 bg-black/55" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`sheet-up absolute inset-x-0 bottom-0 flex max-h-[85dvh] flex-col overflow-hidden rounded-t-2xl bg-[var(--color-panel)] shadow-2xl outline-none md:inset-y-0 md:bottom-auto md:max-h-none md:w-[340px] md:rounded-none ${sideClass}`}
      >
        <div className="flex items-center justify-between px-4 py-3">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={`Close ${title}`}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
          >
            <CloseIcon size={18} />
          </button>
        </div>
        <div className="min-h-0 flex-1">{children}</div>
      </div>
    </div>
  )
}
