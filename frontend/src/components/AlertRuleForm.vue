<template>
  <v-dialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    max-width="720px"
    persistent
  >
    <v-card>
      <v-card-title>
        <span class="text-h5">{{ isEditing ? 'Edit Rule' : 'New Rule' }}</span>
      </v-card-title>
      <v-card-text>
        <v-form ref="form" v-model="valid" @submit.prevent="save">
          <v-text-field
            v-model="edited.name"
            label="Name"
            variant="outlined"
            :rules="[v => !!v || 'Required']"
            autofocus
          ></v-text-field>

          <!-- Rule type: 3 high-level categories.  Sub-selector
               appears for per-space and lot_utilization. -->
          <v-select
            v-model="kind"
            :items="kindOptions"
            item-title="title"
            item-value="value"
            label="Rule type"
            variant="outlined"
            :rules="[v => !!v || 'Required']"
            :disabled="isEditing"
            @update:model-value="onKindChange"
          ></v-select>

          <!-- Sub-selector: which edge?  Plain v-btn with click
               handlers + computed active class — v-chip-group and
               v-btn-toggle both fail to resolve in this Vuetify 3
               build, so we use the lowest-common-denominator
               pattern that works. -->
          <template v-if="kind === 'per_space'">
            <div class="text-subtitle-2 mb-2">Fire when</div>
            <div class="d-flex mb-3" style="gap: 8px;">
              <v-btn
                v-for="opt in perSpaceSubOptions"
                :key="opt.value"
                :variant="subKind === opt.value ? 'flat' : 'outlined'"
                :color="subKind === opt.value ? 'primary' : 'default'"
                @click="subKind = opt.value"
              >{{ opt.title }}</v-btn>
            </div>
          </template>

          <template v-if="kind === 'lot_utilization'">
            <div class="text-subtitle-2 mb-2">Fire when</div>
            <div class="d-flex mb-3" style="gap: 8px;">
              <v-btn
                v-for="opt in lotUtilSubOptions"
                :key="opt.value"
                :variant="subKind === opt.value ? 'flat' : 'outlined'"
                :color="subKind === opt.value ? 'primary' : 'default'"
                @click="subKind = opt.value"
              >{{ opt.title }}</v-btn>
            </div>
          </template>

          <!-- ============================================================
               Per-Space editor
               ============================================================ -->
          <template v-if="kind === 'per_space'">
            <v-divider class="my-3">
              <span class="text-caption text-grey">Pick the spaces to watch</span>
            </v-divider>

            <!-- Source selector: segmented control of two v-btn
                 elements styled as a toggle.  v-radio-group and
                 v-radio fail to resolve in this Vuetify 3 build. -->
            <div class="text-subtitle-2 mb-2">Source</div>
            <div class="d-flex mb-3" style="gap: 8px;">
              <v-btn
                :variant="sourceType === 'camera' ? 'flat' : 'outlined'"
                :color="sourceType === 'camera' ? 'primary' : 'default'"
                prepend-icon="mdi-camera"
                @click="sourceType = 'camera'; onSourceTypeChange()"
                style="min-width: 140px;"
              >Camera</v-btn>
              <v-btn
                :variant="sourceType === 'group' ? 'flat' : 'outlined'"
                :color="sourceType === 'group' ? 'primary' : 'default'"
                prepend-icon="mdi-group"
                @click="sourceType = 'group'; onSourceTypeChange()"
                style="min-width: 140px;"
              >Parking Lot</v-btn>
            </div>

            <v-select
              v-model="pickedSourceId"
              :items="sourceType === 'camera' ? cameraOptions : groupOptions"
              item-title="title"
              item-value="value"
              :label="sourceType === 'camera' ? 'Camera' : 'Parking Lot'"
              variant="outlined"
              :rules="[v => !!v || 'Required']"
              class="mb-3"
            ></v-select>

            <v-select
              v-model="edited.condition.space_ids"
              :items="availableSpaces"
              item-title="title"
              item-value="value"
              multiple
              chips
              closable-chips
              variant="outlined"
              label="Spaces"
              :rules="[v => (v && v.length > 0) || 'Pick at least one space']"
              :disabled="!pickedSourceId"
              hint="Only spaces in the selected source are shown."
              persistent-hint
            ></v-select>

            <v-text-field
              v-model.number="edited.consecutive_count"
              type="number"
              label="Consecutive observations before firing"
              variant="outlined"
              :min="1"
              :rules="[v => (v >= 1) || 'Must be at least 1']"
              hint="Buffer to ignore flicker. 1 = fire on first observation, 3 = fire on the third consecutive."
              persistent-hint
              class="mt-2"
            ></v-text-field>
          </template>

          <!-- ============================================================
               Lot Utilization editor
               ============================================================ -->
          <template v-if="kind === 'lot_utilization'">
            <v-divider class="my-3">
              <span class="text-caption text-grey">Pick the scope for the % calculation</span>
            </v-divider>

            <div class="text-subtitle-2 mb-2">Source</div>
            <div class="d-flex mb-3" style="gap: 8px;">
              <v-btn
                :variant="sourceType === 'camera' ? 'flat' : 'outlined'"
                :color="sourceType === 'camera' ? 'primary' : 'default'"
                prepend-icon="mdi-camera"
                @click="sourceType = 'camera'; onSourceTypeChange()"
                style="min-width: 140px;"
              >Camera</v-btn>
              <v-btn
                :variant="sourceType === 'group' ? 'flat' : 'outlined'"
                :color="sourceType === 'group' ? 'primary' : 'default'"
                prepend-icon="mdi-group"
                @click="sourceType = 'group'; onSourceTypeChange()"
                style="min-width: 140px;"
              >Parking Lot</v-btn>
            </div>

            <v-select
              v-model="pickedSourceId"
              :items="sourceType === 'camera' ? cameraOptions : groupOptions"
              item-title="title"
              item-value="value"
              :label="sourceType === 'camera' ? 'Camera' : 'Parking Lot'"
              variant="outlined"
              :rules="[v => !!v || 'Required']"
              class="mb-3"
            ></v-select>

            <!--
              The Fire/Resolve labels swap with the direction the user
              picked, so the form stays intuitive in both modes.
                subKind='above' → "Fire above %" / "Resolve below %"
                  (e.g. "Fire above 90% / Resolve below 70%")
                subKind='below' → "Drop below %" / "Recover above %"
                  (e.g. "Drop below 10% / Recover above 30%")
              The arrow icon also flips direction so the visual flow
              matches the semantics.
            -->
            <v-row dense>
              <v-col cols="5">
                <v-text-field
                  v-model.number="edited.condition.fire_at_pct"
                  type="number"
                  :min="0" :max="100"
                  :label="lotFireLabel"
                  variant="outlined"
                  :rules="[v => (v >= 0 && v <= 100) || '0-100']"
                ></v-text-field>
              </v-col>
              <v-col cols="2" class="d-flex align-center justify-center">
                <v-icon color="grey">{{ lotArrowIcon }}</v-icon>
              </v-col>
              <v-col cols="5">
                <v-text-field
                  v-model.number="edited.condition.resolve_at_pct"
                  type="number"
                  :min="0" :max="100"
                  :label="lotResolveLabel"
                  variant="outlined"
                  :rules="[v => (v >= 0 && v <= 100) || '0-100']"
                ></v-text-field>
              </v-col>
            </v-row>
            <div v-if="thresholdError" class="text-caption text-error mt-1">
              {{ thresholdError }}
            </div>
          </template>

          <!-- ============================================================
               Camera-offline editor (unchanged)
               ============================================================ -->
          <template v-if="kind === 'camera_offline'">
            <v-divider class="my-3">
              <span class="text-caption text-grey">Camera offline configuration</span>
            </v-divider>
            <v-select
              v-model="pickedCameraForOfflineId"
              :items="cameraOfflineOptions"
              item-title="title"
              item-value="value"
              label="Camera"
              variant="outlined"
            ></v-select>
            <v-text-field
              v-model.number="edited.condition.minutes_offline"
              type="number"
              :min="1"
              label="Minutes offline before firing"
              variant="outlined"
              :rules="[v => (v >= 1) || 'Must be at least 1']"
              class="mt-2"
            ></v-text-field>
          </template>

          <v-divider class="my-3">
            <span class="text-caption text-grey">Delivery</span>
          </v-divider>

          <v-select
            v-model="edited.channel_id"
            :items="channelOptions"
            item-title="title"
            item-value="value"
            label="Channel"
            variant="outlined"
            :rules="[v => !!v || 'Required']"
          ></v-select>

          <v-text-field
            v-model.number="edited.cooldown_seconds"
            type="number"
            :min="0"
            label="Cooldown (seconds)"
            variant="outlined"
            hint="Minimum time between fires for this rule. 0 = no cooldown."
            persistent-hint
          ></v-text-field>

          <v-switch
            v-model="edited.enabled"
            label="Enabled"
            color="primary"
            hide-details
            class="mt-2"
          ></v-switch>
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-btn
          v-if="isEditing"
          color="secondary"
          variant="text"
          prepend-icon="mdi-flash"
          :loading="testing"
          @click="testFire"
        >
          Test Fire
        </v-btn>
        <v-spacer></v-spacer>
        <v-btn color="grey-darken-1" variant="text" @click="close">Cancel</v-btn>
        <v-btn color="primary" variant="text" :disabled="!valid || !!thresholdError" :loading="saving" @click="save">Save</v-btn>
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
          <div v-if="testResult?.event_id" class="mb-2">
            <strong>Event ID:</strong> {{ testResult.event_id }}
            <span class="text-caption text-grey ml-1">(see History tab)</span>
          </div>
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
import { ref, computed, watch, reactive, onMounted } from 'vue';
import axios from 'axios';

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  rule: { type: Object, default: null },
});
const emit = defineEmits(['update:modelValue', 'saved', 'error']);

