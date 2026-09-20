// Menu - popover menu (trigger button + list of actions).
//
// The popover is `position: fixed`, placed from the trigger's rectangle, so it
// is never clipped by a scrolling sidebar. Closes on outside click, Esc,
// scroll, resize and after choosing an item; arrow keys move between items and
// the first item is focused on open.

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CheckIcon } from './Icons'

export default function Menu({ label, icon, items, align = 'right', triggerClassName, onOpenChange, width = 256 }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState(null)
  const rootRef = useRef(null)
  const btnRef = useRef(null)
  const popRef = useRef(null)

  const setOpenState = (v) => {
    setOpen(v)
    onOpenChange?.(v)
  }

  useLayoutEffect(() => {
    if (!open || !btnRef.current) return
    const r = btnRef.current.getBoundingClientRect()
    const height = popRef.current?.offsetHeight || 240
    const below = window.innerHeight - r.bottom
    const top = below >= height + 12 || r.top < height + 12 ? r.bottom + 6 : r.top - height - 6
    const left = align === 'right' ? Math.max(8, r.right - width) : Math.min(r.left, window.innerWidth - width - 8)
    setPos({ top: Math.max(8, Math.min(top, window.innerHeight - height - 8)), left })
  }, [open, align, width, items.length])

  useEffect(() => {
    if (!open) return undefined
    popRef.current?.querySelector('[role=menuitem]:not(:disabled)')?.focus({ preventScroll: true })
    const close = () => {
      setOpen(false)
      onOpenChange?.(false)
    }
    const onDown = (e) => !rootRef.current?.contains(e.target) && !popRef.current?.contains(e.target) && close()
    const onKey = (e) => {
      if (e.key === 'Escape') {
        close()
        btnRef.current?.focus()
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const nodes = [...popRef.current.querySelectorAll('[role=menuitem]:not(:disabled)')]
        const i = nodes.indexOf(document.activeElement)
        const next = e.key === 'ArrowDown' ? (i + 1) % nodes.length : (i - 1 + nodes.length) % nodes.length
        nodes[next]?.focus({ preventScroll: true })
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', close)
    // Close only when something that CONTAINS the trigger scrolls (its list moved under it);
    // an unrelated scroll (e.g. the chat auto-scrolling) must not dismiss the menu.
    const onScroll = (e) => {
      const t = e.target
      if (t === document || t === window || (t instanceof Node && t.contains(btnRef.current))) close()
    }
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open, onOpenChange])

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={btnRef}
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpenState(!open)
        }}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        className={
          triggerClassName ||
          'flex h-9 w-9 items-center justify-center rounded-lg text-[var(--color-text-dim)] transition-colors hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
        }
      >
        {icon}
      </button>
      {open &&
        createPortal(
        <div
          ref={popRef}
          role="menu"
          aria-label={label}
          style={{ position: 'fixed', top: pos?.top ?? -9999, left: pos?.left ?? -9999, width }}
          className="fade-in z-[60] rounded-xl bg-[var(--color-panel-raised)] p-1.5 shadow-xl ring-1 ring-white/8"
        >
          {items.map((item) =>
            item.separator ? (
              <div key={item.key} className="my-1 h-px bg-white/8" role="separator" />
            ) : (
              <button
                key={item.key}
                type="button"
                role="menuitem"
                disabled={item.disabled}
                onClick={(e) => {
                  e.stopPropagation()
                  setOpenState(false)
                  item.onSelect()
                }}
                className={`flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors hover:bg-[var(--color-hover)] focus:bg-[var(--color-hover)] disabled:opacity-40 disabled:hover:bg-transparent ${
                  item.danger ? 'text-[var(--color-danger)]' : 'text-[var(--color-text)]'
                }`}
              >
                <span className="flex min-w-0 items-center gap-2.5">
                  {item.icon && <span className="shrink-0 opacity-80">{item.icon}</span>}
                  <span className="min-w-0">
                    <span className="block truncate">{item.label}</span>
                    {item.hint && <span className="block truncate text-xs text-[var(--color-text-dim)]">{item.hint}</span>}
                  </span>
                </span>
                {item.checked && <CheckIcon size={16} className="shrink-0 text-[var(--color-accent)]" />}
              </button>
            ),
          )}
        </div>,
          document.body,
        )}
    </div>
  )
}
