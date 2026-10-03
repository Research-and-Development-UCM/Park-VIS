import { reactive } from 'vue'
import { billingApi } from './billing'

const state = reactive({
  status: null,
  loading: false,
  error: '',
  lastFetched: 0,
})

const subscribers = new Set()
let pollTimer = null
const POLL_MS = 60000

async function refresh({ force = false } = {}) {
  state.loading = true
  state.error = ''
  try {
    const s = await billingApi.status()
    state.status = s
    state.error = ''
    state.lastFetched = Date.now()
    for (const cb of subscribers) {
      try { cb(s) } catch (_) {}
    }
  } catch (e) {
    // Don't clobber the last good status on a transient error — the UI
    // should keep showing the license we already know about. We do
    // surface the error string so subscribers can show a banner if
    // they want to.
    state.error = e?.response?.data?.detail || e?.message || String(e)
    if (!state.status) {
      state.status = null
    }
  } finally {
    state.loading = false
  }
}

function subscribe(cb) {
  subscribers.add(cb)
  if (state.status) {
    try { cb(state.status) } catch (_) {}
  }
  return () => subscribers.delete(cb)
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => refresh(), POLL_MS)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

export const billingStatus = {
  state,
  refresh,
  subscribe,
  startPolling,
  stopPolling,
}

export default billingStatus