const valid = ref(false);
const saving = ref(false);
const testing = ref(false);
const form = ref(null);
const testDialog = ref(false);
const testResult = ref(null);

const channels = ref([]);
const groups = ref([]);
const spaces = ref([]);
const cameras = ref([]);

// Top-level rule type.  The DB still stores the six trigger_type
// values; this ref is the user-facing category that maps to them.
const kind = ref('per_space');
// Sub-selector: which edge (per_space) or which direction (lot_utilization).
// 'occupied' | 'vacant' | 'either' | 'above' | 'below'
const subKind = ref('occupied');

// Source selector for per_space and lot_utilization: 'camera' | 'group'.
// Default to 'camera' for new rules — the operator usually wants to
// scope a rule to a single camera; "Camera group" is the advanced
// option.  When loading an existing rule the source is derived from
// the saved condition (camera_id vs camera_group_id), so the edit
// path keeps the operator's original choice.
const sourceType = ref('camera');
const pickedSourceId = ref(null);
// Camera-offline uses its own camera dropdown (no "any group" mode).
const pickedCameraForOfflineId = ref(null);

const isEditing = computed(() => !!props.rule?.id);

const kindOptions = [
  { title: 'Occupancy (Space becomes occupied or vacant)', value: 'per_space' },
  { title: 'Lot Utilization % (occupancy across cameras)', value: 'lot_utilization' },
  { title: 'Camera Offline (no recent scans)', value: 'camera_offline' },
];

