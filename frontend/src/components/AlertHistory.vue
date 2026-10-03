<template>
  <div>
    <!-- Filters -->
    <div class="pa-4 pb-0 d-flex flex-wrap align-center" style="gap: 12px;">
      <v-select
        v-model="filterStatus"
        :items="statusOptions"
        label="Status"
        variant="outlined"
        density="compact"
        clearable
        hide-details
        style="max-width: 180px;"
        @update:model-value="fetchEvents"
      ></v-select>
      <v-select
        v-model="filterRuleId"
        :items="ruleOptions"
        item-title="title"
        item-value="value"
        label="Rule"
        variant="outlined"
        density="compact"
        clearable
        hide-details
        style="max-width: 240px;"
        @update:model-value="fetchEvents"
      ></v-select>
      <v-spacer></v-spacer>
      <v-chip-group
        v-model="filterTime"
        mandatory
        selected-class="bg-primary text-white"
        @update:model-value="fetchEvents"
      >
        <v-chip
          v-for="opt in timeOptions"
          :key="opt.value"
          :value="opt.value"
          variant="outlined"
          size="small"
        >{{ opt.title }}</v-chip>
      </v-chip-group>
      <v-btn
        icon="mdi-refresh"
        variant="text"
        :loading="loading"
        @click="fetchEvents"
        title="Refresh"
      ></v-btn>
    </div>

    <v-data-table
      :items="events"
      :headers="headers"
      :loading="loading"
      :expanded="expanded"
      @update:expanded="(v) => expanded = v"
      item-value="id"
      show-expand
      hover
      density="comfortable"
      class="px-4"
    >
      <template #item.fired_at="{ item }">
        <span class="text-caption">{{ formatTime(item.fired_at) }}</span>
      </template>
      <template #item.rule_id="{ item }">
        <span class="text-body-2">#{{ item.rule_id }}</span>
      </template>
      <template #item.status="{ item }">
        <v-chip
          size="x-small"
          label
          :color="statusColor(item.status)"
          variant="flat"
        >{{ item.status }}</v-chip>
      </template>
      <template #item.attempts="{ item }">
        <span class="text-caption">
          {{ item.attempts }} / {{ maxAttempts }}
          <v-tooltip v-if="item.attempts >= maxAttempts" text="Max attempts reached" location="top">
            <template v-slot:activator="{ props }">
              <v-icon v-bind="props" size="x-small" color="warning" class="ml-1">mdi-alert-circle</v-icon>
            </template>
          </v-tooltip>
        </span>
      </template>
      <template #item.last_error="{ item }">
        <v-tooltip v-if="item.last_error" :text="item.last_error" location="top">
          <template v-slot:activator="{ props }">
            <span v-bind="props" class="text-caption text-error" style="max-width: 200px; display: inline-block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              {{ item.last_error }}
            </span>
          </template>
        </v-tooltip>
        <span v-else class="text-caption text-grey">—</span>
      </template>
      <template #item.actions="{ item }">
        <v-btn
          v-if="canRetry(item)"
          icon="mdi-restart"
          variant="text"
          color="primary"
          size="small"
          :loading="retryingId === item.id"
          :disabled="item.attempts >= maxAttempts"
          @click="retryEvent(item)"
          title="Retry"
        ></v-btn>
      </template>
      <template #expanded-row="{ columns, item }">
        <tr :class="'bg-grey-lighten-4'">
          <td :colspan="columns.length">
            <div class="pa-3">
              <div class="text-caption text-grey-darken-1 mb-1">Payload</div>
              <pre class="text-caption" style="white-space: pre-wrap; word-break: break-all; margin: 0;">{{ formatPayload(item.payload) }}</pre>
            </div>
          </td>
        </tr>
      </template>
    </v-data-table>

    <div v-if="!loading && events.length === 0" class="pa-10 text-center text-grey-darken-1">
      <v-icon size="64" color="grey-lighten-1" class="mb-4">mdi-bell-off-outline</v-icon>
      <div class="text-h6 mb-2">No alert events</div>
      <div class="text-body-2">
        Events will appear here when rules fire.
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import axios from 'axios';

const props = defineProps({
  canManage: { type: Boolean, default: false },
  autoRefreshSeconds: { type: Number, default: 30 },
});

const events = ref([]);
const rules = ref([]);
const loading = ref(false);
const retryingId = ref(null);
const expanded = ref([]);

const filterStatus = ref(null);
const filterRuleId = ref(null);
const filterTime = ref('24h');
const maxAttempts = 4;

