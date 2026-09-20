// ui - small form primitives shared by the settings dialogs.

export function Switch({ checked, onChange, label, hint, disabled }) {
  return (
    <label className={`flex items-start justify-between gap-4 py-2.5 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
      <span className="min-w-0">
        <span className="block text-sm text-[var(--color-text)]">{label}</span>
        {hint && <span className="mt-0.5 block text-xs leading-relaxed text-[var(--color-text-dim)]">{hint}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-6 w-10 shrink-0 rounded-full transition-colors ${checked ? 'bg-[var(--color-accent)]' : 'bg-white/15'}`}
      >
        <span className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-4' : ''}`} />
      </button>
    </label>
  )
}

export function Segmented({ value, onChange, options, label }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg bg-[var(--color-input)] p-0.5">
      {options.map(([val, text]) => (
        <button
          key={val}
          type="button"
          role="radio"
          aria-checked={value === val}
          onClick={() => onChange(val)}
          className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
            value === val ? 'bg-[var(--color-panel-raised)] text-[var(--color-text)] shadow' : 'text-[var(--color-text-dim)] hover:text-[var(--color-text)]'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, hint, children }) {
  return (
    <div className="py-2.5">
      <p className="mb-1.5 text-sm text-[var(--color-text)]">{label}</p>
      {children}
      {hint && <p className="mt-1.5 text-xs leading-relaxed text-[var(--color-text-dim)]">{hint}</p>}
    </div>
  )
}

export const LANGUAGE_OPTIONS = [
  ['auto', 'Auto'],
  ['hinglish', 'Hinglish'],
  ['english', 'English'],
  ['hindi', 'हिन्दी'],
]
export const RESPONSE_OPTIONS = [
  ['auto', 'Auto'],
  ['text', 'Text only'],
  ['voice', 'Text + voice'],
]
export const LENGTH_OPTIONS = [
  ['short', 'Short'],
  ['detailed', 'Detailed'],
]