// Sub-selector options.  Kept as refs-of-objects so the v-for
// templates can render the title without an index lookup.
const perSpaceSubOptions = [
  { title: 'Occupied', value: 'occupied' },
  { title: 'Vacant', value: 'vacant' },
  { title: 'Either', value: 'either' },
];
const lotUtilSubOptions = [
  { title: 'Near full (above %)', value: 'above' },
  { title: 'Near empty (below %)', value: 'below' },
];

const sourceTypeOptions = [
  { title: 'Camera', value: 'camera' },
  { title: 'Parking Lot', value: 'group' },
];

const onSourceTypeChange = () => {
  // Switching source type invalidates the picked source; the user
  // must pick again.  The spaces picker auto-clears via the
  // availableSpaces computed.
  pickedSourceId.value = null;
};

const channelOptions = computed(() =>
  channels.value.map(c => ({
    title: `${c.name} (${c.channel_type})`,
    value: c.id,
  }))
);

const groupOptions = computed(() =>
  groups.value.map(g => ({ title: g.name, value: g.id }))
);

const cameraOptions = computed(() =>
  cameras.value.map(c => ({ title: c.name || `Camera #${c.id}`, value: c.id }))
);

const cameraOfflineOptions = computed(() => [
  { title: 'Any camera', value: null },
  ...cameras.value.map(c => ({ title: c.name || `Camera #${c.id}`, value: c.id })),
]);

