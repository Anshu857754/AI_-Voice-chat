// Date helpers for the conversation list: real timestamps, local time zone.

const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
const DAY = 86400000

/** Which sidebar bucket an `updated_at` (epoch seconds) belongs to. */
export function bucketOf(epochSeconds, now = new Date()) {
  const today = startOfDay(now)
  const t = epochSeconds * 1000
  if (t >= today) return 'today'
  if (t >= today - DAY) return 'yesterday'
  if (t >= today - 7 * DAY) return 'week'
  return 'earlier'
}

export const BUCKET_LABELS = { today: 'Today', yesterday: 'Yesterday', week: 'Previous 7 days', earlier: 'Earlier' }
const ORDER = ['today', 'yesterday', 'week', 'earlier']

/** Split conversations into pinned + dated groups, keeping the incoming (server) order. */
export function groupConversations(items, now = new Date()) {
  const pinned = items.filter((c) => c.pinned)
  const groups = ORDER.map((key) => ({ key, label: BUCKET_LABELS[key], items: [] }))
  for (const c of items) {
    if (c.pinned) continue
    groups[ORDER.indexOf(bucketOf(c.updated_at, now))].items.push(c)
  }
  return { pinned, groups: groups.filter((g) => g.items.length > 0) }
}

/** "11:42 PM" today, "Yesterday", weekday within a week, else "12 Sep". */
export function shortTime(epochSeconds, now = new Date()) {
  const d = new Date(epochSeconds * 1000)
  switch (bucketOf(epochSeconds, now)) {
    case 'today':
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    case 'yesterday':
      return 'Yesterday'
    case 'week':
      return d.toLocaleDateString([], { weekday: 'short' })
    default:
      return d.toLocaleDateString([], { day: 'numeric', month: 'short' })
  }
}
