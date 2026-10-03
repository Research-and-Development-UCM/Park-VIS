<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <div class="text-h5 font-weight-bold mb-6 d-flex align-center">
          <v-icon color="blue-grey" class="mr-3">mdi-console-network</v-icon>
          Network & Stream Diagnostics
          <v-spacer></v-spacer>
          <v-btn color="primary" variant="tonal" prepend-icon="mdi-refresh" @click="fetchStats" :loading="loading">Refresh</v-btn>
        </div>
      </v-col>

      <!-- Active Streams Grid -->
      <v-col v-for="stream in streams" :key="stream.camera_id" cols="12" md="6" lg="4">
        <v-card elevation="2" rounded="lg" class="fill-height" border>
          <v-toolbar :color="getStatusColor(stream)" density="comfortable" class="px-4">
            <v-icon start>{{ getStatusIcon(stream) }}</v-icon>
            <v-toolbar-title class="text-subtitle-1 font-weight-bold">
              {{ stream.camera_name }}
            </v-toolbar-title>
            <v-spacer></v-spacer>
            <v-chip size="x-small" variant="flat" color="white" class="text-black font-weight-bold">
              ID: {{ stream.camera_id }}
            </v-chip>
          </v-toolbar>

          <v-card-text class="pa-4">
            <div class="d-flex justify-space-between mb-2">
              <span class="text-grey-darken-1 text-body-2">GStreamer State:</span>
              <v-chip size="x-small" label class="font-weight-bold">{{ stream.state }}</v-chip>
            </div>

            <div class="d-flex justify-space-between mb-2">
              <span class="text-grey-darken-1 text-body-2">Uptime:</span>
              <span class="text-body-2 font-weight-bold">{{ formatUptime(stream.uptime_seconds) }}</span>
            </div>

            <div class="d-flex justify-space-between mb-2">
              <span class="text-grey-darken-1 text-body-2">
                {{ stream.source_type === 'video' ? 'Disk Read Throughput:' : 'Network Throughput:' }}
              </span>
              <v-chip size="x-small" :color="stream.source_type === 'video' ? 'blue-grey-lighten-5' : 'green-lighten-5'" :class="stream.source_type === 'video' ? 'text-blue-grey-darken-3' : 'text-green-darken-3'" class="font-weight-bold">
                {{ (stream.network_bitrate_mbps ?? 0).toFixed(3) }} Mbps
              </v-chip>
            </div>

            <div class="d-flex justify-space-between mb-2">
              <span class="text-grey-darken-1 text-body-2">AI Processing Load:</span>
              <v-chip size="x-small" color="blue-lighten-5" class="text-blue-darken-3 font-weight-bold">
                {{ stream.bitrate_mbps ?? '—' }} Mbps
              </v-chip>
            </div>

            <div class="d-flex justify-space-between mb-2">
              <span class="text-grey-darken-1 text-body-2">Resolution:</span>
              <span class="text-body-2 font-weight-bold">{{ stream.resolution }}</span>
            </div>

            <div class="d-flex justify-space-between mb-2">
              <span class="text-grey-darken-1 text-body-2">Frames Per Second:</span>
              <div class="text-right">
                <div class="text-body-2 font-weight-bold">{{ stream.fps_actual }} / {{ stream.fps_target }}</div>
                <div class="text-xxs text-grey">Actual vs Target</div>
              </div>
            </div>

            <v-divider class="my-3"></v-divider>

            <v-row no-gutters>
              <v-col cols="6">
                <div class="text-xxs text-grey uppercase font-weight-bold">Total Received</div>
                <div class="text-body-1 font-weight-bold text-success">{{ stream.total_received.toLocaleString() }}</div>
              </v-col>
              <v-col cols="6" class="text-right">
                <div class="text-xxs text-grey uppercase font-weight-bold">Total Dropped</div>
                <div class="text-body-1 font-weight-bold text-error">{{ stream.total_dropped.toLocaleString() }}</div>
              </v-col>
            </v-row>

            <v-divider class="my-3"></v-divider>

            <div class="d-flex align-center">
              <v-icon :color="getAgeColor(stream.last_frame_seconds_ago)" size="small" class="mr-2">mdi-clock-outline</v-icon>
              <span class="text-caption text-grey-darken-1">Last Frame:</span>
              <v-spacer></v-spacer>
              <span class="text-caption font-weight-bold" :class="getAgeTextColor(stream.last_frame_seconds_ago)">
                {{ stream.last_frame_seconds_ago < 0 ? 'NEVER' : stream.last_frame_seconds_ago + 's ago' }}
              </span>
            </div>

            <!-- Error Log if any -->
            <div v-if="stream.errors && stream.errors.length > 0" class="mt-4">
              <div class="text-xxs text-error font-weight-bold mb-1">RECENT BUS ERRORS</div>
              <div v-for="(err, i) in stream.errors" :key="i" class="error-line text-xxs mb-1 pa-1 bg-error-lighten-5 rounded">
                {{ err.msg }}
              </div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col v-if="!loading && streams.length === 0" cols="12">
        <v-alert type="info" variant="tonal">
          No active video streams found.
        </v-alert>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import axios from 'axios';

const streams = ref([]);
const loading = ref(false);
let pollInterval = null;

const fetchStats = async () => {
  // Original guard was `if (loading.value && streams.value.length > 0)`,
  // which only blocked concurrent fetches when the list was already
  // populated. On first load streams is empty, so the guard let
  // overlapping polls through. Block unconditionally while a fetch
  // is in flight.
  if (loading.value) return;
  loading.value = true;
  try {
    const res = await axios.get('/api/admin/debug/streams');
    streams.value = res.data;
  } catch (e) {
    console.error(e);
  } finally {
    loading.value = false;
  }
};

const getStatusColor = (s) => {
  if (!s.active) return 'grey-darken-2';
  if (s.last_frame_seconds_ago > 5 || s.last_frame_seconds_ago < 0) return 'warning';
  if (s.state !== 'PLAYING') return 'blue-grey';
  return 'success';
};

const getStatusIcon = (s) => {
  if (!s.active) return 'mdi-video-off';
  if (s.last_frame_seconds_ago > 5 || s.last_frame_seconds_ago < 0) return 'mdi-video-wait';
  return 'mdi-video-check';
};

const getAgeColor = (age) => {
  if (age < 0 || age > 10) return 'error';
  if (age > 3) return 'warning';
  return 'success';
};

const getAgeTextColor = (age) => {
  if (age < 0 || age > 10) return 'text-error';
  if (age > 3) return 'text-warning';
  return 'text-success';
};

const formatUptime = (seconds) => {
  if (!seconds || seconds < 0) return '0s';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  
  let parts = [];
  if (h > 0) parts.push(`${h}h`);
  if (m > 0 || h > 0) parts.push(`${m}m`);
  parts.push(`${s}s`);
  return parts.join(' ');
};

onMounted(() => {
  fetchStats();
  pollInterval = setInterval(fetchStats, 2000); // 2s polling for "live" feel
});

onBeforeUnmount(() => {
  if (pollInterval) clearInterval(pollInterval);
});
</script>

<style scoped>
.text-xxs {
  font-size: 0.65rem;
  line-height: 1;
}
.error-line {
  font-family: monospace;
  word-break: break-all;
}
.uppercase {
  text-transform: uppercase;
}
</style>
