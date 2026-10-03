<template>
  <v-card class="log-viewer d-flex flex-column fill-height" elevation="2">
    <v-toolbar color="grey-darken-4" density="comfortable" class="text-white">
      <v-toolbar-title class="text-subtitle-1 font-weight-bold">
        <v-icon start icon="mdi-console" size="small"></v-icon>
        Live System Logs
      </v-toolbar-title>
      <v-spacer></v-spacer>
      
      <v-select
        v-model="level"
        :items="levels"
        label="Min Level"
        variant="solo-filled"
        density="compact"
        hide-details
        style="max-width: 120px"
        class="mr-2"
        @update:model-value="reconnect"
      ></v-select>

      <v-text-field
        v-model="grep"
        placeholder="Filter (grep)..."
        variant="solo-filled"
        density="compact"
        hide-details
        prepend-inner-icon="mdi-filter-variant"
        style="max-width: 200px"
        class="mr-2"
        @keyup.enter="reconnect"
        clearable
        @click:clear="clearGrep"
      ></v-text-field>

      <v-btn icon="mdi-refresh" size="small" @click="reconnect" title="Reconnect"></v-btn>
      <v-btn icon="mdi-delete-sweep" size="small" @click="logs = []" title="Clear View"></v-btn>
      <v-btn :icon="autoScroll ? 'mdi-arrow-down-bold-circle' : 'mdi-arrow-down-bold-circle-outline'" 
             size="small" 
             @click="autoScroll = !autoScroll" 
             :color="autoScroll ? 'primary' : ''"
             title="Toggle Auto-scroll"></v-btn>
    </v-toolbar>

    <div class="log-container pa-2 bg-grey-darken-4 text-grey-lighten-2 font-monospace" ref="logContainer">
      <div v-if="!connected" class="text-warning mb-2">
        <v-icon start icon="mdi-lan-disconnect" size="x-small"></v-icon>
        Disconnected. Attempting to connect...
      </div>
      <div v-for="(log, index) in logs" :key="index" class="log-line" :class="getLogLevelClass(log)">
        {{ log }}
      </div>
      <div v-if="logs.length === 0" class="text-grey-darken-1 italic">
        Waiting for logs...
      </div>
    </div>
  </v-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue';

const logs = ref([]);
const level = ref('DEBUG');
const grep = ref('');
const autoScroll = ref(true);
const connected = ref(false);
const logContainer = ref(null);
let ws = null;
let reconnectTimer = null;

const levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

const getLogLevelClass = (line) => {
  if (line.includes('| ERROR    |')) return 'log-error';
  if (line.includes('| WARNING  |')) return 'log-warning';
  if (line.includes('| SUCCESS  |')) return 'log-success';
  if (line.includes('| INFO     |')) return 'log-info';
  if (line.includes('| DEBUG    |')) return 'log-debug';
  if (line.includes('| CRITICAL |')) return 'log-critical';
  return '';
};

const clearGrep = () => {
  grep.value = '';
  reconnect();
};

const connect = () => {
  if (ws) ws.close();

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = localStorage.getItem('token');
  // SECURITY NOTE (C6 audit): embedding the long-lived JWT in the
  // URL query string is a deliberate trade-off.  Browsers cannot
  // set custom headers on the WebSocket handshake, so header-based
  // auth isn't an option here.  Industry-standard mitigation is a
  // short-lived single-use ticket — see DEVELOPMENT.md "Security
  // Considerations" for the full discussion and a TODO to migrate
  // to a ticket endpoint if the operator's threat model requires it.
  const url = `${protocol}//${window.location.host}/api/admin/debug/logs/ws?token=${token}&level=${level.value}&grep=${encodeURIComponent(grep.value)}`;
  
  ws = new WebSocket(url);

  ws.onopen = () => {
    connected.value = true;
  };

  ws.onmessage = (event) => {
    logs.value.push(event.data);
    if (logs.value.length > 500) logs.value.shift();
    
    if (autoScroll.value) {
      nextTick(() => {
        if (logContainer.value) {
          logContainer.value.scrollTop = logContainer.value.scrollHeight;
        }
      });
    }
  };

  ws.onclose = () => {
    connected.value = false;
    // H9 audit fix: track the reconnect timer so onUnmounted can
    // cancel it.  Without this, ``ws.close()`` in onUnmounted
    // fires ``onclose`` which schedules a fresh ``setTimeout`` that
    // calls ``connect()`` after unmount, opening a new WebSocket
    // on an unmounted component.  In dev with HMR this can spiral
    // (each ``connect()`` closes the prior socket, which schedules
    // another reconnect).
    reconnectTimer = setTimeout(() => {
      if (!connected.value) connect();
    }, 3000);
  };

  ws.onerror = (err) => {
    console.error('Log WebSocket Error:', err);
    ws.close();
  };
};

const reconnect = () => {
  logs.value = [];
  connect();
};

onMounted(() => {
  connect();
});

onUnmounted(() => {
  // H9 audit fix: clear any pending reconnect timer first so
  // the close-handler's scheduled setTimeout doesn't fire after
  // unmount, then null onclose before closing to prevent the
  // handler from re-scheduling itself.
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    ws.onclose = null;
    ws.onerror = null;
    ws.close();
    ws = null;
  }
});
</script>

<style scoped>
.log-viewer {
  height: 600px;
  max-height: 80vh;
}
.log-container {
  flex-grow: 1;
  overflow-y: auto;
  font-size: 0.85rem;
  line-height: 1.4;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-line {
  border-bottom: 1px solid rgba(255,255,255,0.05);
  padding: 2px 0;
}
.log-error {
  color: #ff5252 !important;
  font-weight: bold;
}
.log-warning {
  color: #fb8c00 !important;
}
.log-success {
  color: #4caf50 !important;
}
.log-info {
  color: #2196f3 !important;
}
.log-debug {
  color: #9e9e9e !important;
}
.log-critical {
  color: #ff1744 !important;
  font-weight: black;
  background-color: rgba(255, 23, 68, 0.1);
}
.font-monospace {
  font-family: 'Fira Code', 'Courier New', Courier, monospace !important;
}
.italic {
  font-style: italic;
}
</style>
