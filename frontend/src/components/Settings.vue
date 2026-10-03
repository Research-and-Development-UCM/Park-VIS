<template>
  <v-container>
    <v-card elevation="2" max-width="600" class="mx-auto">
      <v-toolbar color="primary" density="comfortable">
        <v-toolbar-title class="text-h6 text-white">System Settings</v-toolbar-title>
      </v-toolbar>
      <v-divider></v-divider>
      <v-card-text class="pa-6">
        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Inference Interval</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">
            Adjust how often the system pulls fresh snapshots from cameras and runs the occupancy detection algorithm (in seconds).
          </div>
          <v-slider
            v-model="interval"
            :min="1"
            :max="300"
            :step="1"
            thumb-label="always"
            color="primary"
            :readonly="!hasPermission('edit_settings')"
          >
            <template v-slot:append>
              <v-text-field
                v-model="interval"
                density="compact"
                style="width: 80px"
                type="number"
                variant="outlined"
                hide-details
              ></v-text-field>
            </template>
          </v-slider>
          <div class="d-flex align-center mt-2">
            <v-icon color="info" size="small" class="mr-2">mdi-information-outline</v-icon>
            <span class="text-caption text-grey-darken-1">
              Lower intervals provide more real-time data but increase server load and network usage.
            </span>
          </div>
        </div>

        <v-divider class="mb-6"></v-divider>

        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Occupancy Thresholds (Hysteresis)</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">
            Adjust the **AI confidence levels** (0-100%) required to trigger a status change. 
            This creates a "sticky" zone where the current state is maintained unless the model is highly certain of a change, preventing status flickering.
          </div>
          
          <div class="d-flex justify-space-between text-caption font-weight-bold mb-2">
            <span class="text-success">FREE (< {{ (freeThresh * 100).toFixed(0) }}%)</span>
            <span class="text-grey">STICKY (No Change)</span>
            <span class="text-error">OCCUPIED (> {{ (occThresh * 100).toFixed(0) }}%)</span>
          </div>

          <div class="mt-4">
            <v-range-slider
              v-model="thresholds"
              :min="0.01"
              :max="0.99"
              :step="0.01"
              thumb-label="always"
              strict
              color="primary"
            ></v-range-slider>
          </div>
          
          <div class="d-flex align-center mt-4">
            <!--
              H10 audit fix: cross-field validation.  The text fields
              bypass the range-slider's strict mode, so a user can
              invert the hysteresis band (freeThresh=0.9, occThresh=0.1).
              The :rules enforce freeThresh < occThresh and require
              numeric input; invalid input shows an error and blocks save.
            -->
            <v-text-field
              v-model.number="freeThresh"
              label="Free"
              density="compact"
              type="number"
              variant="outlined"
              hide-details="auto"
              prefix="<"
              class="mr-4"
              :rules="[v => typeof v === 'number' && !isNaN(v) || 'Must be a number', v => typeof v === 'number' && v < +occThresh || 'Free must be less than Occupied']"
            ></v-text-field>
            <v-text-field
              v-model.number="occThresh"
              label="Occupied"
              density="compact"
              type="number"
              variant="outlined"
              hide-details="auto"
              prefix=">"
              :rules="[v => typeof v === 'number' && !isNaN(v) || 'Must be a number', v => typeof v === 'number' && v > +freeThresh || 'Occupied must be greater than Free']"
            ></v-text-field>

          </div>
        </div>

        <v-divider class="mb-6"></v-divider>

        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Inference Device</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">
            Select whether to use the GPU or CPU for model inference.
            <template v-if="!cudaAvailable && gpuError">
              <v-alert
                type="warning"
                density="compact"
                variant="tonal"
                class="mt-2"
              >
                <div class="font-weight-bold mb-1">GPU not available</div>
                <div class="text-caption">{{ gpuError }}</div>
                <div class="text-caption mt-1">
                  The model will run on CPU until the GPU provider can be loaded.
                </div>
              </v-alert>
            </template>
            <template v-else-if="!cudaAvailable">
              <div class="text-caption text-grey-darken-1 mt-1">
                No GPU detected on this host. The model will run on CPU.
              </div>
            </template>
          </div>
          <v-select
            v-model="device"
            :items="gpuItems"
            item-title="title"
            item-value="value"
            density="comfortable"
            variant="outlined"
            :readonly="!hasPermission('edit_settings')"
            color="primary"
          ></v-select>
        </div>

        <v-divider class="mb-6"></v-divider>

        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Max Inference Resolution</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">
            The maximum pixel width/height images are resized to before being sent to the AI model. 
            <strong>Higher</strong> improves accuracy for small or distant spots but uses more memory and time.
            <strong>Lower</strong> increases speed and reduces system load.
          </div>
          <v-select
            v-model="maxRes"
            :items="[
              { title: '512px (Fastest)', value: '512' },
              { title: '640px', value: '640' },
              { title: '768px (Balanced)', value: '768' },
              { title: '896px', value: '896' },
              { title: '1024px (Default)', value: '1024' }
            ]"
            item-title="title"
            item-value="value"
            density="comfortable"
            variant="outlined"
            :readonly="!hasPermission('edit_settings')"
            color="primary"
          ></v-select>
        </div>

        <v-divider class="mb-6"></v-divider>

        <div v-if="hasPermission('edit_settings')" class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Alerting (SMTP for email channels)</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">
            Configure the system-wide SMTP server used by email alert
            channels. Use your own email SMTP server, or try a third
            party provider like
            <a
              href="https://www.smtp2go.com/"
              target="_blank"
              rel="noopener"
              class="text-primary"
            >SMTP2Go</a>.
          </div>

          <v-switch
            v-model="alertingEnabled"
            label="Enable email alerts"
            color="primary"
            hide-details
            class="mb-3"
          ></v-switch>

          <v-row dense>
            <v-col cols="8">
              <v-text-field
                v-model="smtpHost"
                label="SMTP host"
                variant="outlined"
                density="comfortable"
                placeholder="smtp.example.com"
                prepend-inner-icon="mdi-server-network"
                :disabled="!alertingEnabled"
              ></v-text-field>
            </v-col>
            <v-col cols="4">
              <v-text-field
                v-model.number="smtpPort"
                label="Port"
                variant="outlined"
                density="comfortable"
                type="number"
                :disabled="!alertingEnabled"
              ></v-text-field>
            </v-col>
          </v-row>

          <v-row dense class="mt-2">
            <v-col cols="6">
              <v-text-field
                v-model="smtpUser"
                label="Username"
                variant="outlined"
                density="comfortable"
                :disabled="!alertingEnabled"
              ></v-text-field>
            </v-col>
            <v-col cols="6">
              <v-text-field
                v-model="smtpPass"
                label="Password"
                variant="outlined"
                density="comfortable"
                type="password"
                :disabled="!alertingEnabled"
              ></v-text-field>
            </v-col>
          </v-row>

          <v-text-field
            v-model="smtpFrom"
            label="Default from address"
            variant="outlined"
            density="comfortable"
            class="mt-2"
            placeholder="alerts@your-domain.com"
            :disabled="!alertingEnabled"
          ></v-text-field>

          <v-switch
            v-model="smtpUseTls"
            label="Use STARTTLS"
            color="primary"
            hide-details
            class="mt-2"
            :disabled="!alertingEnabled"
          ></v-switch>
        </div>
      </v-card-text>
      <v-divider></v-divider>
      <v-card-actions class="pa-4 bg-grey-lighten-5">
        <v-spacer></v-spacer>
        <v-btn
          v-if="hasPermission('edit_settings')"
          color="primary"
          variant="flat"
          :loading="saving"
          prepend-icon="mdi-content-save"
          @click="saveSettings"
          class="px-6"
        >
          Save Settings
        </v-btn>
      </v-card-actions>
    </v-card>

    <v-card elevation="2" max-width="600" class="mx-auto mt-6" v-if="hasPermission('edit_settings')">
      <v-toolbar color="secondary" density="comfortable">
        <v-toolbar-title class="text-h6 text-white">Persistence & Storage</v-toolbar-title>
      </v-toolbar>
      <v-divider></v-divider>
      <v-card-text class="pa-6">
        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Image Storage & Quality</div>
          <div class="text-body-2 text-grey-darken-1 mb-6">
            Adjust JPEG compression levels to balance disk space and visual clarity. 
          </div>

          <div class="mb-4">
            <div class="text-subtitle-2 mb-1">Full Snapshot Quality</div>
            <v-slider
              v-model="qualitySnap"
              :min="10" :max="100" :step="5"
              thumb-label="always"
              color="secondary"
              :readonly="!hasPermission('edit_settings')"
            >
              <template v-slot:append>
                <div style="width: 40px" class="text-caption">{{ qualitySnap }}%</div>
              </template>
            </v-slider>
          </div>

          <div class="mb-4">
            <div class="text-subtitle-2 mb-1">Vehicle Crop Quality</div>
            <v-slider
              v-model="qualityCrop"
              :min="10" :max="100" :step="5"
              thumb-label="always"
              color="secondary"
              :readonly="!hasPermission('edit_settings')"
            >
              <template v-slot:append>
                <div style="width: 40px" class="text-caption">{{ qualityCrop }}%</div>
              </template>
            </v-slider>
          </div>

          <div class="mb-4">
            <div class="text-subtitle-2 mb-1">Max Snapshot Resolution</div>
            <div class="text-body-2 text-grey-darken-1 mb-2">
              Images wider or taller than this are downscaled before storage.
              <strong>Lower</strong> saves disk space.
            </div>
            <v-select
              v-model="maxSnapRes"
              :items="[
                { title: '640px', value: '640' },
                { title: '800px', value: '800' },
                { title: '960px', value: '960' },
                { title: '1280px', value: '1280' },
                { title: '1920px (Default)', value: '1920' },
                { title: '2560px (2K)', value: '2560' },
                { title: '3840px (4K)', value: '3840' },
              ]"
              item-title="title" item-value="value"
              density="comfortable" variant="outlined"
              :readonly="!hasPermission('edit_settings')"
              color="primary"
            ></v-select>
          </div>
        </div>

        <v-divider class="mb-6"></v-divider>

        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-4">Current Storage Usage</div>
          <v-list density="compact" class="bg-grey-lighten-4 rounded-lg">
            <v-list-item title="Camera Snapshots" :subtitle="formatSize(stats.snapshots?.total_size_bytes)"></v-list-item>
            <v-list-item title="Event Crops (128x128)" :subtitle="formatSize(stats.crops?.total_size_bytes)"></v-list-item>
            <v-list-item title="Occupancy Data (Raw)" :subtitle="formatSize(stats.sqlite?.raw_occupancy_est)"></v-list-item>
            <v-list-item title="Occupancy Data (Hourly)" :subtitle="formatSize(stats.sqlite?.hourly_data_est)"></v-list-item>
            <v-divider></v-divider>
            <v-list-item title="Database File (SQLite)" :subtitle="formatSize(stats.sqlite?.size_bytes)" class="font-weight-bold"></v-list-item>
          </v-list>
        </div>

        <v-divider class="mb-6"></v-divider>

        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Snapshot Retention</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">How long to keep full-size snapshots before purging them (in hours).</div>
          <v-slider v-model="retention.images" :min="1" :max="168" :step="1" thumb-label="always" color="secondary">
            <template v-slot:append>
              <div style="width: 60px" class="text-caption">{{ retention.images }}h</div>
            </template>
          </v-slider>
        </div>

        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Raw Metrics Retention</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">How long to keep second-by-second occupancy records (in days).</div>
          <v-slider v-model="retention.raw" :min="1" :max="30" :step="1" thumb-label="always" color="secondary">
            <template v-slot:append>
              <div style="width: 60px" class="text-caption">{{ retention.raw }}d</div>
            </template>
          </v-slider>
        </div>

        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Event History Retention</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">How long to keep state-change events and their crops (in days).</div>
          <v-slider v-model="retention.events" :min="7" :max="365" :step="1" thumb-label="always" color="secondary">
            <template v-slot:append>
              <div style="width: 60px" class="text-caption">{{ retention.events }}d</div>
            </template>
          </v-slider>
        </div>

        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Hourly History Retention</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">How long to keep aggregated hourly trends for long-term charts (in days).</div>
          <v-slider v-model="retention.hourly" :min="30" :max="1825" :step="1" thumb-label="always" color="secondary">
            <template v-slot:append>
              <div style="width: 60px" class="text-caption">{{ retention.hourly }}d</div>
            </template>
          </v-slider>
        </div>
      </v-card-text>
      <v-divider></v-divider>
      <v-card-actions class="pa-4 bg-grey-lighten-5">
        <v-spacer></v-spacer>
        <v-btn color="secondary" variant="flat" :loading="saving" prepend-icon="mdi-content-save" @click="saveSettings">
          Save Persistence
        </v-btn>
      </v-card-actions>
    </v-card>

    <v-card elevation="2" max-width="600" class="mx-auto mt-6" v-if="hasPermission('edit_settings')">
      <v-toolbar color="warning" density="comfortable">
        <v-toolbar-title class="text-h6 text-white">Security & Encryption (SSL/TLS)</v-toolbar-title>
      </v-toolbar>
      <v-divider></v-divider>
      <v-card-text class="pa-6">
        <div class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-1">Enable HTTPS (SSL/TLS)</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">
            Encrypt connection traffic to prevent interception of camera feeds and passwords. 
            Once enabled and saved, you must restart the service and access the dashboard using <strong>https://</strong>.
          </div>
          <v-switch
            v-model="sslEnabled"
            label="Enable HTTPS / SSL"
            color="warning"
            hide-details
            class="mb-4"
          ></v-switch>
          <v-alert
            v-if="sslEnabled && (!sslCertPath || !sslKeyPath)"
            type="error"
            density="compact"
            variant="tonal"
            class="mt-2 text-caption"
            icon="mdi-alert-circle"
          >
            Cannot enable HTTPS: Please upload certificates or generate a self-signed certificate below first.
          </v-alert>
        </div>

        <v-divider v-if="sslEnabled" class="mb-6"></v-divider>

        <div v-if="sslEnabled" class="mb-6">
          <div class="text-subtitle-1 font-weight-bold mb-2">SSL Certificates</div>
          
          <!-- Current Cert Info -->
          <v-alert
            v-if="sslCertPath || sslKeyPath"
            type="info"
            density="compact"
            variant="tonal"
            class="mb-4 text-caption text-wrap"
            icon="mdi-shield-check"
          >
            <div class="font-weight-bold">Current Certificate Files:</div>
            <div style="word-break: break-all;">Cert: {{ sslCertPath || 'Not set' }}</div>
            <div style="word-break: break-all;">Key: {{ sslKeyPath || 'Not set' }}</div>
          </v-alert>

          <!-- Upload Files -->
          <div class="text-subtitle-2 mb-2">Upload Custom Certificate & Private Key</div>
          <v-file-input
            v-model="certFile"
            label="Certificate File (.crt / .pem)"
            variant="outlined"
            density="comfortable"
            prepend-icon="mdi-certificate"
            accept=".crt,.pem,.cert"
            class="mb-2"
          ></v-file-input>
          <v-file-input
            v-model="keyFile"
            label="Private Key File (.key)"
            variant="outlined"
            density="comfortable"
            prepend-icon="mdi-key"
            accept=".key,.pem"
            class="mb-4"
          ></v-file-input>

          <v-btn
            color="primary"
            variant="tonal"
            :loading="uploadingCerts"
            :disabled="!certFile || !keyFile"
            prepend-icon="mdi-upload"
            block
            class="mb-6"
            @click="uploadCerts"
          >
            Upload SSL Credentials
          </v-btn>

          <v-divider class="mb-6"></v-divider>

          <!-- Generate Self-Signed Cert -->
          <div class="text-subtitle-2 mb-2">Auto-Generate Self-Signed Certificate</div>
          <div class="text-body-2 text-grey-darken-1 mb-4">
            If you do not have a custom domain certificate, generate a self-signed certificate. 
            Browsers will show a warning upon first access, which you can bypass.
          </div>
          <v-btn
            color="warning"
            variant="outlined"
            :loading="generatingSelfSigned"
            prepend-icon="mdi-cached"
            block
            @click="generateSelfSigned"
          >
            Generate Self-Signed Certificate
          </v-btn>
        </div>
      </v-card-text>
      <v-divider></v-divider>
      <v-card-actions class="pa-4 bg-grey-lighten-5">
        <v-spacer></v-spacer>
        <v-btn
          color="warning"
          variant="flat"
          :loading="saving"
          :disabled="sslEnabled && (!sslCertPath || !sslKeyPath)"
          prepend-icon="mdi-content-save"
          @click="saveSecuritySettings"
          class="px-6"
        >
          Save SSL Configuration
        </v-btn>
      </v-card-actions>
    </v-card>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="3000">
      {{ snackbar.text }}
      <template v-slot:actions>
        <v-btn variant="text" @click="snackbar.show = false">Close</v-btn>
      </template>
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue';
import axios from 'axios';
import { formatSize } from '../utils/format';

