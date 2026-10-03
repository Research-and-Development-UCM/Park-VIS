<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <div class="text-h5 font-weight-bold mb-6 d-flex align-center flex-wrap ga-3">
          <v-icon color="deep-purple-accent-3" class="mr-1">mdi-transit-connection-variant</v-icon>
          <span>Thread Inspector & Stack Traces</span>
          <v-chip v-if="threads.length" color="deep-purple" variant="tonal" class="ml-2 font-weight-bold">
            {{ threads.length }} Active Threads
          </v-chip>
          <v-spacer></v-spacer>
          <v-btn
            color="secondary"
            variant="tonal"
            prepend-icon="mdi-content-copy"
            class="mr-2"
            :disabled="loading || !threads.length"
            @click="copyAllTraces"
          >
            Copy All Traces
          </v-btn>
          <v-btn
            color="primary"
            variant="tonal"
            prepend-icon="mdi-refresh"
            :loading="loading"
            @click="fetchThreads"
          >
            Refresh
          </v-btn>
        </div>
      </v-col>

      <!-- Search & Filters -->
      <v-col cols="12">
        <v-card elevation="2" rounded="lg" class="pa-4 mb-4">
          <v-row align="center" dense>
            <v-col cols="12" sm="7" md="6">
              <v-text-field
                v-model="searchQuery"
                label="Filter by thread name, file, or function"
                prepend-inner-icon="mdi-magnify"
                variant="outlined"
                density="compact"
                hide-details
                clearable
              ></v-text-field>
            </v-col>
            <v-col cols="12" sm="5" md="6" class="d-flex align-center justify-sm-end ga-3">
              <v-switch
                v-model="hideIdleDaemons"
                label="Hide idle daemons"
                density="compact"
                color="primary"
                hide-details
              ></v-switch>
              <v-btn
                size="small"
                variant="outlined"
                color="deep-purple"
                :prepend-icon="allExpanded ? 'mdi-collapse-all' : 'mdi-expand-all'"
                @click="toggleExpandAll"
              >
                {{ allExpanded ? 'Collapse All' : 'Expand All' }}
              </v-btn>
            </v-col>
          </v-row>
        </v-card>
      </v-col>

      <!-- Thread Cards / Expansion Panels -->
      <v-col cols="12">
        <v-card elevation="2" rounded="lg">
          <v-toolbar color="white" density="comfortable" class="px-4">
            <v-icon start color="deep-purple">mdi-format-list-bulleted-type</v-icon>
            <v-toolbar-title class="text-subtitle-1 font-weight-bold">
              Thread Execution Frames ({{ filteredThreads.length }})
            </v-toolbar-title>
            <span v-if="lastUpdated" class="text-caption text-grey mr-2">
              Captured: {{ lastUpdated }}
            </span>
          </v-toolbar>
          <v-divider></v-divider>

          <div v-if="loading && !threads.length" class="pa-10 text-center">
            <v-progress-circular indeterminate color="primary" size="48"></v-progress-circular>
            <div class="text-caption text-grey mt-3">Inspecting active Python threads...</div>
          </div>

          <div v-else-if="!filteredThreads.length" class="pa-10 text-center text-grey">
            <v-icon size="48" class="mb-2">mdi-check-circle-outline</v-icon>
            <div>No matching threads found.</div>
          </div>

          <v-expansion-panels
            v-else
            v-model="expandedPanels"
            multiple
            variant="accordion"
            class="thread-panels"
          >
            <v-expansion-panel
              v-for="t in filteredThreads"
              :key="t.ident"
              :value="t.ident"
              class="border-b"
            >
              <v-expansion-panel-title class="py-3">
                <div class="d-flex align-center flex-wrap ga-2 w-100 mr-4">
                  <!-- Status icon -->
                  <v-avatar
                    size="32"
                    :color="t.is_current ? 'success-lighten-5' : 'deep-purple-lighten-5'"
                    class="mr-2"
                  >
                    <v-icon
                      size="18"
                      :color="t.is_current ? 'success' : 'deep-purple'"
                    >
                      {{ t.is_current ? 'mdi-lightning-bolt' : 'mdi-code-braces' }}
                    </v-icon>
                  </v-avatar>

                  <span class="font-weight-bold text-body-1">{{ t.name }}</span>
                  <span class="text-caption text-grey font-mono">(ID: {{ t.ident }})</span>

                  <v-chip v-if="t.is_current" size="x-small" color="success" class="font-weight-bold">
                    CURRENT
                  </v-chip>
                  <v-chip v-if="t.name === 'MainThread'" size="x-small" color="primary" class="font-weight-bold">
                    MAIN
                  </v-chip>
                  <v-chip v-if="t.is_daemon" size="x-small" variant="tonal" color="grey">
                    DAEMON
                  </v-chip>

                  <v-spacer></v-spacer>

                  <!-- Top execution summary line -->
                  <div v-if="t.top_frame" class="text-caption text-grey-darken-1 text-truncate" style="max-width: 480px;">
                    <span class="font-weight-medium font-mono text-deep-purple">{{ formatFileName(t.top_frame.file) }}:{{ t.top_frame.line }}</span>
                    <span class="ml-1 text-grey font-mono">({{ t.top_frame.func }})</span>
                    <span v-if="t.top_frame.code" class="ml-2 font-mono text-grey-darken-3">→ {{ t.top_frame.code }}</span>
                  </div>
                </div>
              </v-expansion-panel-title>

              <v-expansion-panel-text class="bg-grey-lighten-5 pa-4">
                <div class="d-flex align-center justify-space-between mb-3 flex-wrap ga-2">
                  <div class="text-subtitle-2 font-weight-bold text-grey-darken-3 d-flex align-center ga-2">
                    <span>Call Stack ({{ t.stack.length }} frames)</span>
                    <v-chip size="x-small" variant="tonal" color="deep-purple">
                      Top: {{ t.top_frame ? t.top_frame.func + '()' : 'N/A' }}
                    </v-chip>
                  </div>
                  <div class="d-flex ga-2">
                    <v-btn
                      size="small"
                      variant="outlined"
                      color="secondary"
                      prepend-icon="mdi-content-copy"
                      @click.stop="copySingleTrace(t)"
                    >
                      Copy Backtrace
                    </v-btn>
                  </div>
                </div>

                <!-- Call stack frames -->
                <div class="stack-container pa-3 rounded bg-grey-darken-4 text-white">
                  <div
                    v-for="(frame, idx) in t.stack"
                    :key="idx"
                    class="frame-line py-2 font-mono text-caption"
                    :class="{ 'app-code-frame': isAppFrame(frame.file) }"
                  >
                    <div class="d-flex align-center justify-space-between">
                      <div>
                        <span class="font-weight-bold" :class="isAppFrame(frame.file) ? 'text-amber-accent-2' : 'text-primary-lighten-2'">
                          #{{ idx + 1 }}
                        </span>
                        <span class="ml-2" :class="isAppFrame(frame.file) ? 'text-amber-lighten-4 font-weight-bold' : 'text-cyan-lighten-3'">
                          {{ frame.file }}:{{ frame.line }}
                        </span>
                        <span class="ml-2 text-yellow-lighten-3 font-weight-bold">
                          in {{ frame.func }}()
                        </span>
                      </div>
                      <v-chip v-if="isAppFrame(frame.file)" size="x-small" color="amber" variant="tonal" class="ml-2">
                        APP
                      </v-chip>
                    </div>
                    <div v-if="frame.code" class="code-snippet ml-4 mt-1 text-light-green-accent-2">
                      {{ frame.code }}
                    </div>
                  </div>
                </div>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </v-card>
      </v-col>
    </v-row>

    <!-- Copy Feedback Snackbar -->
    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="2500">
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import axios from 'axios';

