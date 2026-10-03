<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <div class="text-h5 font-weight-bold mb-6 d-flex align-center">
          <v-icon color="primary" class="mr-3">mdi-database-cog</v-icon>
          Storage Engine Diagnostics
          <v-spacer></v-spacer>
          <v-btn color="primary" variant="tonal" prepend-icon="mdi-refresh" @click="fetchStats" :loading="loading" class="mr-2">Refresh</v-btn>
          <v-btn color="indigo" variant="tonal" prepend-icon="mdi-database-search" @click="openBrowser">Browse Raw Database</v-btn>
        </div>
      </v-col>

      <!-- Disk Space Mount Card -->
      <v-col cols="12" v-if="stats.disk">
        <v-card elevation="2" rounded="lg" class="mb-4">
          <v-toolbar color="primary" density="comfortable" class="px-4">
            <v-icon start color="white">mdi-harddisk</v-icon>
            <v-toolbar-title class="text-subtitle-1 font-weight-bold text-white">Physical Storage Mount</v-toolbar-title>
          </v-toolbar>
          <v-divider></v-divider>
          <v-card-text class="pa-6">
            <v-row align="center">
              <v-col cols="12" md="8">
                <div class="d-flex justify-space-between mb-2">
                  <span class="text-body-2 font-weight-bold text-grey-darken-3">Disk Space Usage Breakdown</span>
                  <span class="text-body-2 font-weight-bold text-primary">{{ usedPct }}% Total Used</span>
                </div>
                
                <!-- Stacked Progress Bar -->
                <div class="d-flex rounded-pill overflow-hidden bg-grey-lighten-3 mb-2 border" style="height: 18px;">
                  <div v-if="otherPct > 0" class="bg-blue-grey-lighten-1" :style="{ width: otherPct + '%' }" title="Other Files / OS"></div>
                  <div v-if="snapshotsPct > 0" class="bg-indigo" :style="{ width: snapshotsPct + '%' }" title="Snapshots"></div>
                  <div v-if="cropsPct > 0" class="bg-deep-orange" :style="{ width: cropsPct + '%' }" title="Crops"></div>
                  <div v-if="freePct > 0" class="bg-grey-lighten-2" :style="{ width: freePct + '%' }" title="Free Space"></div>
                </div>

                <!-- Legend / Labels -->
                <div class="d-flex flex-wrap mt-3 text-caption align-center justify-space-between" style="gap: 12px 16px;">
                  <div class="d-flex align-center">
                    <span class="d-inline-block rounded-circle bg-blue-grey-lighten-1 mr-1" style="width: 8px; height: 8px;"></span>
                    <span>Other Data: <b>{{ formatSize(otherBytes) }}</b> ({{ otherPct }}%)</span>
                  </div>
                  <div class="d-flex align-center">
                    <span class="d-inline-block rounded-circle bg-indigo mr-1" style="width: 8px; height: 8px;"></span>
                    <span>Snapshots: <b>{{ formatSize(stats.snapshots?.summary?.total_size_bytes) }}</b> ({{ snapshotsPct }}%)</span>
                  </div>
                  <div class="d-flex align-center">
                    <span class="d-inline-block rounded-circle bg-deep-orange mr-1" style="width: 8px; height: 8px;"></span>
                    <span>Crops: <b>{{ formatSize(stats.crops?.summary?.total_size_bytes) }}</b> ({{ cropsPct }}%)</span>
                  </div>
                  <div class="d-flex align-center">
                    <span class="d-inline-block rounded-circle bg-grey-lighten-2 mr-1" style="width: 8px; height: 8px; border: 1px solid rgba(0,0,0,0.1)"></span>
                    <span>Free Space: <b>{{ formatSize(freeBytes) }}</b> ({{ freePct }}%)</span>
                  </div>
                </div>
              </v-col>
              <v-col cols="12" md="4" class="border-s pl-6">
                <div class="text-caption font-weight-bold text-error mb-1 d-flex align-center">
                  <v-icon start icon="mdi-alert-circle" size="small" color="error" class="mr-1"></v-icon>
                  EMERGENCY DISK GUARD
                </div>
                <div class="text-caption text-grey-darken-2" style="line-height: 1.4;">
                  Emergency auto-eviction triggers when the storage partition drops below <b>1500 MB</b> of free space. In this state, the system automatically purges the oldest database chunks regardless of retention to prevent disk exhaustion.
                </div>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>

      <!-- SQLite Meta Stats -->
      <v-col cols="12" md="4">
        <v-card elevation="2" rounded="lg">
          <v-toolbar color="white" density="comfortable" class="px-4">
            <v-icon start color="blue-grey">mdi-database</v-icon>
            <v-toolbar-title class="text-subtitle-1 font-weight-bold">SQLite (Metadata)</v-toolbar-title>
          </v-toolbar>
          <v-divider></v-divider>
          <v-list class="pa-4">
            <v-list-item title="File Size" :subtitle="formatSize(stats.sqlite?.size_bytes)"></v-list-item>
            <v-list-item title="Scan Records" :subtitle="stats.sqlite?.scans_count"></v-list-item>
            <v-list-item title="Event Records" :subtitle="stats.sqlite?.events_count"></v-list-item>
          </v-list>
        </v-card>
      </v-col>

      <!-- Snapshots Store -->
      <v-col cols="12" md="4">
        <v-card elevation="2" rounded="lg">
          <v-toolbar color="white" density="comfortable" class="px-4">
            <v-icon start color="indigo">mdi-camera</v-icon>
            <v-toolbar-title class="text-subtitle-1 font-weight-bold">Snapshots</v-toolbar-title>
          </v-toolbar>
          <v-divider></v-divider>
          <v-list class="pa-4">
            <v-list-item title="Entries" :subtitle="stats.snapshots?.summary?.total_entries"></v-list-item>
            <v-list-item title="Used Bytes" :subtitle="formatSize(stats.snapshots?.summary?.total_size_bytes)"></v-list-item>
            <v-list-item title="Chunks" :subtitle="stats.snapshots?.summary?.num_chunks"></v-list-item>
            <v-list-item title="Max Size" :subtitle="stats.snapshots?.config?.max_size_gb + ' GB'"></v-list-item>
            <v-list-item title="Chunk Size" :subtitle="stats.snapshots?.config?.chunk_size_mb + ' MB'"></v-list-item>
            <v-list-item v-if="stats.snapshots?.range?.oldest?.id" class="pt-0">
              <v-chip size="x-small" label color="blue-grey-lighten-4" variant="flat"
                      class="mr-1 text-blue-grey-darken-3 cursor-pointer"
                      @click="viewImage(stats.snapshots.range.oldest.id, false)">
                <v-icon start icon="mdi-clock-start" size="x-small"></v-icon>
                Oldest ID {{ stats.snapshots.range.oldest.id }}: {{ formatDateTime(stats.snapshots.range.oldest.timestamp) }}
                <v-icon end icon="mdi-open-in-new" size="x-small" class="ml-1"></v-icon>
              </v-chip>
            </v-list-item>
            <v-list-item v-if="stats.snapshots?.range?.newest?.id" class="pt-0">
              <v-chip size="x-small" label color="blue-grey-lighten-4" variant="flat"
                      class="text-blue-grey-darken-3 cursor-pointer"
                      @click="viewImage(stats.snapshots.range.newest.id, false)">
                <v-icon start icon="mdi-clock-end" size="x-small"></v-icon>
                Newest ID {{ stats.snapshots.range.newest.id }}: {{ formatDateTime(stats.snapshots.range.newest.timestamp) }}
                <v-icon end icon="mdi-open-in-new" size="x-small" class="ml-1"></v-icon>
              </v-chip>
            </v-list-item>
          </v-list>
        </v-card>
      </v-col>

      <!-- Crops Store -->
      <v-col cols="12" md="4">
        <v-card elevation="2" rounded="lg">
          <v-toolbar color="white" density="comfortable" class="px-4">
            <v-icon start color="deep-orange">mdi-crop</v-icon>
            <v-toolbar-title class="text-subtitle-1 font-weight-bold">Crops</v-toolbar-title>
          </v-toolbar>
          <v-divider></v-divider>
          <v-list class="pa-4">
            <v-list-item title="Entries" :subtitle="stats.crops?.summary?.total_entries"></v-list-item>
            <v-list-item title="Used Bytes" :subtitle="formatSize(stats.crops?.summary?.total_size_bytes)"></v-list-item>
            <v-list-item title="Chunks" :subtitle="stats.crops?.summary?.num_chunks"></v-list-item>
            <v-list-item title="Max Size" :subtitle="stats.crops?.config?.max_size_gb + ' GB'"></v-list-item>
            <v-list-item title="Chunk Size" :subtitle="stats.crops?.config?.chunk_size_mb + ' MB'"></v-list-item>
            <v-list-item v-if="stats.crops?.range?.oldest?.id" class="pt-0">
              <v-chip size="x-small" label color="blue-grey-lighten-4" variant="flat"
                      class="mr-1 text-blue-grey-darken-3 cursor-pointer"
                      @click="viewImage(stats.crops.range.oldest.id, true)">
                <v-icon start icon="mdi-clock-start" size="x-small"></v-icon>
                Oldest ID {{ stats.crops.range.oldest.id }}: {{ formatDateTime(stats.crops.range.oldest.timestamp) }}
                <v-icon end icon="mdi-open-in-new" size="x-small" class="ml-1"></v-icon>
              </v-chip>
            </v-list-item>
            <v-list-item v-if="stats.crops?.range?.newest?.id" class="pt-0">
              <v-chip size="x-small" label color="blue-grey-lighten-4" variant="flat"
                      class="text-blue-grey-darken-3 cursor-pointer"
                      @click="viewImage(stats.crops.range.newest.id, true)">
                <v-icon start icon="mdi-clock-end" size="x-small"></v-icon>
                Newest ID {{ stats.crops.range.newest.id }}: {{ formatDateTime(stats.crops.range.newest.timestamp) }}
                <v-icon end icon="mdi-open-in-new" size="x-small" class="ml-1"></v-icon>
              </v-chip>
            </v-list-item>
          </v-list>
        </v-card>
      </v-col>

      <!-- Maintenance -->
      <v-col cols="12">
        <v-card elevation="2" rounded="lg" border="error">
          <v-toolbar color="error-lighten-5" density="comfortable" class="px-4">
            <v-icon start color="error">mdi-alert-circle-outline</v-icon>
            <v-toolbar-title class="text-subtitle-1 font-weight-bold text-error">Maintenance & Recovery</v-toolbar-title>
          </v-toolbar>
          <v-divider></v-divider>
          <v-card-text class="pa-6">
            <div class="d-flex align-center mb-6">
              <div class="mr-6">
                <div class="text-subtitle-2 font-weight-bold mb-1">Prune Old Records</div>
                <div class="text-body-2 text-grey-darken-1">
                  Instantly execute the system's retention policy.
                  This permanently deletes snapshots, crops, and records older than configured limits.
                  Pruning also removes orphaned LMDB entries via rollinglmdb's size enforcement.
                </div>
              </div>
              <v-spacer></v-spacer>
              <v-btn color="warning" variant="outlined" :loading="pruning" @click="doPrune" prepend-icon="mdi-clock-fast">
                Prune Now
              </v-btn>
            </div>
          </v-card-text>
        </v-card>
      </v-col>

      <!-- Architecture alert -->
      <v-col cols="12">
        <v-alert type="info" variant="tonal" class="mb-0">
          <div class="text-subtitle-2 font-weight-bold">Storage Architecture</div>
          Metadata and state are stored in <b>SQLite</b> for relational integrity.
          High-volume binary blobs are stored in time-rolled <b>LMDB</b> chunks for high-performance I/O.
        </v-alert>
      </v-col>
    </v-row>

    <!-- ============================================================ -->
    <!-- Browse Raw DB Dialog (inlined — no separate component) -->
    <!-- ============================================================ -->
    <v-dialog v-model="browseDialog" max-width="1000" max-height="90vh" scrollable>
      <v-card>
        <v-toolbar color="indigo-darken-4" density="compact">
          <v-icon start color="white" class="ml-4">mdi-database-search</v-icon>
          <v-toolbar-title class="text-white">Browse Raw Database</v-toolbar-title>
          <v-spacer></v-spacer>
          <v-btn icon variant="text" color="white" @click="closeBrowser"><v-icon>mdi-close</v-icon></v-btn>
        </v-toolbar>
        <v-tabs v-model="browseTab" color="indigo" class="px-4 pt-2" @update:model-value="onTabChange">
          <v-tab value="snapshots">Snapshots</v-tab>
          <v-tab value="crops">Crops</v-tab>
        </v-tabs>
        <v-divider></v-divider>
        <v-card-text>
          <v-alert v-if="browseError" type="error" variant="tonal" dense class="mb-3">{{ browseError }}</v-alert>

          <!-- Chunk list -->
          <v-table v-if="!selectedChunk" density="compact" hover class="rounded-lg">
            <thead>
              <tr>
                <th class="text-left text-caption font-weight-bold">Filename</th>
                <th class="text-left text-caption font-weight-bold">First Timestamp</th>
                <th class="text-left text-caption font-weight-bold">Last Timestamp</th>
                <th class="text-left text-caption font-weight-bold">Entries</th>
                <th class="text-left text-caption font-weight-bold">Size</th>
                <th class="text-left text-caption font-weight-bold">Map Size</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(chunk, idx) in chunks" :key="chunk.path"
                  class="cursor-pointer" @click="selectChunk(chunk, idx)">
                <td><span class="text-body-2">{{ chunk.filename }}</span></td>
                <td><span class="text-caption">{{ formatDateTime(chunk.first_timestamp) }}</span></td>
                <td><span class="text-caption">{{ formatDateTime(chunk.last_timestamp) }}</span></td>
                <td>{{ chunk.num_entries }}</td>
                <td>{{ formatSize(chunk.size_bytes) }}</td>
                <td>{{ formatSize(chunk.map_size_bytes) }}</td>
              </tr>
              <tr v-if="!chunks.length && !loadingChunks">
                <td colspan="6" class="text-center text-grey">No chunks found</td>
              </tr>
            </tbody>
            <caption v-if="loadingChunks" class="text-center pa-2">
              <v-progress-linear indeterminate></v-progress-linear>
            </caption>
          </v-table>

          <!-- Key list for a selected chunk -->
          <div v-else>
            <div class="d-flex align-center mb-3">
              <v-btn size="small" variant="text" prepend-icon="mdi-arrow-left" @click="selectedChunk = null">Back</v-btn>
              <v-chip size="small" color="indigo" variant="tonal" class="ml-2">
                {{ selectedChunk.filename }}
              </v-chip>
              <v-chip size="small" variant="outlined" class="ml-2">
                {{ filteredKeys.length }} / {{ keys.length }} key(s)
              </v-chip>
            </div>

            <v-text-field
              v-model="keyFilter"
              prepend-inner-icon="mdi-magnify"
              label="Filter keys by ID"
              density="compact"
              hide-details
              clearable
              class="mb-2"
              @update:model-value="keysPage = 1"
            ></v-text-field>

            <div v-if="loadingKeys" class="pa-4">
              <v-progress-linear indeterminate></v-progress-linear>
            </div>

            <template v-else-if="filteredKeys.length">
              <v-table density="compact" hover class="rounded-lg"
                       style="max-height: 400px; overflow-y: auto">
                <thead>
                  <tr>
                    <th class="text-left text-caption font-weight-bold">Key (SQLite ID)</th>
                    <th class="text-left text-caption font-weight-bold">Size</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in paginatedKeys" :key="item.key"
                      class="cursor-pointer" @click="handleViewBlob({ dbName: browseTab, key: item.key })">
                    <td><span class="text-body-2 text-primary">{{ item.key }}</span></td>
                    <td>{{ formatSize(item.size_bytes) }}</td>
                  </tr>
                </tbody>
              </v-table>

              <div class="d-flex align-center justify-end pa-2 text-caption text-grey">
                <v-btn size="x-small" variant="text" icon
                       :disabled="keysPage <= 1"
                       @click="keysPage--">
                  <v-icon>mdi-chevron-left</v-icon>
                </v-btn>
                <span class="mx-2">Page {{ keysPage }} of {{ totalPages }}</span>
                <v-btn size="x-small" variant="text" icon
                       :disabled="keysPage >= totalPages"
                       @click="keysPage++">
                  <v-icon>mdi-chevron-right</v-icon>
                </v-btn>
                <v-select
                  v-model="keysPerPage"
                  :items="[50, 100, 250, 500]"
                  density="compact"
                  hide-details
                  variant="plain"
                  class="ml-4"
                  style="max-width: 100px"
                  @update:model-value="keysPage = 1"
                ></v-select>
              </div>
            </template>

            <v-alert v-else type="info" variant="tonal" dense>No keys match filter.</v-alert>
          </div>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="5000">
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import axios from 'axios';

