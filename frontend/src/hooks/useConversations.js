// useConversations - the sidebar's data: recent chats, archived chats and search.
//
// The backend is the source of truth. The list holds only summaries (id,
// title, preview, timestamps, pinned/archived, participants) and is paged, so
// a large history is never loaded up front. Mutations update the list
// optimistically and roll back (by re-fetching) if the server refuses.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createConversationsApi } from '../services/conversations'

const PAGE = 30

const byPinnedThenRecent = (a, b) => Number(b.pinned) - Number(a.pinned) || b.updated_at - a.updated_at

function applyLocal(item, patch) {
  const next = { ...item }
  if ('title' in patch) {
    next.title = String(patch.title).trim() || item.title
    next.title_auto = false
  }
  if ('pinned' in patch) next.pinned = !!patch.pinned
  if ('archived' in patch) next.archived = !!patch.archived
  if ('participants' in patch) next.participants = patch.participants
  if ('settings' in patch) next.settings = { ...item.settings, ...patch.settings }
  return next
}

export function useConversationsApi({ tokenEndpoint, authToken }) {
  return useMemo(() => createConversationsApi({ tokenEndpoint, authToken }), [tokenEndpoint, authToken])
}

export function useConversations(api) {
  const [state, setState] = useState({ items: [], hasMore: false, total: 0, status: 'loading', error: null, loadingMore: false })
  const reqId = useRef(0)
  const countRef = useRef(0)
  useEffect(() => {
    countRef.current = state.items.length
  }, [state.items.length])

  const refresh = useCallback(
    async ({ silent = false } = {}) => {
      const id = ++reqId.current
      if (!silent) setState((s) => ({ ...s, status: s.items.length ? s.status : 'loading', error: null }))
      try {
        // Reload as many rows as are already shown, so a refresh never collapses the list.
        const r = await api.list({ limit: Math.min(100, Math.max(PAGE, countRef.current)) })
        if (id !== reqId.current) return // a newer request superseded this one
        setState({ items: r.conversations, hasMore: r.has_more, total: r.total, status: 'ready', error: null, loadingMore: false })
      } catch (err) {
        if (id !== reqId.current) return
        setState((s) => ({ ...s, status: s.items.length ? 'ready' : 'error', error: err.message, loadingMore: false }))
      }
    },
    [api],
  )

  useEffect(() => {
    refresh()
  }, [refresh])

  const loadMore = useCallback(async () => {
    if (state.loadingMore || !state.hasMore) return
    setState((s) => ({ ...s, loadingMore: true }))
    try {
      const r = await api.list({ limit: PAGE, offset: countRef.current })
      setState((s) => {
        const seen = new Set(s.items.map((c) => c.id))
        return { ...s, items: [...s.items, ...r.conversations.filter((c) => !seen.has(c.id))], hasMore: r.has_more, total: r.total, loadingMore: false }
      })
    } catch (err) {
      setState((s) => ({ ...s, loadingMore: false, error: err.message }))
    }
  }, [api, state.loadingMore, state.hasMore])

  const create = useCallback(
    async (payload) => {
      const conv = await api.create(payload)
      setState((s) => ({ ...s, items: [conv, ...s.items].sort(byPinnedThenRecent), total: s.total + 1 }))
      return conv
    },
    [api],
  )

  const patch = useCallback(
    async (id, changes) => {
      setState((s) => ({
        ...s,
        items: s.items
          .map((c) => (c.id === id ? applyLocal(c, changes) : c))
          .filter((c) => !c.archived)
          .sort(byPinnedThenRecent),
      }))
      try {
        const saved = await api.patch(id, changes)
        if (!saved.archived) {
          setState((s) => ({
            ...s,
            items: s.items.some((c) => c.id === id)
              ? s.items.map((c) => (c.id === id ? saved : c)).sort(byPinnedThenRecent)
              : [...s.items, saved].sort(byPinnedThenRecent),
          }))
        }
        return saved
      } catch (err) {
        refresh({ silent: true })
        throw err
      }
    },
    [api, refresh],
  )

  const remove = useCallback(
    async (id) => {
      setState((s) => ({ ...s, items: s.items.filter((c) => c.id !== id), total: Math.max(0, s.total - 1) }))
      try {
        await api.remove(id)
      } catch (err) {
        refresh({ silent: true })
        throw err
      }
    },
    [api, refresh],
  )

  return { ...state, refresh, loadMore, create, patch, remove }
}

export function useArchived(api, { enabled }) {
  const [state, setState] = useState({ items: [], status: 'idle', error: null })

  const load = useCallback(async () => {
    setState((s) => ({ ...s, status: s.items.length ? 'ready' : 'loading', error: null }))
    try {
      const r = await api.list({ archived: true, limit: 100 })
      setState({ items: r.conversations, status: 'ready', error: null })
    } catch (err) {
      setState((s) => ({ ...s, status: 'error', error: err.message }))
    }
  }, [api])

  useEffect(() => {
    if (enabled) load()
  }, [enabled, load])

  const restore = useCallback(
    async (id) => {
      setState((s) => ({ ...s, items: s.items.filter((c) => c.id !== id) }))
      try {
        await api.patch(id, { archived: false })
      } catch (err) {
        load()
        throw err
      }
    },
    [api, load],
  )

  const remove = useCallback(
    async (id) => {
      setState((s) => ({ ...s, items: s.items.filter((c) => c.id !== id) }))
      try {
        await api.remove(id)
      } catch (err) {
        load()
        throw err
      }
    },
    [api, load],
  )

  return { ...state, reload: load, restore, remove }
}

/** Debounced backend search over titles and messages. */
export function useConversationSearch(api, query) {
  const q = query.trim()
  const [state, setState] = useState({ status: 'idle', results: [], error: null })
  const reqId = useRef(0)

  useEffect(() => {
    const id = ++reqId.current
    if (!q) {
      setState({ status: 'idle', results: [], error: null })
      return undefined
    }
    setState((s) => ({ ...s, status: 'loading', error: null }))
    const timer = setTimeout(async () => {
      try {
        const r = await api.search(q)
        if (id === reqId.current) setState({ status: 'ready', results: r.results, error: null })
      } catch (err) {
        if (id === reqId.current) setState({ status: 'error', results: [], error: err.message })
      }
    }, 250)
    return () => clearTimeout(timer)
  }, [api, q])

  return state
}
