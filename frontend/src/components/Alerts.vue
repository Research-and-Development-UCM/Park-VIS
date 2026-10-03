<template>
  <v-container fluid>
    <v-card elevation="2">
      <v-toolbar color="white" density="comfortable">
        <v-toolbar-title class="text-h6">Alerts & Notifications</v-toolbar-title>
        <v-spacer></v-spacer>
        <v-btn
          icon="mdi-refresh"
          variant="text"
          :loading="loading"
          @click="refreshActiveTab"
          title="Refresh"
        ></v-btn>
      </v-toolbar>
      <v-divider></v-divider>

      <v-tabs
        v-model="activeTab"
        color="primary"
        align-tabs="start"
        @update:model-value="onTabChange"
      >
        <v-tab value="rules">
          <v-icon start>mdi-bell-outline</v-icon>Rules
        </v-tab>
        <v-tab value="channels">
          <v-icon start>mdi-broadcast</v-icon>Channels
        </v-tab>
        <v-tab value="history">
          <v-icon start>mdi-history</v-icon>History
        </v-tab>
      </v-tabs>

      <v-divider></v-divider>

      <v-window v-model="activeTab">
        <v-window-item value="rules">
          <div class="d-flex justify-end pa-4 pb-0">
            <v-btn
              v-if="canManage"
              color="primary"
              prepend-icon="mdi-plus"
              @click="openNewRule"
            >Add Rule</v-btn>
          </div>
          <v-data-table
            :items="rules"
            :headers="ruleHeaders"
            :loading="loading"
            hover
            density="comfortable"
            class="px-4"
          >
            <template #item.trigger_type="{ item }">
              <v-chip size="x-small" label border color="primary" variant="tonal">
                {{ triggerLabel(item.trigger_type) }}
              </v-chip>
            </template>
            <template #item.condition="{ item }">
              <span class="text-caption text-grey-darken-2">
                {{ conditionSummary(item) }}
              </span>
            </template>
            <template #item.channel_id="{ item }">
              <span class="text-caption">{{ channelName(item.channel_id) }}</span>
            </template>
            <template #item.enabled="{ item }">
              <v-switch
                v-model="item.enabled"
                density="compact"
                hide-details
                color="success"
                :readonly="!canManage"
                @change="toggleRuleEnabled(item)"
              ></v-switch>
            </template>
            <template #item.last_edited_by_username="{ item }">
              <span class="text-caption">{{ item.last_edited_by_username || '—' }}</span>
            </template>
            <template #item.actions="{ item }">
              <div v-if="canManage" class="d-flex justify-end">
                <v-btn
                  icon="mdi-pencil" variant="text" color="primary" size="small"
                  @click="openEditRule(item)" title="Edit"
                ></v-btn>
                <v-btn
                  icon="mdi-delete" variant="text" color="error" size="small"
                  @click="confirmDeleteRule(item)" title="Delete"
                ></v-btn>
              </div>
            </template>
          </v-data-table>
          <div v-if="!loading && rules.length === 0" class="pa-10 text-center text-grey-darken-1">
            <v-icon size="64" color="grey-lighten-1" class="mb-4">mdi-bell-off-outline</v-icon>
            <div class="text-h6 mb-2">No alert rules yet</div>
            <div class="text-body-2 mb-4">
              Rules describe when to fire. Combine a trigger type, condition, and channel.
            </div>
            <v-btn
              v-if="canManage"
              color="primary" variant="elevated" prepend-icon="mdi-plus"
              @click="openNewRule"
            >Create your first rule</v-btn>
          </div>
        </v-window-item>

        <!-- Channels tab -->
        <v-window-item value="channels">
          <div class="d-flex justify-end pa-4 pb-0">
            <v-btn
              v-if="canManage"
              color="primary"
              prepend-icon="mdi-plus"
              @click="openNewChannel"
            >Add Channel</v-btn>
          </div>
          <v-data-table
            :items="channels"
            :headers="channelHeaders"
            :loading="loading"
            hover
            density="comfortable"
            class="px-4"
          >
            <template #item.channel_type="{ item }">
              <v-chip size="x-small" label border color="primary" variant="tonal">
                {{ item.channel_type }}
              </v-chip>
            </template>
            <template #item.config="{ item }">
              <span class="text-caption text-grey-darken-2">
                {{ configSummary(item) }}
              </span>
            </template>
            <template #item.enabled="{ item }">
              <v-switch
                v-model="item.enabled"
                density="compact"
                hide-details
                color="success"
                :readonly="!canManage"
                @change="toggleEnabled(item)"
              ></v-switch>
            </template>
            <template #item.last_edited_by_username="{ item }">
              <span class="text-caption">{{ item.last_edited_by_username || '—' }}</span>
            </template>
            <template #item.actions="{ item }">
              <div v-if="canManage" class="d-flex justify-end">
                <v-btn
                  icon="mdi-pencil" variant="text" color="primary" size="small"
                  @click="openEditChannel(item)" title="Edit"
                ></v-btn>
                <v-btn
                  icon="mdi-delete" variant="text" color="error" size="small"
                  @click="confirmDeleteChannel(item)" title="Delete"
                ></v-btn>
              </div>
            </template>
          </v-data-table>
          <div v-if="!loading && channels.length === 0" class="pa-10 text-center text-grey-darken-1">
            <v-icon size="64" color="grey-lighten-1" class="mb-4">mdi-broadcast-off</v-icon>
            <div class="text-h6 mb-2">No channels yet</div>
            <div class="text-body-2 mb-4">
              Channels are delivery destinations (webhook / email / mqtt) that rules send alerts to.
            </div>
            <v-btn
              v-if="canManage"
              color="primary" variant="elevated" prepend-icon="mdi-plus"
              @click="openNewChannel"
            >Create your first channel</v-btn>
          </div>
        </v-window-item>

        <v-window-item value="history">
          <AlertHistory ref="historyRef" :can-manage="canManage" />
        </v-window-item>
      </v-window>
    </v-card>

    <AlertChannelForm
      v-model="channelDialogOpen"
      :channel="editingChannel"
      @saved="onChannelSaved"
      @error="(msg) => showSnackbar(msg, 'error')"
    />

    <AlertRuleForm
      v-model="ruleDialogOpen"
      :rule="editingRule"
      @saved="onRuleSaved"
      @error="(msg) => showSnackbar(msg, 'error')"
    />

    <!-- Delete confirmation (channels) -->
    <v-dialog v-model="deleteDialog" max-width="400px">
      <v-card>
        <v-card-title class="text-h5">Confirm Delete</v-card-title>
        <v-card-text>
          Delete channel <b>{{ channelToDelete?.name }}</b>?
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="grey-darken-1" variant="text" @click="deleteDialog = false">Cancel</v-btn>
          <v-btn color="error" variant="text" :loading="deleting" @click="doDeleteChannel">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- Delete confirmation (rules) -->
    <v-dialog v-model="ruleDeleteDialog" max-width="480px">
      <v-card>
        <v-card-title class="text-h5">Confirm Delete</v-card-title>
        <v-card-text>
          <div>Delete rule <b>{{ ruleToDelete?.name }}</b>?</div>
          <v-checkbox
            v-if="ruleToDeleteHasEvents"
            v-model="ruleForceDelete"
            label="Also delete fired events for this rule"
            density="compact"
            hide-details
            class="mt-3"
          ></v-checkbox>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="grey-darken-1" variant="text" @click="ruleDeleteDialog = false">Cancel</v-btn>
          <v-btn color="error" variant="text" :loading="ruleDeleting" @click="doDeleteRule">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="3000">
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import axios from 'axios';
import AlertChannelForm from './AlertChannelForm.vue';
import AlertHistory from './AlertHistory.vue';
import AlertRuleForm from './AlertRuleForm.vue';