// --- main stats ---
const stats = ref({});
const snapshotsPct = computed(() => {
  if (!stats.value?.disk?.total_bytes) return 0;
  return Math.round((stats.value.snapshots?.summary?.total_size_bytes || 0) / stats.value.disk.total_bytes * 1000) / 10;
});

const cropsPct = computed(() => {
  if (!stats.value?.disk?.total_bytes) return 0;
  return Math.round((stats.value.crops?.summary?.total_size_bytes || 0) / stats.value.disk.total_bytes * 1000) / 10;
});

const usedPct = computed(() => {
  if (!stats.value?.disk?.total_bytes) return 0;
  return Math.round(stats.value.disk.used_bytes / stats.value.disk.total_bytes * 1000) / 10;
});

const otherBytes = computed(() => {
  const totUsed = stats.value?.disk?.used_bytes || 0;
  const snap = stats.value?.snapshots?.summary?.total_size_bytes || 0;
  const crop = stats.value?.crops?.summary?.total_size_bytes || 0;
  return Math.max(0, totUsed - snap - crop);
});

const otherPct = computed(() => {
  if (!stats.value?.disk?.total_bytes) return 0;
  return Math.round(otherBytes.value / stats.value.disk.total_bytes * 1000) / 10;
});

const freeBytes = computed(() => {
  if (!stats.value?.disk?.total_bytes) return 0;
  const total = stats.value.disk.total_bytes;
  const used = stats.value.disk.used_bytes;
  return Math.max(0, total - used);
});