const interval = ref(60);
const occThresh = ref(0.75);
const freeThresh = ref(0.25);

const thresholds = computed({
  get: () => [freeThresh.value, occThresh.value],
  set: (val) => {
    freeThresh.value = val[0];
    occThresh.value = val[1];
  }
});

const device = ref('auto');
const maxRes = ref('1024');
const qualitySnap = ref(85);
const qualityCrop = ref(90);
const maxSnapRes = ref('1920');
const cudaAvailable = ref(false);
const gpuProvider = ref('');
// When ``cudaAvailable`` is false, the backend returns a human-
// readable error string from the C++ probe (e.g. "libcudnn.so.9:
// cannot open shared object file"). The Settings page surfaces
// this in a warning so the user can see *why* GPU is missing
// instead of silently falling back to CPU.
const gpuError = ref('');

const alertingEnabled = ref(false);
const smtpHost = ref('');
const smtpPort = ref(587);
const smtpUser = ref('');
const smtpPass = ref('');
const smtpFrom = ref('');
const smtpUseTls = ref(true);
const saving = ref(false);
const snackbar = ref({ show: false, text: '', color: 'success' });

const stats = ref({
  scan_images: 0,
  event_crops: 0,
  raw_occupancy: 0,
  hourly_data: 0,
  total_db: 0
});