const threads = ref([]);
const loading = ref(false);
const searchQuery = ref('');
const hideIdleDaemons = ref(false);
const expandedPanels = ref([]);
const lastUpdated = ref('');
const snackbar = ref({ show: false, text: '', color: 'success' });

const IDLE_DAEMON_NAMES = [
  'watchfiles-watcher',
  'anyio-worker',
  'ThreadPoolExecutor',
  'queue-listener'
];

const fetchThreads = async () => {
  loading.value = true;
  try {
    const res = await axios.get('/api/admin/debug/threads');
    threads.value = res.data.threads || [];
    lastUpdated.value = new Date().toLocaleTimeString();
  } catch (e) {
    console.error('Failed to load thread diagnostics:', e);
    snackbar.value = {
      show: true,
      text: e.response?.data?.detail || 'Failed to fetch thread stack traces',
      color: 'error'
    };
  } finally {
    loading.value = false;
  }
};

const filteredThreads = computed(() => {
  return threads.value.filter(t => {
    // Hide idle daemons filter
    if (hideIdleDaemons.value && t.is_daemon && !t.is_current && t.name !== 'MainThread') {
      if (IDLE_DAEMON_NAMES.some(name => t.name.includes(name))) {
        return false;
      }
    }

    if (!searchQuery.value) return true;
    const q = searchQuery.value.toLowerCase();
    
    // Check name
    if (t.name.toLowerCase().includes(q)) return true;
    if (String(t.ident).includes(q)) return true;

    // Check stack frames
    return t.stack.some(f => 
      f.file.toLowerCase().includes(q) ||
      f.func.toLowerCase().includes(q) ||
      (f.code && f.code.toLowerCase().includes(q))
    );
  });
});