const freePct = computed(() => {
  if (!stats.value?.disk?.total_bytes) return 0;
  return Math.round((100 - usedPct.value) * 10) / 10;
});
const loading = ref(false);
const pruning = ref(false);
const snackbar = ref({ show: false, text: '', color: 'success' });

// --- browser dialog ---
const browseDialog = ref(false);
const browseTab = ref('snapshots');
const chunks = ref([]);
const loadingChunks = ref(false);
const browseError = ref('');
const selectedChunk = ref(null);
const keys = ref([]);
const loadingKeys = ref(false);
const keyFilter = ref('');
const keysPage = ref(1);
const keysPerPage = ref(100);
const totalKeys = ref(0);

const filteredKeys = computed(() => {
  const all = keys.value;
  if (!keyFilter.value) return all;
  const q = keyFilter.value.toLowerCase();
  return all.filter(item => item.key.toLowerCase().includes(q));
});

const totalPages = computed(() => Math.max(1, Math.ceil(totalKeys.value / keysPerPage.value)));

const paginatedKeys = computed(() => filteredKeys.value);

function openBrowser() {
  selectedChunk.value = null;
  keys.value = [];
  browseDialog.value = true;
  fetchChunks('snapshots');
}

function closeBrowser() {
  browseDialog.value = false;
}