const retention = ref({
  images: 24,
  raw: 7,
  events: 30,
  hourly: 365
});

const sslEnabled = ref(false);
const sslCertPath = ref('');
const sslKeyPath = ref('');
const certFile = ref(null);
const keyFile = ref(null);
const uploadingCerts = ref(false);
const generatingSelfSigned = ref(false);

const hasPermission = (p) => {
  if (localStorage.getItem('is_admin') === 'true') return true;
  const permsStr = localStorage.getItem('permissions');
  if (!permsStr) return false;
  try {
    const perms = JSON.parse(permsStr);
    return Array.isArray(perms) && perms.includes(p);
  } catch (e) {
    return false;
  }
};

const gpuItems = computed(() => {
  const items = [
    { title: 'CPU Only', value: 'cpu' },
  ];
  if (cudaAvailable.value) {
    let title = gpuProvider.value === 'DmlExecutionProvider'
      ? 'GPU (DirectML)'
      : 'NVIDIA GPU (CUDA)';
    items.unshift({
      title,
      value: 'cuda',
    });
  }
  return items;
});

const fetchSetting = async (key, defaultValue) => {
  try {
    const res = await axios.get(`/api/settings/${key}`);
    return res.data.value;
  } catch (e) {
    console.warn(`[SETTINGS] Setting '${key}' not found, using default: ${defaultValue}`);
    return defaultValue;
  }
};

