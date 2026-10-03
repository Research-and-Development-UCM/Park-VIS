<template>
  <v-dialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    max-width="640px"
    persistent
  >
    <v-card>
      <v-card-title>
        <span class="text-h5">{{ isEditing ? 'Edit Channel' : 'New Channel' }}</span>
      </v-card-title>
      <v-card-text>
        <v-form ref="form" v-model="valid" @submit.prevent="save">
          <v-text-field
            v-model="edited.name"
            label="Name"
            variant="outlined"
            :rules="[v => !!v || 'Required']"
          ></v-text-field>

          <v-select
            v-model="edited.channel_type"
            :items="channelTypeOptions"
            item-title="title"
            item-value="value"
            label="Type"
            variant="outlined"
            :rules="[v => !!v || 'Required']"
            :disabled="isEditing"
            @update:model-value="onTypeChange"
          ></v-select>

          <!-- Webhook config -->
          <template v-if="edited.channel_type === 'webhook'">
            <v-divider class="my-3">
              <span class="text-caption text-grey">Webhook configuration</span>
            </v-divider>
            <v-text-field
              v-model="webhookConfig.url"
              label="URL"
              variant="outlined"
              placeholder="https://hooks.example.com/..."
              :rules="[v => !!v || 'Required']"
              prepend-inner-icon="mdi-link-variant"
            ></v-text-field>
            <v-text-field
              v-model="webhookConfig.secret"
              label="Secret (optional, for HMAC-SHA256 signature)"
              variant="outlined"
              placeholder="Shared secret"
              hint="If set, the request includes an X-Signature header with sha256=<hex>"
              persistent-hint
              prepend-inner-icon="mdi-key-variant"
            ></v-text-field>
          </template>

          <!-- Email config -->
          <template v-if="edited.channel_type === 'email'">
            <v-divider class="my-3">
              <span class="text-caption text-grey">Email channel configuration</span>
            </v-divider>

            <v-text-field
              v-model="newEmail"
              label="Add recipient (press Enter to add)"
              variant="outlined"
              placeholder="alerts@example.com"
              hint="Type an email address and press Enter. Click × on a chip to remove."
              persistent-hint
              prepend-inner-icon="mdi-email-plus-outline"
              append-inner-icon="mdi-plus"
              class="mb-3"
              @keyup.enter="addEmail"
              @click:append-inner="addEmail"
            ></v-text-field>

            <div v-if="emailConfig.to.length > 0" class="d-flex flex-wrap mt-1" style="gap: 8px;">
              <v-chip
                v-for="email in emailConfig.to"
                :key="email"
                closable
                color="primary"
                variant="tonal"
                @click:close="removeEmail(email)"
              >{{ email }}</v-chip>
            </div>
            <v-alert
              v-else
              type="warning"
              variant="tonal"
              density="compact"
              class="mb-0"
            >
              At least one recipient is required.
            </v-alert>

            <v-text-field
              v-model="emailConfig.subject_prefix"
              label="Subject prefix (optional)"
              variant="outlined"
              placeholder="Parking Vis alert"
              hint="Prepended to the email subject. Useful when several channels route to the same inbox."
              persistent-hint
              prepend-inner-icon="mdi-format-text"
              class="mt-3"
            ></v-text-field>
            <v-text-field
              v-model="emailConfig.from_addr"
              label="From address override (optional)"
              variant="outlined"
              placeholder="alerts@your-domain.com"
              hint="Leave blank to use the global SMTP from address from Settings → Alerting."
              persistent-hint
              prepend-inner-icon="mdi-email-arrow-right-outline"
            ></v-text-field>
          </template>

          <!-- MQTT config -->
          <template v-if="edited.channel_type === 'mqtt'">
            <v-divider class="my-3">
              <span class="text-caption text-grey">MQTT configuration</span>
            </v-divider>
            <v-text-field
              v-model="mqttConfig.broker"
              label="Broker URL"
              variant="outlined"
              placeholder="tcp://broker.example.com or mqtts://..."
              :rules="[v => !!v || 'Required']"
              prepend-inner-icon="mdi-broadcast"
            ></v-text-field>
            <v-text-field
              v-model.number="mqttConfig.port"
              label="Port"
              variant="outlined"
              type="number"
              :rules="[v => (v > 0 && v < 65536) || 'Must be 1-65535']"
            ></v-text-field>
            <v-row dense>
              <v-col cols="6">
                <v-text-field
                  v-model="mqttConfig.username"
                  label="Username (optional)"
                  variant="outlined"
                ></v-text-field>
              </v-col>
              <v-col cols="6">
                <v-text-field
                  v-model="mqttConfig.password"
                  label="Password (optional)"
                  variant="outlined"
                  type="password"
                ></v-text-field>
              </v-col>
            </v-row>
            <v-text-field
              v-model="mqttConfig.topic"
              label="Topic"
              variant="outlined"
              placeholder="park-vis/alerts"
              :rules="[v => !!v || 'Required']"
            ></v-text-field>
            <v-row dense>
              <v-col cols="6">
                <v-select
                  v-model.number="mqttConfig.qos"
                  :items="[0, 1, 2]"
                  label="QoS"
                  variant="outlined"
                ></v-select>
              </v-col>
              <v-col cols="6">
                <v-text-field
                  v-model="mqttConfig.client_id"
                  label="Client ID (optional)"
                  variant="outlined"
                  placeholder="park-vis-alerts"
                ></v-text-field>
              </v-col>
            </v-row>
          </template>

          <v-switch
            v-model="edited.enabled"
            label="Enabled"
            color="primary"
            hide-details
            class="mb-2 mt-4"
          ></v-switch>
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-btn
          color="secondary"
          variant="text"
          prepend-icon="mdi-broadcast"
          :loading="testing"
          @click="testChannel"
        >
          Test Channel
        </v-btn>
        <v-spacer></v-spacer>
        <v-btn color="grey-darken-1" variant="text" @click="close">Cancel</v-btn>
        <v-btn color="primary" variant="text" :disabled="!valid" :loading="saving" @click="save">Save</v-btn>
      </v-card-actions>
    </v-card>

    <!-- Test result dialog -->
    <v-dialog v-model="testDialog" max-width="500px">
      <v-card>
        <v-card-title>
          <v-icon :color="testResult?.success ? 'success' : 'error'" class="mr-2">
            {{ testResult?.success ? 'mdi-check-circle' : 'mdi-alert-circle' }}
          </v-icon>
          <span>{{ testResult?.success ? 'Test Successful' : 'Test Failed' }}</span>
        </v-card-title>
        <v-card-text>
          <div v-if="testResult?.status_code" class="mb-2">
            <strong>HTTP status:</strong> {{ testResult.status_code }}
          </div>
          <div v-if="testResult?.latency_ms != null" class="mb-2">
            <strong>Latency:</strong> {{ testResult.latency_ms.toFixed(0) }} ms
          </div>
          <div v-if="testResult?.error" class="mb-2">
            <strong>Error:</strong> <span class="text-error">{{ testResult.error }}</span>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="primary" variant="text" @click="testDialog = false">Close</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-dialog>
