import axios from 'axios'

const BASE = '/api/admin/feedback'

export const feedbackApi = {
  /**
   * Number of feedback submissions waiting to be uploaded to S3.
   *
   * Surfaced as a warning chip in HistoryView when > ``threshold``
   * — usually means the cloud billing portal is unreachable, S3
   * credentials are wrong, or the user is being rate-limited.
   */
  pending() {
    return axios.get(`${BASE}/pending`).then(r => r.data)
  },
}

export default feedbackApi