const fetchSettings = async () => {
  const canEdit = hasPermission('edit_settings');
  
  try {
    // Check for GPU provider BEFORE we read the persisted device
    // value: if GPU is unavailable (e.g. cuDNN 9 missing), we force
    // the device to "cpu" so the user can't select an option that
    // won't actually work. The persisted value stays in the DB
    // unchanged so when GPU becomes available again, the user's
    // previous choice is restored.
    try {
      const resGpu = await axios.get('/api/settings/gpu-provider');
      cudaAvailable.value = resGpu.data.available;
      gpuProvider.value = resGpu.data.provider || '';
      gpuError.value = resGpu.data.error || '';
    } catch (e) {
      console.error('[SETTINGS] Error checking GPU provider:', e);
    }


    // Fetch settings individually to be more resilient to 404s
    interval.value = parseInt(await fetchSetting('inference_interval', '60'));
    occThresh.value = parseFloat(await fetchSetting('hysteresis_occupied_threshold', '0.75'));
    freeThresh.value = parseFloat(await fetchSetting('hysteresis_free_threshold', '0.25'));
    const persistedDevice = await fetchSetting('inference_device', 'auto');
    device.value = (cudaAvailable.value || persistedDevice === 'cpu') ? persistedDevice : 'cpu';
    maxRes.value = await fetchSetting('max_inference_resolution', '1024');
    qualitySnap.value = parseInt(await fetchSetting('quality_snapshots', '85'));
    qualityCrop.value = parseInt(await fetchSetting('quality_crops', '90'));
    maxSnapRes.value = await fetchSetting('max_snapshot_resolution', '1920');
    sslEnabled.value = (await fetchSetting('ssl_enabled', 'false')).toLowerCase() === 'true';
    sslCertPath.value = await fetchSetting('ssl_cert_path', '');
    sslKeyPath.value = await fetchSetting('ssl_key_path', '');

    retention.value.images = parseInt(await fetchSetting('retention_images_hours', '24'));
    retention.value.raw = parseInt(await fetchSetting('retention_raw_data_days', '7'));
    retention.value.events = parseInt(await fetchSetting('retention_events_days', '30'));
    retention.value.hourly = parseInt(await fetchSetting('retention_hourly_data_days', '365'));

    alertingEnabled.value = (await fetchSetting('alerting_enabled', 'false')).toLowerCase() === 'true';
    smtpHost.value = await fetchSetting('alerting_smtp_host', '');
    smtpPort.value = parseInt(await fetchSetting('alerting_smtp_port', '587'));
    smtpUser.value = await fetchSetting('alerting_smtp_user', '');
    smtpPass.value = await fetchSetting('alerting_smtp_pass', '');
    smtpFrom.value = await fetchSetting('alerting_smtp_from', '');
    smtpUseTls.value = (await fetchSetting('alerting_smtp_use_tls', 'true')).toLowerCase() === 'true';

    if (canEdit) {
      try {
        const token = localStorage.getItem('token');
        const config = token ? { headers: { 'Authorization': `Bearer ${token}` } } : {};
        const resStats = await axios.get('/api/settings/storage-stats', config);
        stats.value = resStats.data;
      } catch (e) {
        if (e.response?.status === 401) {
          console.warn('[SETTINGS] Unauthorized to fetch storage stats.');
        } else {
          console.error('[SETTINGS] Error fetching storage stats:', e);
        }
      }
    }
  } catch (error) {
    console.error('[SETTINGS] Error during fetchSettings:', error);
  }
};