// Spaces visible in the per-space picker — filtered by the source.
const availableSpaces = computed(() => {
  if (!pickedSourceId.value) return [];
  if (sourceType.value === 'camera') {
    return spaces.value
      .filter(s => s.camera_id === pickedSourceId.value)
      .map(s => ({ title: s.name || `Space #${s.id}`, value: s.id }));
  }
  // group: spaces whose camera_id is in the group's cameras
  const grp = groups.value.find(g => g.id === pickedSourceId.value);
  if (!grp) return [];
  const camIds = new Set(grp.camera_ids || []);
  return spaces.value
    .filter(s => camIds.has(s.camera_id))
    .map(s => ({ title: `${s.name || 'Space #' + s.id} (cam #${s.camera_id})`, value: s.id }));
});

const thresholdError = computed(() => {
  if (kind.value !== 'lot_utilization') return '';
  const f = edited.condition.fire_at_pct;
  const r = edited.condition.resolve_at_pct;
  // H11 audit fix: require numeric input.  Previously the check
  // ``typeof f !== 'number' || typeof r !== 'number'`` returned ''
  // when the fields were empty, allowing the save button to enable
  // and ``buildPayload`` to send ``fire_at_pct: NaN`` (because
  // ``Number(undefined) === NaN``).
  if (typeof f !== 'number' || isNaN(f)) {
    return 'Fire at % is required and must be a number';
  }
  if (typeof r !== 'number' || isNaN(r)) {
    return 'Resolve at % is required and must be a number';
  }
  if (f < 0 || f > 100 || r < 0 || r > 100) {
    return 'Percentages must be between 0 and 100';
  }
  if (subKind.value === 'above' && !(r < f)) {
    return 'Resolve at % must be less than Fire at %';
  }
  if (subKind.value === 'below' && !(r > f)) {
    return 'Resolve at % must be greater than Fire at %';
  }
  return '';
});

// Lot-utilization labels: change with the direction so the form
// reads naturally in both modes.  "Fire above 90% / Resolve below
// 70%" makes sense for "near full"; "Drop below 10% / Recover above
// 30%" makes sense for "near empty".  The arrow icon flips to
// reinforce the visual flow (left = "fire" side, right = "resolve"
// side).
const lotFireLabel = computed(() => {
  if (kind.value !== 'lot_utilization') return 'Fire at %';
  return subKind.value === 'above' ? 'Fire above %' : 'Drop below %';
});
const lotResolveLabel = computed(() => {
  if (kind.value !== 'lot_utilization') return 'Resolve at %';
  return subKind.value === 'above' ? 'Resolve below %' : 'Recover above %';
});
const lotArrowIcon = computed(() => {
  if (kind.value !== 'lot_utilization') return 'mdi-arrow-left-thick';
  return subKind.value === 'above' ? 'mdi-arrow-down-thick' : 'mdi-arrow-up-thick';
});

const edited = reactive({
  id: null,
  name: '',
  trigger_type: 'space_occupied',  // mirrors the DB value, derived from (kind, subKind)
  condition: { space_ids: [] },
  channel_id: null,
  enabled: true,
  cooldown_seconds: 180,
  consecutive_count: 1,
});

// --- (kind, subKind) <-> (trigger_type, condition) translation ------------

const TRIGGER_FROM_KIND = {
  per_space: { occupied: 'space_occupied', vacant: 'space_vacated', either: 'space_edge' },
  lot_utilization: { above: 'lot_full_above_pct', below: 'lot_open_below_pct' },
  camera_offline: { _: 'camera_offline' },
};
const KIND_FROM_TRIGGER = {
  space_occupied: ['per_space', 'occupied'],
  space_vacated: ['per_space', 'vacant'],
  space_edge: ['per_space', 'either'],
  lot_full_above_pct: ['lot_utilization', 'above'],
  lot_open_below_pct: ['lot_utilization', 'below'],
  camera_offline: ['camera_offline', '_'],
};

