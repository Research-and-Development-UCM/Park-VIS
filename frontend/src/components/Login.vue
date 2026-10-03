<template>
  <v-container class="fill-height pv-login" fluid>
    <v-row align="center" justify="center">
      <v-col cols="12" sm="8" md="4">
        <v-card elevation="4" class="pv-login-card">
          <v-card-title class="text-center py-6">
            <div class="w-100"><span class="pv-login-tag">LOCAL PARKING OBSERVATORY</span><div class="pv-login-brand">Parking Vis</div><div class="pv-login-caption">A CLEARER VIEW OF EVERY SPACE.</div></div>
          </v-card-title>
          <v-card-text>
            <v-alert
              v-if="error"
              type="error"
              variant="tonal"
              class="mb-4"
              closable
              icon="mdi-alert-circle"
              density="compact"
              border="start"
              @click:close="error = ''"
            >
              {{ error }}
            </v-alert>
            <v-form @submit.prevent="login">
              <v-text-field
                v-model="username"
                label="Username"
                prepend-inner-icon="mdi-account"
                variant="outlined"
                required
                :disabled="loading"
              ></v-text-field>
              <v-text-field
                v-model="password"
                label="Password"
                type="password"
                prepend-inner-icon="mdi-lock"
                variant="outlined"
                required
                :disabled="loading"
              ></v-text-field>
              <v-btn type="submit" color="primary" block size="large" class="mt-2" :loading="loading">Enter control room →</v-btn>
            </v-form>
            <div class="pv-login-footnote">PARKING VIS / OPERATOR ACCESS</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')
const router = useRouter()

async function login() {
  loading.value = true
  error.value = ''
  try {
    const res = await axios.post('/api/login', { username: username.value, password: password.value })
    const token = res.data.access_token
    localStorage.setItem('token', token)
    localStorage.setItem('is_admin', res.data.is_admin ? 'true' : 'false')
    localStorage.setItem('permissions', JSON.stringify(res.data.permissions || []))
    // Persist username so the app bar shows the real name on first
    // render instead of the default "User" placeholder while syncUser
    // is still in flight.
    localStorage.setItem('username', username.value)
    axios.defaults.headers.common['Authorization'] = `Bearer ${token}`

    // Redirect to live dashboard after login
    router.push('/dashboard')
  } catch (e) {
    // C3 audit fix: 428 Precondition Required means the install has
    // no admin yet.  Redirect to /setup instead of showing a generic
    // "invalid credentials" error.
    if (e.response?.status === 428) {
      router.push('/setup')
      return
    }
    console.error('Login error:', e)

    if (!e.response) {
      // Network error / backend down / connection refused
      error.value = 'Cannot connect to backend server. Please verify the service is running.'
    } else if (e.response.status === 401 || e.response.status === 403) {
      error.value = e.response.data?.detail || 'Invalid username or password'
    } else if (e.response.status === 500) {
      error.value = 'Backend server error (500). Please check server logs or try again shortly.'
    } else if (e.response.status === 502 || e.response.status === 503 || e.response.status === 504) {
      error.value = 'Backend service is unavailable or starting up. Please wait a moment and try again.'
    } else {
      error.value = e.response.data?.detail?.message || e.response.data?.detail || 'An unexpected error occurred during login.'
    }
  } finally {
    loading.value = false
  }
}

// C3 audit fix: on a fresh install, /api/setup/status returns
// setup_required=true.  Redirect to /setup so the operator never
// sees a broken login form on a brand-new install.
async function checkSetupStatus() {
  try {
    const res = await axios.get('/api/setup/status')
    if (res.data?.setup_required) {
      router.push('/setup')
    }
  } catch (e) {
    // Backend not reachable — let the login form show its own error
    // when the user submits.
  }
}

onMounted(() => {
  checkSetupStatus()
})
</script>