</template>

<script setup>
import { ref, computed, watch, reactive } from 'vue';
import axios from 'axios';

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  channel: { type: Object, default: null },
});
const emit = defineEmits(['update:modelValue', 'saved', 'error']);

const valid = ref(false);
const saving = ref(false);
const testing = ref(false);
const form = ref(null);

const testDialog = ref(false);
const testResult = ref(null);

const channelTypeOptions = [
  { title: 'Webhook (POST to URL)', value: 'webhook' },
  { title: 'Email (SMTP)', value: 'email' },
  { title: 'MQTT (publish to broker)', value: 'mqtt' },
];

const isEditing = computed(() => !!props.channel?.id);

const edited = reactive({
  id: null,
  name: '',
  channel_type: 'webhook',
  enabled: true,
});

const webhookConfig = reactive({ url: '', secret: '' });
const emailConfig = reactive({ to: [], subject_prefix: '', from_addr: '' });
// The v-text-field for adding new email recipients.  We use a
// v-text-field + chip group instead of v-combobox because the
// combobox with multiple+chips can collapse to 0x0 inside a
// template v-if block (a known Vuetify rendering issue).
const newEmail = ref('');
const mqttConfig = reactive({
  broker: '',
  port: 1883,
  username: '',
  password: '',
  topic: '',
  qos: 1,
  client_id: '',
});

const addEmail = () => {
  const email = newEmail.value.trim();
  if (!email) return;
  // De-duplicate — if the address is already in the list, just
  // clear the input and don't add it again.
  if (emailConfig.to.includes(email)) {
    newEmail.value = '';
    return;
  }
  emailConfig.to.push(email);
  newEmail.value = '';
};

const removeEmail = (email) => {
  const idx = emailConfig.to.indexOf(email);
  if (idx !== -1) emailConfig.to.splice(idx, 1);
};

