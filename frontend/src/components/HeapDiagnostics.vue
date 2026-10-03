<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <div class="text-h5 font-weight-bold mb-6 d-flex align-center">
          <v-icon color="error" class="mr-3">mdi-memory</v-icon>
          Heap & Memory Diagnostics
          <v-spacer></v-spacer>
          <v-btn color="warning" variant="tonal" class="mr-2" prepend-icon="mdi-broom" @click="runGC" :loading="gcLoading">Force GC</v-btn>
          <v-btn color="primary" variant="tonal" prepend-icon="mdi-refresh" @click="fetchStats" :loading="loading">Refresh</v-btn>
        </div>
      </v-col>

      <v-col cols="12" v-if="stats.tracing_enabled === false">
        <v-alert
          type="info"
          variant="elevated"
          border="start"
          icon="mdi-information"
          class="mb-6"
        >
          <div class="text-subtitle-1 font-weight-bold">Memory Profiling is Disabled</div>
          To enable allocation tracing and heap snapshots, restart the application with the <code>--profile-memory</code> flag.
          <div class="mt-2">
            <code class="bg-black pa-1 rounded">./run_backend --profile-memory</code>
          </div>
        </v-alert>
      </v-col>

      <!-- System Process Info -->
      <v-col cols="12" md="4">
        <v-card elevation="2" rounded="lg" class="fill-height">
          <v-toolbar color="white" density="comfortable" class="px-4">
            <v-icon start color="blue-grey">mdi-cpu-64-bit</v-icon>
            <v-toolbar-title class="text-subtitle-1 font-weight-bold">Process Info</v-toolbar-title>
          </v-toolbar>
          <v-divider></v-divider>
          <v-list class="pa-4">
            <v-list-item title="PID" :subtitle="stats.process?.pid"></v-list-item>
            <v-list-item title="Resident Set Size (RSS)" :subtitle="formatSize(stats.process?.rss_bytes)"></v-list-item>
            <v-list-item title="Virtual Memory Size (VMS)" :subtitle="formatSize(stats.process?.vms_bytes)"></v-list-item>
            <v-list-item title="CPU Usage" :subtitle="(stats.process?.cpu_percent || 0).toFixed(1) + '%'"></v-list-item>
          </v-list>
        </v-card>
      </v-col>

      <v-col cols="12" md="8">
        <v-card elevation="2" rounded="lg">
          <v-tabs v-model="tab" color="error" align-tabs="start">
            <v-tab value="types"><v-icon start>mdi-shape-outline</v-icon>Object Types</v-tab>
            <v-tab value="traces"><v-icon start>mdi-code-braces</v-icon>Allocation Traces</v-tab>
          </v-tabs>
          
          <v-divider></v-divider>

          <v-window v-model="tab">
            <!-- Pympler Object Types -->
            <v-window-item value="types">
              <div v-if="!loading && (!stats.heap || stats.heap.length === 0)" class="pa-10 text-center">
                <v-icon size="64" color="grey-lighten-1" class="mb-4">mdi-database-search</v-icon>
                <div class="text-h6 text-grey-darken-1 mb-2">Heap Scan Required</div>
                <div class="text-body-2 text-grey mb-6">
                  Detailed object analysis is disabled by default to prevent performance issues.<br>
                  This scan can take several seconds and will temporarily freeze the application.
                </div>
                <v-btn
                  color="error"
                  variant="elevated"
                  prepend-icon="mdi-magnify"
                  @click="fetchStats(true)"
                  :loading="loading"
                >
                  Analyze Full Heap
                </v-btn>
              </div>

              <v-data-table
                v-else
                :headers="typeHeaders"
                :items="stats.heap || []"
                density="compact"
                :loading="loading"
                class="elevation-0"
                :items-per-page="15"
              >
                <template #item.size_bytes="{ item }">
                  {{ formatSize(item.size_bytes) }}
                </template>
                <template #item.percent="{ item }">
                  <v-progress-linear
                    :model-value="(stats.process?.rss_bytes ? (item.size_bytes / stats.process.rss_bytes) * 100 : 0)"
                    color="error"
                    height="8"
                    rounded
                  ></v-progress-linear>
                </template>
              </v-data-table>
            </v-window-item>

            <!-- Tracemalloc Allocation Traces -->
            <v-window-item value="traces">
              <v-data-table
                :headers="traceHeaders"
                :items="stats.traces || []"
                density="compact"
                :loading="loading"
                class="elevation-0"
                :items-per-page="15"
              >
                <template #item.file="{ item }">
                  <div class="text-caption font-weight-bold text-primary">{{ shortenPath(item.file) }}</div>
                  <div class="text-xxs text-grey">Line: {{ item.line }}</div>
                </template>
                <template #item.size_bytes="{ item }">
                  {{ formatSize(item.size_bytes) }}
                </template>
                <template #item.count="{ item }">
                  {{ item.count.toLocaleString() }}
                </template>
              </v-data-table>
            </v-window-item>
          </v-window>
        </v-card>
      </v-col>

      <v-col cols="12">
        <v-alert type="warning" variant="tonal" border="start" icon="mdi-alert">
          <div class="text-subtitle-2 font-weight-bold">Profiling Overhead</div>
          Capturing heap statistics and stack traces requires scanning every object in memory. 
          <b>The application will be temporarily unresponsive</b> during the refresh.
        </v-alert>
      </v-col>
    </v-row>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color">
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import axios from 'axios';

const stats = ref({});
const loading = ref(false);
const gcLoading = ref(false);
const tab = ref('types');
const snackbar = ref({ show: false, text: '', color: 'success' });

const typeHeaders = [
  { title: 'Object Type', key: 'type' },
  { title: 'Count', key: 'count', align: 'end' },
  { title: 'Total Size', key: 'size_bytes', align: 'end' },
  { title: 'RSS %', key: 'percent', width: '120px' }
];

const traceHeaders = [
  { title: 'Location (File & Line)', key: 'file' },
  { title: 'Allocations', key: 'count', align: 'end' },
  { title: 'Total Memory', key: 'size_bytes', align: 'end' }
];

const fetchStats = async (includeHeap = false) => {
  loading.value = true;
  try {
    const res = await axios.get(`/api/admin/debug/heap?include_heap=${includeHeap}`);
    if (res.data.error) {
      snackbar.value = { show: true, text: res.data.error, color: 'error' };
    } else {
      stats.value = res.data;
    }
  } catch (e) {
    snackbar.value = { show: true, text: 'Failed to fetch heap stats', color: 'error' };
  } finally {
    loading.value = false;
  }
};

const runGC = async () => {
  gcLoading.value = true;
  try {
    const res = await axios.post('/api/admin/debug/gc');
    snackbar.value = { show: true, text: res.data.message, color: 'success' };
    await fetchStats();
  } catch (e) {
    snackbar.value = { show: true, text: 'GC failed', color: 'error' };
  } finally {
    gcLoading.value = false;
  }
};

const formatSize = (bytes) => {
  if (!bytes) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const shortenPath = (path) => {
  if (!path) return '';
  const parts = path.split('/');
  // If it's a site-packages path, just show the library name
  const spIdx = parts.indexOf('site-packages');
  if (spIdx !== -1 && parts.length > spIdx + 1) {
    return `[lib] ${parts.slice(spIdx + 1).join('/')}`;
  }
  // Otherwise show the last 3 parts
  return parts.slice(-3).join('/');
};

onMounted(fetchStats);
</script>

<style scoped>
.text-xxs {
  font-size: 0.7rem;
}
</style>