const saveSettings = async () => {
  saving.value = true;
  try {
    await Promise.all([
      axios.put('/api/settings/inference_interval', { value: interval.value.toString() }),
      axios.put('/api/settings/hysteresis_occupied_threshold', { value: occThresh.value.toString() }),
      axios.put('/api/settings/hysteresis_free_threshold', { value: freeThresh.value.toString() }),
      axios.put('/api/settings/inference_device', { value: device.value }),
      axios.put('/api/settings/max_inference_resolution', { value: maxRes.value }),
      axios.put('/api/settings/quality_snapshots', { value: qualitySnap.value.toString() }),
      axios.put('/api/settings/quality_crops', { value: qualityCrop.value.toString() }),
      axios.put('/api/settings/max_snapshot_resolution', { value: maxSnapRes.value }),
      axios.put('/api/settings/retention_images_hours', { value: retention.value.images.toString() }),
      axios.put('/api/settings/retention_raw_data_days', { value: retention.value.raw.toString() }),
      axios.put('/api/settings/retention_events_days', { value: retention.value.events.toString() }),
      axios.put('/api/settings/retention_hourly_data_days', { value: retention.value.hourly.toString() }),
      axios.put('/api/settings/alerting_enabled', { value: alertingEnabled.value ? 'true' : 'false' }),
      axios.put('/api/settings/alerting_smtp_host', { value: smtpHost.value }),
      axios.put('/api/settings/alerting_smtp_port', { value: smtpPort.value.toString() }),
      axios.put('/api/settings/alerting_smtp_user', { value: smtpUser.value }),
      axios.put('/api/settings/alerting_smtp_pass', { value: smtpPass.value }),
      axios.put('/api/settings/alerting_smtp_from', { value: smtpFrom.value }),
      axios.put('/api/settings/alerting_smtp_use_tls', { value: smtpUseTls.value ? 'true' : 'false' }),
    ]);
    snackbar.value = { show: true, text: 'Settings saved successfully.', color: 'success' };
    const resStats = await axios.get('/api/settings/storage-stats');
    stats.value = resStats.data;
  } catch (error) {
    console.error('Error saving settings:', error);
    snackbar.value = { show: true, text: 'Error saving settings', color: 'error' };
  } finally {
    saving.value = false;
  }
};