const fetchAll = async () => {
  try {
    const [c, g, s, cam] = await Promise.all([
      axios.get('/api/alert-channels'),
      axios.get('/api/camera-groups'),
      axios.get('/api/spaces'),
      axios.get('/api/cameras'),
    ]);
    channels.value = c.data;
    groups.value = g.data;
    spaces.value = s.data;
    cameras.value = cam.data;
  } catch (e) {
    emit('error', 'Failed to load form data');
  }
};

const defaultConditionFor = (k) => {
  if (k === 'per_space') return { space_ids: [] };
  if (k === 'lot_utilization') {
    return { camera_group_id: null, fire_at_pct: 90, resolve_at_pct: 70 };
  }
  if (k === 'camera_offline') return { camera_id: null, minutes_offline: 10 };
  return {};
};

const onKindChange = () => {
  // Reset the condition to the defaults for this kind so a partial
  // form doesn't carry over fields from a previous kind.
  edited.condition = defaultConditionFor(kind.value);
  // Reset the source-selector sub-state.  Keep the new-rule default
  // of 'camera' (so the operator starts with the simpler "single
  // camera" scope); the edit path overrides this from the saved
  // condition in resetFromRule.
  sourceType.value = 'camera';
  pickedSourceId.value = null;
  pickedCameraForOfflineId.value = null;
  // Reset the sub-selector to a sensible per-kind default.
  // per_space → 'occupied' (becomes occupied is the common case);
  // lot_utilization → 'above' (Near Full, the common case);
  // camera_offline has no sub-selector.
  if (kind.value === 'lot_utilization') {
    subKind.value = 'above';
  } else if (kind.value === 'per_space') {
    subKind.value = 'occupied';
  }
  if (kind.value === 'camera_offline' && edited.cooldown_seconds === 180) {
    edited.cooldown_seconds = 600;
  }
};

const onSubKindChange = () => {
  // Toggling Occupied/Vacant/Either or Above/Below doesn't reset the
  // form — the condition fields stay.  No-op for now.
};

const resetFromRule = () => {
  const r = props.rule;
  edited.id = r?.id || null;
  edited.name = r?.name || '';
  edited.trigger_type = r?.trigger_type || 'space_occupied';
  edited.condition = (r?.condition && typeof r.condition === 'object')
    ? { ...r.condition }
    : defaultConditionFor('per_space');
  edited.channel_id = r?.channel_id || null;
  edited.enabled = r?.enabled !== false;
  edited.cooldown_seconds = r?.cooldown_seconds ?? 180;
  edited.consecutive_count = r?.consecutive_count ?? 1;

  // Derive (kind, subKind) from the trigger_type.
  const mapping = KIND_FROM_TRIGGER[edited.trigger_type] || ['per_space', 'occupied'];
  kind.value = mapping[0];
  subKind.value = mapping[1];

  // For per-space: the saved condition is just {space_ids: [...]};
  // we don't know which camera/group the user originally picked.  The
  // form will auto-pick a sensible default (group with the most
  // spaces, or the first camera containing a picked space) so the
  // user can see something sensible without re-picking.
  if (kind.value === 'per_space') {
    const picked = new Set(edited.condition.space_ids || []);
    if (picked.size > 0) {
      // Find the camera or group whose spaces best cover the selection.
      const firstSpace = spaces.value.find(s => picked.has(s.id));
      if (firstSpace) {
        sourceType.value = 'camera';
        pickedSourceId.value = firstSpace.camera_id;
      } else {
        sourceType.value = 'group';
        pickedSourceId.value = groups.value[0]?.id || null;
      }
    } else {
      sourceType.value = 'group';
      pickedSourceId.value = groups.value[0]?.id || null;
    }
  } else if (kind.value === 'lot_utilization') {
    // The saved condition carries the scope (camera_id or
    // camera_group_id).  Reflect it in the source radio + pick.
    if (edited.condition.camera_id != null) {
      sourceType.value = 'camera';
      pickedSourceId.value = edited.condition.camera_id;
    } else if (edited.condition.camera_group_id != null) {
      sourceType.value = 'group';
      pickedSourceId.value = edited.condition.camera_group_id;
    } else {
      sourceType.value = 'group';
      pickedSourceId.value = groups.value[0]?.id || null;
    }
  } else if (kind.value === 'camera_offline') {
    pickedCameraForOfflineId.value = edited.condition.camera_id ?? null;
  }
};

