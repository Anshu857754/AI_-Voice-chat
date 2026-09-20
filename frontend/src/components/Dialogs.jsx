// Dialogs - accessible modal + the rename / confirm dialogs built on it.
//
// role="dialog" + aria-modal, focus moves in, Tab is kept inside, Esc or a
// backdrop click closes, and focus returns to whatever opened it.

import { useEffect, useRef, useState } from 'react'
import { CloseIcon } from './Icons'

const FOCUSABLE = 'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'

export function Modal({ title, onClose, children, maxWidth = 'max-w-md', initialFocus }) {
  const panelRef = useRef(null)

  useEffect(() => {
    const previous = document.activeElement
    const target = (initialFocus && panelRef.current?.querySelector(initialFocus)) || panelRef.current?.querySelector(FOCUSABLE) || panelRef.current
    target?.focus()
    return () => previous?.focus?.()
  }, [initialFocus])

  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
    } else if (e.key === 'Tab') {
      const nodes = [...panelRef.current.querySelectorAll(FOCUSABLE)]
      if (nodes.length === 0) return
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center sm:items-center sm:p-4" onKeyDown={onKeyDown}>
      <div className="fade-in absolute inset-0 bg-black/60" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`sheet-up relative flex max-h-[90dvh] w-full flex-col overflow-hidden rounded-t-2xl bg-[var(--color-panel)] shadow-2xl ring-1 ring-white/8 outline-none sm:rounded-2xl ${maxWidth}`}
      >
        <div className="flex items-center justify-between px-5 pt-4 pb-2">
          <h2 className="text-base font-semibold text-[var(--color-text)]">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
          >
            <CloseIcon size={17} />
          </button>
        </div>
        <div className="min-h-0 overflow-y-auto px-5 pb-5">{children}</div>
      </div>
    </div>
  )
}

const btn = 'h-10 rounded-lg px-4 text-sm font-medium transition-colors disabled:opacity-50'
export const ghostBtn = `${btn} text-[var(--color-text)] hover:bg-[var(--color-hover)]`
export const primaryBtn = `${btn} bg-[var(--color-accent)] text-white hover:brightness-110`
export const dangerBtn = `${btn} bg-[var(--color-danger)] text-white hover:brightness-110`

export function ConfirmDialog({ title, body, confirmLabel, danger = true, busy, error, onConfirm, onCancel }) {
  return (
    <Modal title={title} onClose={busy ? () => {} : onCancel} maxWidth="max-w-sm" initialFocus="[data-cancel]">
      <p className="mb-4 text-sm leading-relaxed text-[var(--color-text-dim)]">{body}</p>
      {error && (
        <p role="alert" className="mb-3 text-sm text-[var(--color-danger)]">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" data-cancel onClick={onCancel} disabled={busy} className={ghostBtn}>
          Cancel
        </button>
        <button type="button" onClick={onConfirm} disabled={busy} className={danger ? dangerBtn : primaryBtn}>
          {busy ? 'Please wait…' : confirmLabel}
        </button>
      </div>
    </Modal>
  )
}

export function RenameDialog({ initial, onSave, onCancel }) {
  const [value, setValue] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const trimmed = value.trim()

  const submit = async (e) => {
    e.preventDefault()
    if (!trimmed || busy) return
    if (trimmed === initial) return onCancel()
    setBusy(true)
    setError(null)
    try {
      await onSave(trimmed)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <Modal title="Rename conversation" onClose={onCancel} maxWidth="max-w-sm" initialFocus="input">
      <form onSubmit={submit}>
        <label className="mb-1.5 block text-sm text-[var(--color-text-dim)]" htmlFor="rename-input">
          Conversation title
        </label>
        <input
          id="rename-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          maxLength={80}
          onFocus={(e) => e.target.select()}
          className="mb-4 w-full rounded-lg bg-[var(--color-input)] px-3 py-2.5 text-sm text-[var(--color-text)] outline-none ring-1 ring-white/8 focus:ring-[var(--color-accent)]"
        />
        {error && (
          <p role="alert" className="mb-3 text-sm text-[var(--color-danger)]">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onCancel} className={ghostBtn}>
            Cancel
          </button>
          <button type="submit" disabled={!trimmed || busy} className={primaryBtn}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