const route = useRoute();
const router = useRouter();

const activeTab = ref('rules');
const loading = ref(false);
const snackbar = ref({ show: false, text: '', color: 'success' });

const channels = ref([]);
const channelDialogOpen = ref(false);
const editingChannel = ref(null);
const deleteDialog = ref(false);
const deleting = ref(false);
const channelToDelete = ref(null);

const rules = ref([]);
const ruleDialogOpen = ref(false);
const editingRule = ref(null);
const ruleDeleteDialog = ref(false);
const ruleDeleting = ref(false);
const ruleToDelete = ref(null);
const ruleToDeleteHasEvents = ref(false);
const ruleForceDelete = ref(false);

const canManage = computed(() => {
  if (localStorage.getItem('is_admin') === 'true') return true;
  try {
    const perms = JSON.parse(localStorage.getItem('permissions') || '[]');
    return perms.includes('manage_alerts');
  } catch (e) {
    return false;
  }
});

const showSnackbar = (text, color = 'success') => {
  snackbar.value = { show: true, text, color };
};

const channelHeaders = [
  { title: 'ID', key: 'id', width: 60 },
  { title: 'Name', key: 'name' },
  { title: 'Type', key: 'channel_type', width: 100 },
  { title: 'Destination', key: 'config', sortable: false },
  { title: 'Enabled', key: 'enabled', width: 90 },
  { title: 'Last edited by', key: 'last_edited_by_username', width: 160 },
  { title: 'Actions', key: 'actions', sortable: false, align: 'end', width: 120 },
];