const buildPayload = () => {
  // Translate (kind, subKind) into the DB trigger_type and condition.
  let trigger_type;
  let condition = {};
  if (kind.value === 'per_space') {
    trigger_type = TRIGGER_FROM_KIND.per_space[subKind.value] || 'space_occupied';
    // For per-space we don't persist sourceType / pickedSourceId —
    // the condition is just {space_ids: [...]} and the engine doesn't
    // care which camera/group the user picked.  This keeps existing
    // rules working without migration.
    condition = { space_ids: [...(edited.condition.space_ids || [])] };
  } else if (kind.value === 'lot_utilization') {
    trigger_type = TRIGGER_FROM_KIND.lot_utilization[subKind.value] || 'lot_full_above_pct';
    const f = Number(edited.condition.fire_at_pct);
    const r = Number(edited.condition.resolve_at_pct);
    // H11 audit fix: don't send NaN to the backend.  If the user
    // bypassed the thresholdError check (or it's stale), fall back
    // to safe defaults (50%/40%) rather than 0%/NaN.
    condition = {
      fire_at_pct: Number.isFinite(f) ? f : 50,
      resolve_at_pct: Number.isFinite(r) ? r : 40,
    };
    // The source radio determines which scope field is set.
    if (sourceType.value === 'camera') {
      condition.camera_id = Number(pickedSourceId.value);
    } else {
      condition.camera_group_id = Number(pickedSourceId.value);
    }
  } else if (kind.value === 'camera_offline') {
    trigger_type = 'camera_offline';
    const camId = pickedCameraForOfflineId.value;
    condition = {
      minutes_offline: Number(edited.condition.minutes_offline),
    };
    if (camId != null) {
      condition.camera_id = Number(camId);
    }
    // (absent camera_id = "any camera")
  } else {
    trigger_type = edited.trigger_type;  // fallback; shouldn't happen
    condition = { ...edited.condition };
  }
  return {
    name: edited.name,
    trigger_type,
    condition,
    channel_id: edited.channel_id,
    enabled: edited.enabled,
    cooldown_seconds: edited.cooldown_seconds || 0,
    consecutive_count: edited.consecutive_count || 1,
  };
};

const save = async () => {
  if (form.value && !(await form.value.validate()).valid) return;
  if (thresholdError.value) return;
  saving.value = true;
  try {
    const payload = buildPayload();
    let res;
    if (isEditing.value) {
      res = await axios.put(`/api/alert-rules/${edited.id}`, payload);
    } else {
      res = await axios.post('/api/alert-rules', payload);
    }
    emit('saved', res.data);
    close();
  } catch (e) {
    const msg = e.response?.data?.detail || 'Error saving rule';
    emit('error', msg);
  } finally {
    saving.value = false;
  }
};

const testFire = async () => {
  if (!isEditing.value) {
    emit('error', 'Save the rule first before testing');
    return;
  }
  if (form.value && !(await form.value.validate()).valid) {
    emit('error', 'Fix the form errors before testing');
    return;
  }
  if (thresholdError.value) {
    emit('error', thresholdError.value);
    return;
  }
  testing.value = true;
  try {
    const res = await axios.post(`/api/alert-rules/${edited.id}/test-fire`);
    testResult.value = res.data;
    testDialog.value = true;
  } catch (e) {
    const msg = e.response?.data?.detail || 'Test fire failed';
    emit('error', msg);
  } finally {
    testing.value = false;
  }
};

const close = () => {
  emit('update:modelValue', false);
};

watch(() => props.modelValue, async (open) => {
  if (open) {
    resetFromRule();
    testResult.value = null;
    await fetchAll();
    resetFromRule();
  }
});

onMounted(fetchAll);
</script>

