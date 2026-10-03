import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import vuetify from './plugins/vuetify'
import VueKonva from 'vue-konva'
import axios from 'axios'
import './styles/retro.css'

// Set up axios interceptor to include token on every request
axios.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
    console.log(`[AXIOS] Added token to ${config.url}`)
  } else {
    console.log(`[AXIOS] No token found for ${config.url}`)
  }
  return config
}, error => {
  return Promise.reject(error)
})

// Set up response interceptor to handle expired tokens
axios.interceptors.response.use(response => {
  return response
}, error => {
  // ONLY redirect on 401 (Expired/Invalid Token)
  // Do NOT redirect on 403 (Valid token, but missing permission for this specific action)
  if (error.response && error.response.status === 401) {
    const path = window.location.pathname;
    // Public routes (/, /setup) must never trigger a redirect — a stale
    // token in localStorage from a previous JWT secret should not bounce
    // the user off the setup wizard they're intentionally viewing. This
    // also prevents the /setup -> / -> /setup redirect loop when a
    // container restart invalidates all tokens but no admin exists yet.
    const isPublic = path === '/' || path === '/setup';
    console.warn('[AXIOS] Session expired or invalid. Clearing local state.')
    localStorage.removeItem('token')
    localStorage.removeItem('permissions')
    localStorage.removeItem('username')
    localStorage.removeItem('is_admin')
    delete axios.defaults.headers.common['Authorization']
    if (!isPublic) {
      // Use router.push, not window.location.href, to avoid a full
      // page reload — a reload re-runs every onMounted (including
      // Login.vue's setup-status probe), which combined with the
      // 401-driven redirect creates an infinite loop on /setup.
      router.push('/').catch(() => { /* ignore navigation duplication */ });
    }
  }
  return Promise.reject(error)
})

createApp(App).use(router).use(vuetify).use(VueKonva).mount('#app')