function onTabChange(val) {
  selectedChunk.value = null;
  keys.value = [];
  fetchChunks(val);
}

async function fetchChunks(dbName) {
  loadingChunks.value = true;
  browseError.value = '';
  try {
    const res = await axios.get(`/api/admin/storage/chunks?db=${dbName}`);
    chunks.value = res.data.chunks;
  } catch (e) {
    browseError.value = 'Failed to load chunks';
  } finally {
    loadingChunks.value = false;
  }
}

function selectChunk(chunk, idx) {
  selectedChunk.value = chunk;
  fetchKeys(idx);
}

async function loadKeysPage() {
  const idx = chunks.value.indexOf(selectedChunk.value);
  if (idx < 0) return;
  loadingKeys.value = true;
  keyFilter.value = '';
  try {
    const offset = (keysPage.value - 1) * keysPerPage.value;
    const res = await axios.get(`/api/admin/storage/chunks/${idx}/keys`, {
      params: { db: browseTab.value, offset, limit: keysPerPage.value },
    });
    keys.value = res.data.keys;
    totalKeys.value = res.data.total_count;
  } catch (e) {
    browseError.value = 'Failed to load keys';
  } finally {
    loadingKeys.value = false;
  }
}

async function fetchKeys(idx) {
  keysPage.value = 1;
  keys.value = [];
  totalKeys.value = 0;
  await loadKeysPage();
}