const ruleHeaders = [
  { title: 'ID', key: 'id', width: 60 },
  { title: 'Name', key: 'name' },
  { title: 'Trigger', key: 'trigger_type', width: 220 },
  { title: 'Condition', key: 'condition', sortable: false },
  { title: 'Channel', key: 'channel_id', width: 160 },
  { title: 'Cooldown', key: 'cooldown_seconds', width: 100 },
  { title: 'Enabled', key: 'enabled', width: 90 },
  { title: 'Last edited by', key: 'last_edited_by_username', width: 160 },
  { title: 'Actions', key: 'actions', sortable: false, align: 'end', width: 120 },
];

const TRIGGER_LABELS = {
  space_occupied: 'per-space (occupied)',
  space_vacated: 'per-space (vacant)',
  space_edge: 'per-space (either)',
  lot_full_above_pct: 'lot utilization (above %)',
  lot_open_below_pct: 'lot utilization (below %)',
  camera_offline: 'camera offline',
};
const triggerLabel = (t) => TRIGGER_LABELS[t] || t;

const configSummary = (item) => {
  const c = item.config || {};
  if (item.channel_type === 'webhook') return c.url || '';
  if (item.channel_type === 'email') return (c.to || []).join(', ');
  if (item.channel_type === 'mqtt') return `${c.broker || ''} → ${c.topic || ''}`;
  return '';
};

const fetchChannels = async () => {
  loading.value = true;
  try {
    const res = await axios.get('/api/alert-channels');
    channels.value = res.data;
  } catch (e) {
    showSnackbar('Error loading channels', 'error');
  } finally {
    loading.value = false;
  }
};

const fetchRules = async () => {
  loading.value = true;
  try {
    const res = await axios.get('/api/alert-rules');
    rules.value = res.data;
    // We also need the channels list to render the channel name column.
    if (channels.value.length === 0) {
      const c = await axios.get('/api/alert-channels');
      channels.value = c.data;
    }
  } catch (e) {
    showSnackbar('Error loading rules', 'error');
  } finally {
    loading.value = false;
  }
};

const conditionSummary = (item) => {
  const c = item.condition || {};
  const t = item.trigger_type;
  if (['space_occupied', 'space_vacated', 'space_edge'].includes(t)) {
    const n = (c.space_ids || []).length;
    return `${n} space${n === 1 ? '' : 's'}`;
  }
  if (['lot_full_above_pct', 'lot_open_below_pct'].includes(t)) {
    const scope = c.camera_group_id
      ? `group #${c.camera_group_id}`
      : (c.camera_id ? `camera #${c.camera_id}` : '(no scope)');
    return `${scope}: fire ${t === 'lot_full_above_pct' ? '≥' : '≤'} ${c.fire_at_pct}%, resolve ${t === 'lot_full_above_pct' ? '≤' : '≥'} ${c.resolve_at_pct}%`;
  }
  if (t === 'camera_offline') {
    const cam = c.camera_id != null ? `camera #${c.camera_id}` : 'any';
    return `${cam}, ${c.minutes_offline}min`;
  }
  return '';
};

const channelName = (id) => {
  const c = channels.value.find(c => c.id === id);
  return c ? c.name : `Channel #${id}`;
};

const openNewRule = () => {
  editingRule.value = null;
  ruleDialogOpen.value = true;
};

const openEditRule = (item) => {
  editingRule.value = item;
  ruleDialogOpen.value = true;
};

const onRuleSaved = () => {
  showSnackbar(editingRule.value ? 'Rule updated' : 'Rule created');
  fetchRules();
};

const toggleRuleEnabled = async (item) => {
  try {
    await axios.put(`/api/alert-rules/${item.id}`, { enabled: item.enabled });
    showSnackbar(item.enabled ? 'Rule enabled' : 'Rule disabled');
  } catch (e) {
    showSnackbar('Error updating rule', 'error');
    item.enabled = !item.enabled; // revert
  }
};

