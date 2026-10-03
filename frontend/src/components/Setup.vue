<template>
  <v-app>
    <v-main>
      <v-container class="fill-height" fluid>
        <v-row align="center" justify="center">
          <v-col cols="12" sm="8" md="6" lg="5">
            <v-card class="pa-4" elevation="8">
              <v-card-title class="text-h5 d-flex align-center">
                <v-icon class="mr-2" color="primary">mdi-shield-key-outline</v-icon>
                First-Time Setup
              </v-card-title>
              <v-card-subtitle class="pb-4">
                Create the initial administrator account for Parking Vis.
              </v-card-subtitle>

              <v-alert
                v-if="setupDisabled"
                type="warning"
                variant="tonal"
                class="mb-4"
                density="compact"
              >
                An administrator already exists. The /setup page is disabled.
                <v-btn class="mt-2" variant="text" size="small" to="/">Go to login</v-btn>
              </v-alert>

              <v-form ref="form" v-model="formValid" @submit.prevent="submit">
                <v-text-field
                  v-model="username"
                  label="Admin username"
                  :rules="usernameRules"
                  autocomplete="username"
                  required
                  variant="outlined"
                  density="comfortable"
                  prepend-inner-icon="mdi-account"
                  class="mb-2"
                />
                <v-text-field
                  v-model="password"
                  label="Admin password"
                  :rules="passwordRules"
                  :type="showPassword ? 'text' : 'password'"
                  :append-inner-icon="showPassword ? 'mdi-eye-off' : 'mdi-eye'"
                  @click:append-inner="showPassword = !showPassword"
                  autocomplete="new-password"
                  required
                  variant="outlined"
                  density="comfortable"
                  prepend-inner-icon="mdi-lock"
                  class="mb-2"
                />
                <v-text-field
                  v-model="confirm"
                  label="Confirm password"
                  :rules="confirmRules"
                  :type="showPassword ? 'text' : 'password'"
                  autocomplete="new-password"
                  required
                  variant="outlined"
                  density="comfortable"
                  prepend-inner-icon="mdi-lock-check"
                  class="mb-4"
                />

                <!-- End User License Agreement (EULA) -->
                <div class="mb-4">
                  <div class="text-subtitle-2 mb-1">End User License Agreement</div>
                  <div
                    class="pa-3 border rounded mb-2 overflow-y-auto"
                    style="max-height: 180px; font-size: 0.75rem; line-height: 1.3; background: rgba(0,0,0,0.03); border: 1px solid rgba(0, 0, 0, 0.12);"
                  >
                    <pre style="white-space: pre-wrap; font-family: inherit; margin: 0;">{{ eulaText }}</pre>
                  </div>
                  <v-checkbox
                    v-model="acceptEula"
                    label="I accept the End User License Agreement"
                    :rules="[v => !!v || 'You must accept the EULA to continue']"
                    density="compact"
                    hide-details
                  />
                </div>

                <v-alert
                  v-if="error"
                  type="error"
                  variant="tonal"
                  class="mb-4"
                  density="compact"
                >
                  {{ error }}
                </v-alert>

                <v-btn
                  type="submit"
                  color="primary"
                  block
                  :loading="loading"
                  :disabled="!formValid || setupDisabled"
                >
                  Create admin account
                </v-btn>
              </v-form>

              <v-card-text class="text-caption text-medium-emphasis pt-4">
                This page only appears on fresh installs. Once an admin is created,
                the page is disabled and the standard login form is shown.
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>
      </v-container>
    </v-main>
  </v-app>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'

const router = useRouter()

const username = ref('')
const password = ref('')
const confirm = ref('')
const showPassword = ref(false)
const formValid = ref(false)
const form = ref(null)
const loading = ref(false)
const error = ref('')
const setupDisabled = ref(false)
const acceptEula = ref(false)
const eulaText = ref('Loading EULA...')

const usernameRules = [
  v => !!v || 'Username is required',
  v => (v && v.length >= 3) || 'Username must be at least 3 characters',
  v => /^[a-zA-Z0-9_.-]+$/.test(v) || 'Only letters, digits, dot, dash, underscore',
]

const passwordRules = [
  v => !!v || 'Password is required',
  v => (v && v.length >= 6) || 'Password must be at least 6 characters',
]

const confirmRules = computed(() => [
  v => !!v || 'Please confirm the password',
  v => v === password.value || 'Passwords do not match',
])

async function checkStatus() {
  try {
    const res = await axios.get('/api/setup/status')
    if (!res.data.setup_required) {
      setupDisabled.value = true
    }
  } catch (e) {
    error.value = 'Could not contact the server. Is the backend running?'
  }
}

async function loadEula() {
  try {
    const res = await axios.get('/api/setup/eula')
    eulaText.value = res.data.eula_text
  } catch (e) {
    eulaText.value = 'Failed to load End User License Agreement. Please refresh the page.'
  }
}

async function submit() {
  if (!form.value || !(await form.value.validate()).valid) return
  loading.value = true
  error.value = ''
  try {
    await axios.post('/api/setup/admin', {
      username: username.value,
      password: password.value,
      accept_eula: acceptEula.value,
    })
    // Auto-login: hit /api/login with the same credentials, then
    // redirect to /cameras like the normal login flow.
    const login = await axios.post('/api/login', {
      username: username.value,
      password: password.value,
    })
    const token = login.data.access_token
    localStorage.setItem('token', token)
    localStorage.setItem('is_admin', login.data.is_admin ? 'true' : 'false')
    localStorage.setItem('permissions', JSON.stringify(login.data.permissions || []))
    localStorage.setItem('username', username.value)
    axios.defaults.headers.common['Authorization'] = `Bearer ${token}`
    router.push('/dashboard')
  } catch (e) {
    error.value = e.response?.data?.detail?.message || e.response?.data?.detail || 'Setup failed'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  checkStatus()
  loadEula()
})
</script>