const statusOptions = [
  { title: 'All', value: null },
  { title: 'Fired', value: 'fired' },
  { title: 'Sent', value: 'sent' },
  { title: 'Failed', value: 'failed' },
];

const timeOptions = [
  { title: '1h', value: '1h' },
  { title: '24h', value: '24h' },
  { title: '7d', value: '7d' },
  { title: 'All', value: 'all' },
];

const ruleOptions = computed(() => [
  { title: 'All rules', value: null },
  ...rules.value.map(r => ({ title: `#${r.id} — ${r.name}`, value: r.id })),
]);

const headers = [
  { title: 'Fired at', key: 'fired_at', width: 170 },
  { title: 'Rule', key: 'rule_id', width: 80 },
  { title: 'Status', key: 'status', width: 100 },
  { title: 'Attempts', key: 'attempts', width: 110 },
  { title: 'Last error', key: 'last_error', sortable: false },
  { title: '', key: 'data-table-expand', width: 40 },
  { title: 'Actions', key: 'actions', sortable: false, align: 'end', width: 70 },
];

const statusColor = (s) => {
  if (s === 'sent') return 'success';
  if (s === 'failed') return 'error';
  if (s === 'fired') return 'info';
  return 'grey';
};

const formatTime = (iso) => {
  if (!iso) return '';
  // The backend always emits ISO strings with a 'Z' (UTC)
  // suffix, which the Date parser reads as UTC and
  // ``toLocaleString`` then converts to the user's local time.
  // If we ever receive a naive string (no Z, no offset) from a
  // legacy or third-party endpoint, append 'Z' so it's not
  // misinterpreted as local time and shown wrong by the user's
  // UTC offset.
  let s = String(iso);
  if (!/Z$|[+-]\d{2}:?\d{2}$/.test(s)) {
    s = s + 'Z';
  }
  try {
    return new Date(s).toLocaleString();
  } catch (e) {
    return iso;
  }
};

const formatPayload = (payload) => {
  if (!payload) return '(empty)';
  if (typeof payload === 'string') {
    try {
      return JSON.stringify(JSON.parse(payload), null, 2);
    } catch (e) {
      return payload;
    }
  }
  return JSON.stringify(payload, null, 2);
};

const canRetry = (item) => {
  // Only 'failed' events are retryable.  'sent' events already
  // delivered successfully — re-firing would just produce a
  // duplicate alert on the downstream system.  'fired' events
  // are still pending in the dispatcher.  The attempts cap is
  // enforced server-side as well.
  if (!props.canManage) return false;
  if (item.status !== 'failed') return false;
  if (item.attempts >= maxAttempts) return false;
  return true;
};

const buildParams = () => {
  const params = {};
  if (filterStatus.value) params.status = filterStatus.value;
  if (filterRuleId.value) params.rule_id = filterRuleId.value;
  if (filterTime.value && filterTime.value !== 'all') {
    const map = { '1h': 1, '24h': 24, '7d': 24 * 7 };
    const hours = map[filterTime.value];
    if (hours) {
      const since = new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
      params.since = since;
    }
  }
  params.limit = 200;
  return params;
};

const fetchEvents = async () => {
  loading.value = true;
  try {
    const res = await axios.get('/api/alerts', { params: buildParams() });
    events.value = res.data;
  } catch (e) {
    events.value = [];
  } finally {
    loading.value = false;
  }
};

const fetchRules = async () => {
  try {
    const res = await axios.get('/api/alert-rules');
    rules.value = res.data;
  } catch (e) {
    rules.value = [];
  }
};

const retryEvent = async (item) => {
  retryingId.value = item.id;
  try {
    await axios.post(`/api/alerts/${item.id}/retry`);
    await fetchEvents();
  } catch (e) {
    const msg = e.response?.data?.detail || 'Retry failed';
    alert(msg);  // simple fallback; could route through parent snackbar
  } finally {
    retryingId.value = null;
  }
};

let refreshTimer = null;
const startAutoRefresh = () => {
  if (refreshTimer) return;
  refreshTimer = setInterval(fetchEvents, props.autoRefreshSeconds * 1000);
};
const stopAutoRefresh = () => {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
};

onMounted(async () => {
  await Promise.all([fetchEvents(), fetchRules()]);
  startAutoRefresh();
});

onBeforeUnmount(stopAutoRefresh);

// Re-fetch when the parent's "active tab" signal changes (parent
// toggles this when the user navigates to/from the History tab).
defineExpose({ fetchEvents, stopAutoRefresh, startAutoRefresh });
</script>