const confirmDeleteRule = async (item) => {
  ruleToDelete.value = item;
  ruleForceDelete.value = false;
  ruleToDeleteHasEvents.value = false;
  // Check if the rule has any fired events so we know whether to
  // show the force-delete option.
  try {
    const res = await axios.get('/api/alerts', { params: { rule_id: item.id, limit: 1 } });
    ruleToDeleteHasEvents.value = (res.data || []).length > 0;
  } catch (e) {
    ruleToDeleteHasEvents.value = false;
  }
  ruleDeleteDialog.value = true;
};

const doDeleteRule = async () => {
  ruleDeleting.value = true;
  try {
    const params = ruleForceDelete.value ? { force: 'true' } : {};
    await axios.delete(`/api/alert-rules/${ruleToDelete.value.id}`, { params });
    showSnackbar('Rule deleted');
    ruleDeleteDialog.value = false;
    fetchRules();
  } catch (e) {
    const msg = e.response?.data?.detail || 'Error deleting rule';
    showSnackbar(msg, 'error');
  } finally {
    ruleDeleting.value = false;
  }
};

const openNewChannel = () => {
  editingChannel.value = null;
  channelDialogOpen.value = true;
};

const openEditChannel = (item) => {
  editingChannel.value = item;
  channelDialogOpen.value = true;
};

const onChannelSaved = (saved) => {
  showSnackbar(editingChannel.value ? 'Channel updated' : 'Channel created');
  fetchChannels();
};

const confirmDeleteChannel = (item) => {
  channelToDelete.value = item;
  deleteDialog.value = true;
};

const doDeleteChannel = async () => {
  deleting.value = true;
  try {
    await axios.delete(`/api/alert-channels/${channelToDelete.value.id}`);
    showSnackbar('Channel deleted');
    deleteDialog.value = false;
    fetchChannels();
  } catch (e) {
    const msg = e.response?.data?.detail || 'Error deleting channel';
    showSnackbar(msg, 'error');
  } finally {
    deleting.value = false;
  }
};

const toggleEnabled = async (item) => {
  try {
    await axios.put(`/api/alert-channels/${item.id}`, { enabled: item.enabled });
    showSnackbar(item.enabled ? 'Channel enabled' : 'Channel disabled');
  } catch (e) {
    showSnackbar('Error updating channel', 'error');
    item.enabled = !item.enabled; // revert
  }
};

const refreshActiveTab = () => {
  if (activeTab.value === 'channels') fetchChannels();
  if (activeTab.value === 'rules') fetchRules();
  if (activeTab.value === 'history' && historyRef.value) {
    historyRef.value.startAutoRefresh();
  } else if (historyRef.value) {
    historyRef.value.stopAutoRefresh();
  }
};

const onTabChange = (val) => {
  // The router.replace below fires the watch(route.query.tab, syncTabFromQuery)
  // watcher, which already calls fetchChannels()/fetchRules() as needed.
  // Calling them here as well would issue two identical requests on every
  // tab switch.  The history auto-refresh toggle is unique to onTabChange
  // (the watcher doesn't know about historyRef) so it stays.
  router.replace({ query: { ...route.query, tab: val } });
  if (val === 'history' && historyRef.value) {
    historyRef.value.startAutoRefresh();
  } else if (historyRef.value) {
    historyRef.value.stopAutoRefresh();
  }
};

const historyRef = ref(null);

const syncTabFromQuery = () => {
  const t = route.query.tab;
  const allowed = ['rules', 'channels', 'history'];
  if (typeof t === 'string' && allowed.includes(t)) {
    activeTab.value = t;
  }
  // Always fetch for the active tab, even when there's no query
  // param.  The default tab is 'rules' and the user lands on
  // /alerts (no query) when clicking the sidebar nav link, so we
  // must populate the list on first mount — otherwise leaving the
  // page and returning shows an empty table until the user clicks
  // the refresh button.  History is handled by AlertHistory's own
  // onMounted (which is lazy-mounted by v-window-item only when
  // the tab is active), so we don't need to fetch for it here.
  if (activeTab.value === 'channels') fetchChannels();
  if (activeTab.value === 'rules') fetchRules();
};

watch(() => route.query.tab, syncTabFromQuery);

onMounted(() => {
  syncTabFromQuery();
});
</script>
