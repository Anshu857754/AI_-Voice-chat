// useAuth - login / signup / logout against the backend account API.
//
// The session token is kept in localStorage so a refresh keeps you signed in;
// it is validated against /auth/me on load and dropped if it has expired.

import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'roxstar.session'

function loadSession() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
  } catch {
    return null
  }
}

function saveSession(session) {
  try {
    if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Storage blocked (private mode): the session just will not survive a refresh.
  }
}

async function post(base, path, body) {
  let res
  try {
    res = await fetch(`${base}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new Error('Server se connect nahi ho paya. Backend chal raha hai?')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? 'Details sahi se bharo.' : data.detail
    throw new Error(detail || `Request fail ho gayi (${res.status})`)
  }
  return data
}

export function useAuth(apiBase) {
  const [session, setSession] = useState(loadSession)
  const [checking, setChecking] = useState(() => !!loadSession())

  const logout = useCallback(() => {
    saveSession(null)
    setSession(null)
  }, [])

  // Validate a stored session once on load.
  useEffect(() => {
    const stored = loadSession()
    if (!stored) return undefined
    let cancelled = false
    fetch(`${apiBase}/auth/me`, { headers: { Authorization: `Bearer ${stored.token}` } })
      .then((res) => {
        if (!cancelled && res.status === 401) logout()
      })
      .catch(() => {})
      .finally(() => !cancelled && setChecking(false))
    return () => {
      cancelled = true
    }
  }, [apiBase, logout])

  const authenticate = useCallback(
    async (mode, fields) => {
      const data = await post(apiBase, `/auth/${mode}`, fields)
      const next = { token: data.token, user: data.user }
      saveSession(next)
      setSession(next)
    },
    [apiBase],
  )

  return {
    user: session?.user ?? null,
    token: session?.token ?? null,
    checking,
    login: (fields) => authenticate('login', fields),
    signup: (fields) => authenticate('signup', fields),
    logout,
  }
}