const allExpanded = computed(() => {
  if (!filteredThreads.value.length) return false;
  return expandedPanels.value.length === filteredThreads.value.length;
});

const toggleExpandAll = () => {
  if (allExpanded.value) {
    expandedPanels.value = [];
  } else {
    expandedPanels.value = filteredThreads.value.map(t => t.ident);
  }
};

const isAppFrame = (filePath) => {
  if (!filePath) return false;
  return filePath.includes('backend/') || filePath.includes('rollinglmdb/') || filePath.includes('main.py');
};

const formatFileName = (path) => {
  if (!path) return '';
  const parts = path.split(/[/\\]/);
  return parts.slice(-2).join('/');
};

const copySingleTrace = (thread) => {
  const header = `Thread "${thread.name}" (ID: ${thread.ident}, Daemon: ${thread.is_daemon}):\n`;
  const text = header + thread.formatted_traceback;
  navigator.clipboard.writeText(text);
  snackbar.value = {
    show: true,
    text: `Copied backtrace for ${thread.name} to clipboard`,
    color: 'success'
  };
};

const copyAllTraces = () => {
  let dump = `=== PARK VIS THREAD STACK DUMP (${new Date().toISOString()}) ===\n`;
  dump += `Total Active Threads: ${threads.value.length}\n\n`;

  for (const t of threads.value) {
    dump += `------------------------------------------------------------\n`;
    dump += `Thread: ${t.name} (ID: ${t.ident}, Daemon: ${t.is_daemon}, Current: ${t.is_current})\n`;
    dump += t.formatted_traceback + '\n';
  }

  navigator.clipboard.writeText(dump);
  snackbar.value = {
    show: true,
    text: `Copied backtraces for all ${threads.value.length} threads to clipboard`,
    color: 'success'
  };
};

onMounted(fetchThreads);
</script>

<style scoped>
.font-mono {
  font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace !important;
}

.stack-container {
  max-height: 480px;
  overflow-y: auto;
  font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
  font-size: 0.82rem;
  line-height: 1.4;
}

.frame-line {
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.frame-line:last-child {
  border-bottom: none;
}

.app-code-frame {
  background-color: rgba(255, 193, 7, 0.08);
  border-left: 3px solid #ffc107;
  padding-left: 8px;
}

.code-snippet {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
