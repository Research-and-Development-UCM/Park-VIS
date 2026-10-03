<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <div class="d-flex align-center justify-space-between flex-wrap gap-4 mb-4">
          <div class="text-h5 font-weight-bold">System Diagnostics</div>
          <v-chip
            v-if="appVersion"
            color="primary"
            variant="tonal"
            class="font-weight-medium"
            prepend-icon="mdi-tag-outline"
          >
            <span>Parking Vis {{ appVersion }}</span>
          </v-chip>
        </div>
      </v-col>

      <v-col cols="12" md="6" lg="4">
        <v-card 
          to="/diagnostics/storage" 
          elevation="2" 
          rounded="lg" 
          class="fill-height pa-4 hover-card"
          border
        >
          <div class="d-flex align-center mb-4">
            <v-avatar color="indigo-lighten-5" rounded="lg" size="48">
              <v-icon color="indigo">mdi-database-cog</v-icon>
            </v-avatar>
            <div class="ml-4">
              <div class="text-h6">Storage Engine</div>
              <div class="text-caption text-grey">LMDB & SQLite status</div>
            </div>
          </div>
          <div class="text-body-2 text-grey-darken-1">
            Monitor blob storage health, record ranges, and manual maintenance tasks like pruning and vacuuming.
          </div>
        </v-card>
      </v-col>

      <v-col cols="12" md="6" lg="4">
        <v-card 
          to="/diagnostics/heap" 
          elevation="2" 
          rounded="lg" 
          class="fill-height pa-4 hover-card"
          border
        >
          <div class="d-flex align-center mb-4">
            <v-avatar color="error-lighten-5" rounded="lg" size="48">
              <v-icon color="error">mdi-memory</v-icon>
            </v-avatar>
            <div class="ml-4">
              <div class="text-h6">Memory Heap</div>
              <div class="text-caption text-grey">Python object profiling</div>
            </div>
          </div>
          <div class="text-body-2 text-grey-darken-1">
            Analyze live Python heap distribution to identify memory leaks and large object allocations.
          </div>
        </v-card>
      </v-col>

      <v-col cols="12" md="6" lg="4">
        <v-card 
          to="/diagnostics/streams" 
          elevation="2" 
          rounded="lg" 
          class="fill-height pa-4 hover-card"
          border
        >
          <div class="d-flex align-center mb-4">
            <v-avatar color="blue-grey-lighten-5" rounded="lg" size="48">
              <v-icon color="blue-grey">mdi-console-network</v-icon>
            </v-avatar>
            <div class="ml-4">
              <div class="text-h6">Network & Streams</div>
              <div class="text-caption text-grey">GStreamer pipeline metrics</div>
            </div>
          </div>
          <div class="text-body-2 text-grey-darken-1">
            Real-time monitoring of video pipeline states, FPS, drop rates, and GStreamer bus error logs.
          </div>
        </v-card>
      </v-col>

      <v-col cols="12" md="6" lg="4">
        <v-card 
          to="/diagnostics/logs" 
          elevation="2" 
          rounded="lg" 
          class="fill-height pa-4 hover-card"
          border
        >
          <div class="d-flex align-center mb-4">
            <v-avatar color="green-lighten-5" rounded="lg" size="48">
              <v-icon color="green">mdi-script-text-outline</v-icon>
            </v-avatar>
            <div class="ml-4">
              <div class="text-h6">System Logs</div>
              <div class="text-caption text-grey">Live console output</div>
            </div>
          </div>
          <div class="text-body-2 text-grey-darken-1">
            Stream real-time server logs with filtering by level or keyword. Essential for deep troubleshooting.
          </div>
        </v-card>
      </v-col>

      <v-col cols="12" md="6" lg="4">
        <v-card 
          to="/diagnostics/threads" 
          elevation="2" 
          rounded="lg" 
          class="fill-height pa-4 hover-card"
          border
        >
          <div class="d-flex align-center mb-4">
            <v-avatar color="deep-purple-lighten-5" rounded="lg" size="48">
              <v-icon color="deep-purple-accent-3">mdi-transit-connection-variant</v-icon>
            </v-avatar>
            <div class="ml-4">
              <div class="text-h6">Thread Inspector</div>
              <div class="text-caption text-grey">Python stack traces</div>
            </div>
          </div>
          <div class="text-body-2 text-grey-darken-1">
            Inspect active Python threads in real-time, view live execution frames, and identify hung operations or deadlocks.
          </div>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import axios from 'axios';

const appVersion = ref('');
const commitSha = ref('');

const versionPrefix = computed(() => {
  if (!appVersion.value) return '';
  if (commitSha.value && appVersion.value.endsWith(`-${commitSha.value}`)) {
    return appVersion.value.slice(0, -commitSha.value.length);
  }
  return appVersion.value;
});

onMounted(async () => {
  try {
    const res = await axios.get('/api/version');
    appVersion.value = res.data.version || '';
    commitSha.value = res.data.commit || '';
  } catch (e) {
    console.debug('Failed to fetch version info', e);
  }
});
</script>

<style scoped>
.hover-card {
  transition: all 0.2s ease-in-out;
}
.hover-card:hover {
  transform: translateY(-4px);
  border-color: var(--v-primary-base) !important;
  background-color: rgb(var(--v-theme-surface-variant));
}
.opacity-60 {
  opacity: 0.6;
}
</style>
