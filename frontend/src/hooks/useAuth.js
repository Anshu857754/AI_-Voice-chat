// useAuth - login / signup / logout against the backend account API.
//
// The session token is kept in localStorage so a refresh keeps you signed in;
// it is validated against /auth/me on load and dropped if it has expired.
//
// Guest mode (default while testing): there is no login page. The first visit silently creates a
// private guest account (random credentials kept in this browser), so every visitor still gets
// their own chats. Set VITE_REQUIRE_LOGIN=true to bring the login / signup page back.

import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'roxstar.session'
const GUEST_KEY = 'roxstar.guest'
export const GUEST_DOMAIN = '@guest.local'

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

function loadGuest() {
  try {
    return JSON.parse(localStorage.getItem(GUEST_KEY) || 'null')
  } catch {
    return null
  }
}

function randomToken(bytes) {
  const a = new Uint8Array(bytes)
  crypto.getRandomValues(a)
  return [...a].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** Sign in with this browser's guest credentials, creating the guest account the first time. */
async function guestSignIn(apiBase) {
  const saved = loadGuest()
  if (saved) {
    try {
      return await post(apiBase, '/auth/login', { email: saved.email, password: saved.password })
    } catch (err) {
      if (err.message.includes('connect nahi')) throw err // backend down: do not burn a new account
      // account is gone (e.g. the server's database was reset): fall through and create a new one
    }
  }
  const guest = {
    name: `Guest-${Math.floor(1000 + Math.random() * 9000)}`,
    email: `guest-${randomToken(8)}${GUEST_DOMAIN}`,
    password: randomToken(16),
  }
  const data = await post(apiBase, '/auth/signup', guest)
  try {
    localStorage.setItem(GUEST_KEY, JSON.stringify(guest))
  } catch {
    // storage blocked: this guest simply will not survive a reload
  }
  return data
}

export function useAuth(apiBase, { guestMode = false } = {}) {
  const [session, setSession] = useState(loadSession)
  const [checking, setChecking] = useState(() => guestMode || !!loadSession())
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)

  const logout = useCallback(() => {
    saveSession(null)
    setSession(null)
  }, [])

  // Validate a stored session once on load (guest mode: also create/restore the guest account).
  useEffect(() => {
    const stored = loadSession()
    if (!stored && !guestMode) return undefined
    let cancelled = false
    const finish = () => !cancelled && setChecking(false)
    const asGuest = async () => {
      try {
        const data = await guestSignIn(apiBase)
        if (cancelled) return
        const next = { token: data.token, user: data.user }
        saveSession(next)
        setSession(next)
        setError(null)
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
    }
    const run = async () => {
      if (stored) {
        try {
          const res = await fetch(`${apiBase}/auth/me`, { headers: { Authorization: `Bearer ${stored.token}` } })
          if (res.ok) return
          if (res.status === 401) {
            logout()
            if (guestMode) await asGuest()
            return
          }
        } catch {
          return // offline / backend asleep: keep the stored session and let the room report it
        }
      } else if (guestMode) {
        await asGuest()
      }
    }
    run().finally(finish)
    return () => {
      cancelled = true
    }
  }, [apiBase, logout, guestMode, attempt])

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
    error,
    retry: () => {
      setError(null)
      setChecking(true)
      setAttempt((n) => n + 1)
    },
    guest: guestMode,
    login: (fields) => authenticate('login', fields),
    signup: (fields) => authenticate('signup', fields),
    logout,
  }
}