const resetFromChannel = () => {
  const c = props.channel;
  edited.id = c?.id || null;
  edited.name = c?.name || '';
  edited.channel_type = c?.channel_type || 'webhook';
  edited.enabled = c?.enabled !== false;
  // Reset config sub-objects to defaults first.
  webhookConfig.url = ''; webhookConfig.secret = '';
  emailConfig.to = []; emailConfig.subject_prefix = ''; emailConfig.from_addr = '';
  mqttConfig.broker = ''; mqttConfig.port = 1883;
  mqttConfig.username = ''; mqttConfig.password = '';
  mqttConfig.topic = ''; mqttConfig.qos = 1; mqttConfig.client_id = '';
  // Then load the actual config if editing.
  if (c?.config && typeof c.config === 'object') {
    if (edited.channel_type === 'webhook') {
      webhookConfig.url = c.config.url || '';
      webhookConfig.secret = c.config.secret || '';
    } else if (edited.channel_type === 'email') {
      emailConfig.to = Array.isArray(c.config.to) ? [...c.config.to] : [];
      emailConfig.subject_prefix = c.config.subject_prefix || '';
      emailConfig.from_addr = c.config.from_addr || '';
    } else if (edited.channel_type === 'mqtt') {
      mqttConfig.broker = c.config.broker || '';
      mqttConfig.port = c.config.port || 1883;
      mqttConfig.username = c.config.username || '';
      mqttConfig.password = c.config.password || '';
      mqttConfig.topic = c.config.topic || '';
      mqttConfig.qos = c.config.qos != null ? c.config.qos : 1;
      mqttConfig.client_id = c.config.client_id || '';
    }
  }
};

watch(() => props.modelValue, (open) => {
  if (open) {
    resetFromChannel();
    newEmail.value = '';
    testResult.value = null;
  }
});

const onTypeChange = () => {
  // Reset the config of the OTHER types so the saved payload only
  // contains the active type's fields.
  if (edited.channel_type !== 'webhook') {
    webhookConfig.url = ''; webhookConfig.secret = '';
  }
  if (edited.channel_type !== 'email') {
    emailConfig.to = []; emailConfig.subject_prefix = ''; emailConfig.from_addr = '';
    newEmail.value = '';
  }
  if (edited.channel_type !== 'mqtt') {
    mqttConfig.broker = ''; mqttConfig.port = 1883;
    mqttConfig.username = ''; mqttConfig.password = '';
    mqttConfig.topic = ''; mqttConfig.qos = 1; mqttConfig.client_id = '';
  }
};

const buildPayload = () => {
  let config = {};
  if (edited.channel_type === 'webhook') {
    config = { url: webhookConfig.url };
    if (webhookConfig.secret) config.secret = webhookConfig.secret;
  } else if (edited.channel_type === 'email') {
    config = { to: [...emailConfig.to] };
    if (emailConfig.subject_prefix) config.subject_prefix = emailConfig.subject_prefix;
    if (emailConfig.from_addr) config.from_addr = emailConfig.from_addr;
  } else if (edited.channel_type === 'mqtt') {
    config = { broker: mqttConfig.broker, topic: mqttConfig.topic };
    if (mqttConfig.port) config.port = mqttConfig.port;
    if (mqttConfig.qos != null) config.qos = mqttConfig.qos;
    if (mqttConfig.username) config.username = mqttConfig.username;
    if (mqttConfig.password) config.password = mqttConfig.password;
    if (mqttConfig.client_id) config.client_id = mqttConfig.client_id;
  }
  return {
    name: edited.name,
    channel_type: edited.channel_type,
    config,
    enabled: edited.enabled,
  };
};

const save = async () => {
  // The Vuetify form is bound to `valid`; the test below is a
  // belt-and-suspenders check.
  if (form.value && !(await form.value.validate()).valid) return;
  saving.value = true;
  try {
    const payload = buildPayload();
    let res;
    if (isEditing.value) {
      res = await axios.put(`/api/alert-channels/${edited.id}`, payload);
    } else {
      res = await axios.post('/api/alert-channels', payload);
    }
    emit('saved', res.data);
    close();
  } catch (e) {
    const msg = e.response?.data?.detail || 'Error saving channel';
    emit('error', msg);
  } finally {
    saving.value = false;
  }
};

const testChannel = async () => {
  // Validate the form first so we don't probe a half-filled config.
  if (form.value && !(await form.value.validate()).valid) {
    emit('error', 'Fix the form errors before testing');
    return;
  }
  testing.value = true;
  try {
    const payload = buildPayload();
    let res;
    if (isEditing.value) {
      // Persisted channel: probe the stored config.  We don't
      // auto-save in-memory edits here — the operator can hit
      // Save separately if they want the probe to use unsaved
      // values.
      res = await axios.post(`/api/alert-channels/${edited.id}/test`);
    } else {
      // Unsaved channel: probe the in-memory config without
      // persisting.  Lets the operator verify the URL / broker /
      // SMTP before committing.
      res = await axios.post('/api/alert-channels/test-config', payload);
    }
    testResult.value = res.data;
    testDialog.value = true;
  } catch (e) {
    const msg = e.response?.data?.detail || 'Test failed';
    emit('error', msg);
  } finally {
    testing.value = false;
  }
};

const close = () => {
  emit('update:modelValue', false);
};
</script>