const uploadCerts = async () => {
  if (!certFile.value || !keyFile.value) return;
  uploadingCerts.value = true;
  const formData = new FormData();
  formData.append('cert', certFile.value);
  formData.append('key', keyFile.value);
  try {
    const token = localStorage.getItem('token');
    const headers = { 'Content-Type': 'multipart/form-data' };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    await axios.post('/api/settings/ssl/upload', formData, { headers });
    sslCertPath.value = await fetchSetting('ssl_cert_path', '');
    sslKeyPath.value = await fetchSetting('ssl_key_path', '');
    certFile.value = null;
    keyFile.value = null;
    snackbar.value = { show: true, text: 'Custom SSL certificates uploaded successfully', color: 'success' };
  } catch (error) {
    console.error('Error uploading certificates:', error);
    snackbar.value = { show: true, text: 'Error uploading certificates', color: 'error' };
  } finally {
    uploadingCerts.value = false;
  }
};

const generateSelfSigned = async () => {
  if (sslCertPath.value) {
    const confirmGenerate = window.confirm(
      'An SSL certificate already exists. Generating a new self-signed certificate will overwrite the existing one. Do you want to generate a new one?'
    );
    if (!confirmGenerate) return;
  }
  generatingSelfSigned.value = true;
  try {
    await axios.post('/api/settings/ssl/generate-self-signed', null, { params: { force: true } });
    sslCertPath.value = await fetchSetting('ssl_cert_path', '');
    sslKeyPath.value = await fetchSetting('ssl_key_path', '');
    snackbar.value = { show: true, text: 'Self-signed certificate generated successfully', color: 'success' };
  } catch (error) {
    console.error('Error generating self-signed certificate:', error);
    snackbar.value = { show: true, text: 'Error generating certificate', color: 'error' };
  } finally {
    generatingSelfSigned.value = false;
  }
};

const saveSecuritySettings = async () => {
  saving.value = true;
  try {
    await axios.put('/api/settings/ssl_enabled', { value: sslEnabled.value ? 'true' : 'false' });
    snackbar.value = { 
      show: true, 
      text: 'SSL configuration saved. Please restart the Parking Vis backend to apply.', 
      color: 'success' 
    };
  } catch (error) {
    console.error('Error saving SSL enabled state:', error);
    snackbar.value = { show: true, text: 'Error saving SSL enabled state', color: 'error' };
  } finally {
    saving.value = false;
  }
};

onMounted(fetchSettings);
</script>

<style scoped>
</style>
