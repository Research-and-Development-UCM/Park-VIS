<template>
  <v-dialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    max-width="500px"
    persistent
  >
    <v-card>
      <v-card-title>
        <span class="text-h5">{{ isEditing ? 'Edit Parking Lot' : 'New Parking Lot' }}</span>
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

          <v-select
            v-model="edited.camera_ids"
            :items="cameraOptions"
            item-title="title"
            item-value="value"
            label="Cameras"
            variant="outlined"
            multiple
            chips
            closable-chips
            :rules="[v => (v && v.length > 0) || 'At least one camera is required']"
            hint="Threshold rules scope to this parking lot."
            persistent-hint
          ></v-select>
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn color="grey-darken-1" variant="text" @click="close">Cancel</v-btn>
        <v-btn color="primary" variant="text" :disabled="!valid" :loading="saving" @click="save">Save</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { ref, computed, watch, reactive, onMounted } from 'vue';
import axios from 'axios';

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  group: { type: Object, default: null },
});
const emit = defineEmits(['update:modelValue', 'saved', 'error']);

const valid = ref(false);
const saving = ref(false);
const form = ref(null);
const cameras = ref([]);

const isEditing = computed(() => !!props.group?.id);

const cameraOptions = computed(() =>
  cameras.value.map(c => ({ title: c.name || `Camera #${c.id}`, value: c.id }))
);

const edited = reactive({
  id: null,
  name: '',
  camera_ids: [],
});

const fetchCameras = async () => {
  try {
    const res = await axios.get('/api/cameras');
    cameras.value = res.data;
  } catch (e) {
    // Surface a friendly error to the parent.
    emit('error', 'Failed to load cameras');
  }
};

const resetFromGroup = () => {
  const g = props.group;
  edited.id = g?.id || null;
  edited.name = g?.name || '';
  edited.camera_ids = Array.isArray(g?.camera_ids) ? [...g.camera_ids] : [];
};

watch(() => props.modelValue, (open) => {
  if (open) {
    resetFromGroup();
    if (cameras.value.length === 0) fetchCameras();
  }
});

const save = async () => {
  if (form.value && !(await form.value.validate()).valid) return;
  saving.value = true;
  try {
    const payload = {
      name: edited.name,
      camera_ids: [...edited.camera_ids],
    };
    let res;
    if (isEditing.value) {
      res = await axios.put(`/api/camera-groups/${edited.id}`, payload);
    } else {
      res = await axios.post('/api/camera-groups', payload);
    }
    emit('saved', res.data);
    close();
  } catch (e) {
    const msg = e.response?.data?.detail || 'Error saving parking lot';
    emit('error', msg);
  } finally {
    saving.value = false;
  }
};

const close = () => {
  emit('update:modelValue', false);
};

onMounted(fetchCameras);
</script>
