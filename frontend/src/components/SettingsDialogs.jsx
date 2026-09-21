// SettingsDialogs - global settings (this browser / account) and per-chat settings.
//
// Every control does something real. Global preferences live in this browser
// (usePrefs); chat settings are saved to the conversation on the server, and the
// worker is told to reload them. Neither can turn voice on.

import { useState } from 'react'
import { Modal, ghostBtn, dangerBtn } from './Dialogs'
import { Field, LANGUAGE_OPTIONS, LENGTH_OPTIONS, RESPONSE_OPTIONS, Segmented, Switch } from './ui'
import { DownloadIcon, TrashIcon } from './Icons'
import { BOTS } from '../lib/botMeta'

const SECTIONS = [
  ['appearance', 'Appearance'],
  ['voice', 'Voice'],
  ['language', 'Language'],
  ['ai', 'AI behavior'],
  ['notifications', 'Notifications'],
  ['account', 'Account'],
]

export function SettingsDialog({ prefs, setPref, user, onLogout, onClose }) {
  const [section, setSection] = useState('appearance')
  const [notifyNote, setNotifyNote] = useState(null)

  const toggleNotify = async (on) => {
    setNotifyNote(null)
    if (!on) return setPref('notifyReplies', false)
    if (!('Notification' in window)) return setNotifyNote('Yeh browser notifications support nahi karta.')
    const perm = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission()
    if (perm === 'granted') setPref('notifyReplies', true)
    else setNotifyNote('Browser ne permission block ki hai. Site settings mein allow karo.')
  }

  return (
    <Modal title="Settings" onClose={onClose} maxWidth="max-w-2xl">
      <div className="flex flex-col gap-4 sm:flex-row">
        <nav aria-label="Settings sections" className="flex shrink-0 gap-1 overflow-x-auto sm:w-40 sm:flex-col">
          {SECTIONS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSection(id)}
              aria-current={section === id ? 'page' : undefined}
              className={`rounded-lg px-3 py-2 text-left text-sm whitespace-nowrap transition-colors ${
                section === id ? 'bg-[var(--color-panel-raised)] text-[var(--color-text)]' : 'text-[var(--color-text-dim)] hover:text-[var(--color-text)]'
              }`}
            >
              {label}
            </button>
          ))}
        </nav>

        <div className="min-h-[15rem] min-w-0 flex-1">
          {section === 'appearance' && (
            <>
              <Field label="Text size" hint="Poore app ka text chhota ya bada karta hai.">
                <Segmented label="Text size" value={prefs.textSize} onChange={(v) => setPref('textSize', v)} options={[['sm', 'Small'], ['md', 'Medium'], ['lg', 'Large']]} />
              </Field>
              <Switch label="Reduce motion" hint="Animations band karta hai." checked={prefs.reduceMotion} onChange={(v) => setPref('reduceMotion', v)} />
            </>
          )}
          {section === 'voice' && (
            <>
              <Switch
                label="Pause mic while AI speaks"
                hint="Speakers se AI ki awaaz mic mein wapas na jaye (echo). Headphones ho to band kar sakte ho."
                checked={prefs.micAutoPause}
                onChange={(v) => setPref('micAutoPause', v)}
              />
              <p className="mt-2 rounded-lg bg-[var(--color-input)] px-3 py-2.5 text-xs leading-relaxed text-[var(--color-text-dim)]">
                Voice mode kabhi apne aap shuru nahi hota, purani conversation kholne par bhi nahi. Sirf tumhare Voice dabane par.
              </p>
            </>
          )}
          {section === 'language' && (
            <Field label="Default reply language for new chats" hint="Har chat ki apni language bhi set ho sakti hai (chat settings).">
              <Segmented label="Default language" value={prefs.defaultLanguage} onChange={(v) => setPref('defaultLanguage', v)} options={LANGUAGE_OPTIONS} />
            </Field>
          )}
          {section === 'ai' && (
            <Field label="Default reply length for new chats" hint="Short: 2-3 line ke jawab. Detailed: poori explanation.">
              <Segmented label="Default reply length" value={prefs.defaultReplyLength} onChange={(v) => setPref('defaultReplyLength', v)} options={LENGTH_OPTIONS} />
            </Field>
          )}
          {section === 'notifications' && (
            <>
              <Switch
                label="Notify me about AI replies"
                hint="Jab yeh tab background mein ho aur AI jawab de, browser notification aayega."
                checked={prefs.notifyReplies}
                onChange={toggleNotify}
              />
              {notifyNote && (
                <p role="alert" className="mt-2 text-xs text-[var(--color-warn)]">
                  {notifyNote}
                </p>
              )}
            </>
          )}
          {section === 'account' && (
            <div className="py-2">
              <p className="text-sm font-medium text-[var(--color-text)]">{user?.name}</p>
              <p className="mb-4 text-sm text-[var(--color-text-dim)]">
                {user?.email?.endsWith('@guest.local') ? 'Guest session (testing): tumhari chats is browser mein save hain.' : user?.email}
              </p>
              {onLogout && (
                <button type="button" onClick={onLogout} className={`${ghostBtn} ring-1 ring-white/12`}>
                  Log out
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}

export function ChatSettingsDialog({ conversation, onPatch, onRequestClear, onExport, onClose }) {
  const s = conversation.settings
  const [error, setError] = useState(null)
  const save = async (patch) => {
    setError(null)
    try {
      await onPatch(patch)
    } catch (err) {
      setError(err.message)
    }
  }
  const toggleParticipant = (id) => {
    const has = conversation.participants.includes(id)
    const next = has ? conversation.participants.filter((p) => p !== id) : [...conversation.participants, id]
    if (next.length === 0) return setError('Kam se kam ek AI chunna zaroori hai.')
    save({ participants: next })
  }

  return (
    <Modal title="Chat settings" onClose={onClose} maxWidth="max-w-lg">
      <p className="mb-1 truncate text-xs text-[var(--color-text-dim)]">{conversation.title}</p>
      {error && (
        <p role="alert" className="my-2 rounded-lg bg-[var(--color-danger)]/10 px-3 py-2 text-xs text-[var(--color-danger)]">
          {error}
        </p>
      )}

      <Field label="AI participants" hint="Sirf chune hue AI is chat mein jawab denge.">
        <div className="flex gap-2">
          {Object.entries(BOTS).map(([id, b]) => {
            const on = conversation.participants.includes(id)
            return (
              <button
                key={id}
                type="button"
                role="checkbox"
                aria-checked={on}
                onClick={() => toggleParticipant(id)}
                className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ring-1 transition-colors ${
                  on ? 'text-[var(--color-text)] ring-white/25' : 'text-[var(--color-text-dim)] ring-white/8'
                }`}
                style={on ? { background: b.soft } : undefined}
              >
                <span className="h-2 w-2 rounded-full" style={{ background: on ? b.color : 'transparent', boxShadow: `inset 0 0 0 1.5px ${b.color}` }} />
                {b.label}
              </button>
            )
          })}
        </div>
      </Field>

      <Switch
        label="AI Collaboration"
        hint="AI aapas mein bhi baat kar sakte hain (max 6 turns). Dono AI chune hone chahiye."
        checked={s.ai_collab}
        disabled={conversation.participants.length < 2}
        onChange={(v) => save({ settings: { ai_collab: v } })}
      />
      <Switch
        label="Allow voice mode"
        hint="Band karne par is chat mein Voice button disabled ho jata hai. Voice tab bhi tumhare dabane par hi shuru hoga."
        checked={s.voice_enabled}
        onChange={(v) => save({ settings: { voice_enabled: v } })}
      />
      <Field
        label="How the AI replies"
        hint={'Auto: text, plus voice only when you press Voice or ask ("voice mein batao"). Text only: never speaks. Text + voice: speaks every reply. Opening a chat never starts speech.'}
      >
        <Segmented label="Reply mode" value={s.response_pref} onChange={(v) => save({ settings: { response_pref: v } })} options={RESPONSE_OPTIONS} />
      </Field>
      <Field label="Reply language">
        <Segmented label="Reply language" value={s.language} onChange={(v) => save({ settings: { language: v } })} options={LANGUAGE_OPTIONS} />
      </Field>
      <Field label="Reply length">
        <Segmented label="Reply length" value={s.reply_length} onChange={(v) => save({ settings: { reply_length: v } })} options={LENGTH_OPTIONS} />
      </Field>

      <Field label="Export conversation">
        <div className="flex flex-wrap gap-2">
          {[['txt', 'TXT'], ['md', 'Markdown'], ['json', 'JSON']].map(([fmt, label]) => (
            <button key={fmt} type="button" onClick={() => onExport(fmt)} className={`${ghostBtn} flex items-center gap-2 ring-1 ring-white/12`}>
              <DownloadIcon size={15} />
              {label}
            </button>
          ))}
        </div>
      </Field>

      <div className="mt-2 border-t border-white/8 pt-4">
        <button type="button" onClick={onRequestClear} className={`${dangerBtn} flex items-center gap-2`}>
          <TrashIcon size={15} />
          Clear conversation
        </button>
        <p className="mt-1.5 text-xs text-[var(--color-text-dim)]">Messages hat jayenge; title aur settings rahengi.</p>
      </div>
    </Modal>
  )
}