watch(keysPage, () => {
  if (selectedChunk.value) loadKeysPage();
});
watch(keysPerPage, () => {
  keysPage.value = 1;
  if (selectedChunk.value) loadKeysPage();
});

// --- main stats ---
const fetchStats = async () => {
  loading.value = true;
  try {
    const res = await axios.get('/api/admin/storage/stats');
    stats.value = res.data;
  } catch (e) {
    console.error(e);
  } finally {
    loading.value = false;
  }
};

const doPrune = async () => {
  pruning.value = true;
  try {
    const res = await axios.post('/api/admin/storage/prune');
    snackbar.value = { show: true, text: res.data.message, color: 'success' };
    await fetchStats();
  } catch (e) {
    snackbar.value = { show: true, text: 'Prune task failed', color: 'error' };
  } finally {
    pruning.value = false;
  }
};

const viewImage = (id, isCrop = false) => {
  if (!id) return;
  const token = localStorage.getItem('token');
  const url = isCrop
    ? `/api/events/${id}/crop?token=${token}`
    : `/api/image?scan_id=${id}&token=${token}`;
  window.open(url, '_blank');
};

const handleViewBlob = async ({ dbName, key }) => {
  try {
    const token = localStorage.getItem('token');
    const res = await axios.get(`/api/admin/storage/blob/${encodeURIComponent(key)}`, {
      params: { db: dbName },
      responseType: 'blob',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    const url = URL.createObjectURL(res.data);
    window.open(url, '_blank');
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (e) {
    snackbar.value = { show: true, text: 'Failed to load blob', color: 'error' };
  }
};

const formatSize = (bytes) => {
  if (!bytes) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const formatDateTime = (ts) => {
  if (!ts) return 'N/A';
  const utcStr = ts.endsWith('Z') ? ts : ts + 'Z';
  return new Intl.DateTimeFormat('default', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  }).format(new Date(utcStr));
};

onMounted(fetchStats);
</script>

<style scoped>
.cursor-pointer { cursor: pointer; }
</style>
