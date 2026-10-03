import axios from 'axios'

const BASE = '/api/billing'

export const billingApi = {
  status() {
    return axios.get(`${BASE}/status`).then(r => r.data)
  },

  // Build the activation URL. Pass the browser's current origin so the
  // callback (return_to) lands on the same host:port the user is
  // actually browsing — not the API server's address (which may be
  // behind a Vite dev proxy or a reverse tunnel).
  connectUrl() {
    const returnTo = window.location.origin
    return axios.post(`${BASE}/connect-url`, { return_to: returnTo }).then(r => r.data)
  },

  disconnectUrl() {
    const returnTo = window.location.origin
    return axios.post(`${BASE}/disconnect-url`, { return_to: returnTo }).then(r => r.data)
  },

  activateCallback(licenseToken, trialExpiresAt) {
    return axios
      .post(`${BASE}/activate-callback`, {
        license_token: licenseToken,
        trial_expires_at: trialExpiresAt || null,
      })
      .then(r => r.data)
  },

  triggerHeartbeat() {
    return axios.post(`${BASE}/heartbeat/trigger`, {}).then(r => r.data)
  },

  logout() {
    return axios.post(`${BASE}/logout`, {}).then(r => r.data)
  },
}

export default billingApi