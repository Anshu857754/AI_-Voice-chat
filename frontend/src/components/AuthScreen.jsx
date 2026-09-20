// AuthScreen - login / signup, styled like ChatGPT's account pages.

import { useState } from 'react'

const inputClass =
  'w-full rounded-full border border-[var(--color-border)] bg-transparent px-5 py-3 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-dim)] focus:border-[var(--color-accent)]'

export default function AuthScreen({ onLogin, onSignup }) {
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const isSignup = mode === 'signup'
  const canSubmit = email.trim() && password && (!isSignup || name.trim()) && !busy

  const submit = async (e) => {
    e.preventDefault()
    if (!canSubmit) return
    setBusy(true)
    setError(null)
    try {
      if (isSignup) await onSignup({ name: name.trim(), email: email.trim(), password })
      else await onLogin({ email: email.trim(), password })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const switchMode = () => {
    setMode(isSignup ? 'login' : 'signup')
    setError(null)
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-[var(--color-bg)] px-4">
      <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-accent)] text-2xl">
        {'\u{1F399}️'}
      </div>
      <h1 className="mb-1 text-3xl font-semibold text-[var(--color-text)]">
        {isSignup ? 'Create your account' : 'Welcome back'}
      </h1>
      <p className="mb-7 text-sm text-[var(--color-text-dim)]">
        Roxstar AI Voice Room · AI Dost &amp; AI Sathi se baat karo
      </p>

      <form onSubmit={submit} className="w-full max-w-sm space-y-3">
        {isSignup && (
          <input
            className={inputClass}
            placeholder="Your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="name"
            maxLength={40}
            autoFocus
          />
        )}
        <input
          className={inputClass}
          type="email"
          placeholder="Email address"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          autoFocus={!isSignup}
        />
        <input
          className={inputClass}
          type="password"
          placeholder={isSignup ? 'Password (min 8 characters)' : 'Password'}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={isSignup ? 'new-password' : 'current-password'}
        />

        {error && (
          <p role="alert" className="rounded-xl bg-[var(--color-danger)]/10 px-4 py-2.5 text-xs text-[var(--color-danger)]">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          className="w-full rounded-full bg-[var(--color-accent)] py-3 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {busy ? 'Please wait…' : isSignup ? 'Sign up' : 'Log in'}
        </button>
      </form>

      <p className="mt-5 text-sm text-[var(--color-text-dim)]">
        {isSignup ? 'Already have an account?' : 'Need an account?'}{' '}
        <button type="button" onClick={switchMode} className="text-[var(--color-accent)] hover:underline">
          {isSignup ? 'Log in' : 'Sign up'}
        </button>
      </p>
    </div>
  )
}
