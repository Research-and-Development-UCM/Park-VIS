<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <v-card elevation="2">
          <v-toolbar color="white" density="comfortable">
            <v-toolbar-title class="text-h6">Global API Keys</v-toolbar-title>
            <v-spacer></v-spacer>
            <v-btn color="primary" prepend-icon="mdi-plus" @click="openAddDialog">New Key</v-btn>
          </v-toolbar>
          <v-divider></v-divider>
          <v-data-table :items="keys" :headers="headers" hover density="comfortable">
            <template #item.creator_username="{ item }">
              <v-chip size="x-small" variant="tonal" color="primary">
                {{ item.creator_username || 'System' }}
              </v-chip>
            </template>
            <template #item.created_at="{ item }">
              {{ formatDate(item.created_at) }}
            </template>
            <template #item.last_used_at="{ item }">
              <span v-if="item.last_used_at">{{ new Date(item.last_used_at).toLocaleString() }}</span>
              <span v-else class="text-grey italic">Never</span>
            </template>
            <template #item.actions="{ item }">
              <v-btn icon="mdi-delete" variant="text" color="error" size="small" @click="deleteKey(item.id)"></v-btn>
            </template>
          </v-data-table>
        </v-card>
      </v-col>

      <v-col cols="12">
        <v-card elevation="2">
          <v-toolbar color="white" density="comfortable">
            <v-toolbar-title class="text-h6">Documentation</v-toolbar-title>
          </v-toolbar>
          <v-divider></v-divider>
          <v-card-text>
            <div class="text-subtitle-1 font-weight-bold mb-2">Authentication</div>
            <p class="text-body-2 mb-4">
              Send your API key in the <code>X-API-Key</code> HTTP header for all requests.
            </p>

            <v-expansion-panels variant="accordion">
              <v-expansion-panel
                v-for="doc in apiDocs"
                :key="doc.title"
                :title="doc.title"
              >
                <v-expansion-panel-text>
                  <div class="mb-2"><strong>Endpoint:</strong> <code>{{ doc.method }} {{ doc.path }}</code></div>
                  <div class="mb-2 text-body-2">{{ doc.description }}</div>
                  <div class="bg-grey-darken-4 pa-3 rounded text-caption text-white overflow-x-auto">
                    <pre>{{ doc.example }}</pre>
                  </div>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- New Key Dialog -->
    <v-dialog v-model="dialog" max-width="500px">
      <v-card>
        <v-card-title>
          <span class="text-h5">Generate API Key</span>
        </v-card-title>
        <v-card-text>
          <v-form v-if="!generatedKey" ref="form" v-model="valid" @submit.prevent="generateKey">
            <v-text-field
              v-model="newName"
              label="Key Description"
              placeholder="e.g. Monitoring Script"
              variant="outlined"
              :rules="[v => !!v || 'Required']"
            ></v-text-field>
          </v-form>
          <div v-else>
            <v-alert type="warning" variant="tonal" class="mb-4">
              Copy this key now. For your security, it will <b>never</b> be shown again.
            </v-alert>
            <v-text-field
              v-model="generatedKey"
              label="Your API Key"
              variant="outlined"
              readonly
              append-inner-icon="mdi-content-copy"
              @click:append-inner="copyToClipboard"
            ></v-text-field>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn v-if="!generatedKey" color="grey-darken-1" variant="text" @click="dialog = false">Cancel</v-btn>
          <v-btn v-if="!generatedKey" color="primary" variant="text" :disabled="!valid" :loading="saving" @click="generateKey">Generate</v-btn>
          <v-btn v-else color="primary" variant="flat" @click="dialog = false">Done</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="3000">
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import axios from 'axios';

const keys = ref([]);
const dialog = ref(false);
const valid = ref(false);
const saving = ref(false);
const newName = ref('');
const generatedKey = ref('');
const snackbar = ref({ show: false, text: '', color: 'success' });

const headers = [
  { title: 'Name', key: 'name' },
  { title: 'Prefix', key: 'key_prefix' },
  { title: 'Created By', key: 'creator_username' },
  { title: 'Created At', key: 'created_at' },
  { title: 'Last Used', key: 'last_used_at' },
  { title: '', key: 'actions', sortable: false, align: 'end' }
];

const today = new Date().toISOString().slice(0, 10);

const apiDocs = computed(() => [
  {
    title: 'List Cameras',
    method: 'GET',
    path: '/api/cameras',
    description: 'Returns a list of all configured cameras.',
    example: `curl -H "X-API-Key: YOUR_KEY" http://localhost:8000/api/cameras`
  },
  {
    title: 'Current Occupancy (Single Camera)',
    method: 'GET',
    path: '/api/live/{camera_id}',
    description: 'Get the most recent occupancy state for all spaces on a specific camera.',
    example: `curl -H "X-API-Key: YOUR_KEY" http://localhost:8000/api/live/5`
  },
  {
    title: 'Current Occupancy (All Cameras)',
    method: 'GET',
    path: '/api/live/all',
    description: 'Get the most recent occupancy state for all cameras across the entire system in one call.',
    example: `curl -H "X-API-Key: YOUR_KEY" http://localhost:8000/api/live/all`
  },
  {
    title: 'Occupancy Statistics',
    method: 'GET',
    path: '/api/stats',
    description: 'Retrieve historical occupancy data for a specific time range.',
    example: `curl -H "X-API-Key: YOUR_KEY" \\
"http://localhost:8000/api/stats?start=${today}T00:00:00&end=${today}T23:59:59"`
  },
  {
    title: 'Hourly Trends',
    method: 'GET',
    path: '/api/stats/hourly',
    description: 'Retrieve aggregated hourly occupancy trends for a specific date range.',
    example: `curl -H "X-API-Key: YOUR_KEY" \\
"http://localhost:8000/api/stats/hourly?start=${today}T00:00:00&end=${today}T23:59:59"`
  }
]);

const fetchKeys = async () => {
  const res = await axios.get('/api/api_keys');
  keys.value = res.data;
};

const openAddDialog = () => {
  newName.value = '';
  generatedKey.value = '';
  dialog.value = true;
};

const generateKey = async () => {
  saving.value = true;
  try {
    const res = await axios.post('/api/api_keys', { name: newName.value });
    generatedKey.value = res.data.full_key;
    fetchKeys();
  } catch (e) {
    console.error(e);
  } finally {
    saving.value = false;
  }
};

const deleteKey = async (id) => {
  await axios.delete(`/api/api_keys/${id}`);
  fetchKeys();
};

const formatDate = (dateStr) => {
  return new Date(dateStr).toLocaleDateString();
};

const copyToClipboard = () => {
  navigator.clipboard.writeText(generatedKey.value);
  snackbar.value = { show: true, text: 'Key copied to clipboard', color: 'success' };
};

onMounted(fetchKeys);
</script>
