// usePrefs - per-browser preferences (localStorage). Every one has a real effect:
//
//   sidebarCollapsed  desktop sidebar shows icons only
//   textSize          sm | md | lg   (root font size)
//   reduceMotion      switches animations off
//   micAutoPause      mute the mic while an AI speaks (echo guard)
//   notifyReplies     browser notification for an AI reply while the tab is hidden
//   defaultLanguage / defaultReplyLength   used as the settings of NEW chats
//
// Nothing here can enable voice: voice mode is never a preference.

import { useCallback, useEffect, useState } from 'react'

const KEY = 'roxstar.prefs'
export const DEFAULT_PREFS = {
  sidebarCollapsed: false,
  textSize: 'md',
  reduceMotion: false,
  micAutoPause: true,
  notifyReplies: false,
  defaultLanguage: 'auto',
  defaultReplyLength: 'short',
}
const ROOT_SIZE = { sm: '14px', md: '16px', lg: '18px' }

function load() {
  try {
    return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(KEY) || '{}') }
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

export function usePrefs() {
  const [prefs, setPrefs] = useState(load)

  useEffect(() => {
    document.documentElement.style.fontSize = ROOT_SIZE[prefs.textSize] || ROOT_SIZE.md
    document.documentElement.toggleAttribute('data-reduce-motion', !!prefs.reduceMotion)
    try {
      localStorage.setItem(KEY, JSON.stringify(prefs))
    } catch {
      // Storage blocked: preferences just last for this session.
    }
  }, [prefs])

  const setPref = useCallback((key, value) => setPrefs((p) => ({ ...p, [key]: value })), [])
  return [prefs, setPref]
}
