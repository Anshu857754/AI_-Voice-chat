// conversations - REST client for saved chats (the backend is the source of truth).
//
// Every call carries the user's bearer token; errors are thrown as
// `ApiError` with a user-safe message (from the server's `detail`).

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

export function createConversationsApi({ tokenEndpoint, authToken }) {
  async function call(path, { method = 'GET', body, raw = false } = {}) {
    let res
    try {
      res = await fetch(`${tokenEndpoint}${path}`, {
        method,
        headers: {
          ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
          ...(body ? { 'Content-Type': 'application/json' } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      })
    } catch {
      throw new ApiError('Server se connect nahi ho paya.', 0)
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new ApiError(typeof data.detail === 'string' ? data.detail : 'Kuch gadbad ho gayi.', res.status)
    }
    if (raw) return res
    return res.status === 204 ? null : res.json()
  }

  const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null)).toString()

  return {
    list: ({ archived = false, limit = 30, offset = 0 } = {}) => call(`/conversations?${qs({ archived, limit, offset })}`),
    create: (payload = { kind: 'text' }) => call('/conversations', { method: 'POST', body: payload }),
    get: (id) => call(`/conversations/${id}`),
    patch: (id, patch) => call(`/conversations/${id}`, { method: 'PATCH', body: patch }),
    remove: (id) => call(`/conversations/${id}`, { method: 'DELETE' }),
    clear: (id) => call(`/conversations/${id}/clear`, { method: 'POST' }),
    messages: (id, { limit = 50, before } = {}) => call(`/conversations/${id}/messages?${qs({ limit, before })}`),
    search: (q) => call(`/conversations/search?${qs({ q })}`),
    async exportFile(id, format) {
      const res = await call(`/conversations/${id}/export?${qs({ format })}`, { raw: true })
      const disposition = res.headers.get('content-disposition') || ''
      const name = /filename="([^"]+)"/.exec(disposition)?.[1] || `conversation.${format}`
      return { blob: await res.blob(), name }
    },
  }
}

/** Save a Blob as a file download. */
export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
