import { useCallback, useEffect, useMemo, useRef } from 'react'
import AuthScreen from './components/AuthScreen'
import AppShell from './components/AppShell'
import { useAuth } from './hooks/useAuth'
import { useRoom } from './hooks/useRoom'

const TOKEN_ENDPOINT = import.meta.env.VITE_TOKEN_ENDPOINT || 'http://localhost:8000'
const DEFAULT_ROOM = import.meta.env.VITE_DEFAULT_ROOM || ''
// While testing there is no login page: every visitor silently gets a private guest account.
// Build with VITE_REQUIRE_LOGIN=true to bring the login / signup page back.
const GUEST_MODE = import.meta.env.VITE_REQUIRE_LOGIN !== 'true'

export default function App() {
  const { user, token, checking, error: authError, retry, guest, login, signup, logout } = useAuth(TOKEN_ENDPOINT, { guestMode: GUEST_MODE })
  const { room, connectionState, error, micEnabled, micError, join, leave, toggleMic } = useRoom({
    tokenEndpoint: TOKEN_ENDPOINT,
    authToken: token,
    onAuthError: logout,
  })

  // Connect once after login and stay connected while the user moves between
  // conversations. The microphone is NOT touched here: it is only ever asked for
  // when the user starts voice mode.
  const attempted = useRef(false)
  const connect = useCallback(() => {
    attempted.current = true
    join(user?.name, DEFAULT_ROOM || undefined).catch(() => {
      // the error is surfaced through `error`
    })
  }, [join, user])

  useEffect(() => {
    if (room) attempted.current = false // a later drop may reconnect once
    else if (user && token && !attempted.current) connect()
  }, [room, user, token, connect])

  const handleLogout = useCallback(async () => {
    await leave()
    attempted.current = false
    window.history.replaceState({}, '', '/')
    logout()
  }, [leave, logout])

  const rtc = useMemo(
    () => ({ room, connectionState, error, micEnabled, micError, toggleMic, reconnect: connect }),
    [room, connectionState, error, micEnabled, micError, toggleMic, connect],
  )

  if (checking) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-[var(--color-text-dim)]">Loading…</div>
  }

  if (!user && GUEST_MODE) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 px-6 text-center text-sm text-[var(--color-text-dim)]">
        <p role={authError ? 'alert' : 'status'}>{authError || 'Guest session bana rahe hain…'}</p>
        {authError && (
          <button type="button" onClick={retry} className="rounded-xl bg-[var(--color-accent)] px-4 py-2 font-medium text-white">
            Dobara try karo
          </button>
        )}
      </div>
    )
  }
  if (!user) return <AuthScreen onLogin={login} onSignup={signup} />

  return <AppShell user={user} token={token} tokenEndpoint={TOKEN_ENDPOINT} onLogout={guest ? null : handleLogout} rtc={rtc} />
}
