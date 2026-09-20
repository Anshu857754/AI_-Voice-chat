// useRoute - a tiny History-API router (no dependency).
//
//   /                 -> { name: 'home' }
//   /chat/:id         -> { name: 'chat', id }
//   /archived         -> { name: 'archived' }
//
// `navigate` pushes (or replaces) a history entry, so refresh, back and
// forward all restore the right screen.

import { useCallback, useEffect, useState } from 'react'

export function parseRoute(pathname) {
  const chat = /^\/chat\/([A-Za-z0-9_-]{1,64})\/?$/.exec(pathname)
  if (chat) return { name: 'chat', id: chat[1] }
  if (/^\/archived\/?$/.test(pathname)) return { name: 'archived' }
  return { name: 'home' }
}

export const chatPath = (id) => `/chat/${id}`

const NAV_EVENT = 'roxstar:navigate'

export function navigate(path, { replace = false } = {}) {
  if (path === window.location.pathname) return
  window.history[replace ? 'replaceState' : 'pushState']({}, '', path)
  window.dispatchEvent(new Event(NAV_EVENT))
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseRoute(window.location.pathname))
  useEffect(() => {
    const sync = () => setRoute(parseRoute(window.location.pathname))
    window.addEventListener('popstate', sync)
    window.addEventListener(NAV_EVENT, sync)
    return () => {
      window.removeEventListener('popstate', sync)
      window.removeEventListener(NAV_EVENT, sync)
    }
  }, [])
  const go = useCallback((path, opts) => navigate(path, opts), [])
  return [route, go]
}
