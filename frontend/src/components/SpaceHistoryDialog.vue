<template>
  <v-navigation-drawer
    v-model="visible"
    location="right"
    temporary
    width="400"
    class="pa-0"
    style="z-index: 3000;"
  >
    <v-toolbar color="primary" density="comfortable">
      <v-toolbar-title class="text-subtitle-1">
        History: {{ space?.name }}
      </v-toolbar-title>
      <v-spacer></v-spacer>
      <v-btn icon="mdi-close" @click="visible = false"></v-btn>
    </v-toolbar>

    <div v-if="loading" class="d-flex align-center justify-center pa-10">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <v-list v-else-if="events.length > 0" lines="three" class="pa-0">
      <v-list-item v-for="event in events" :key="event.id" class="border-b px-4 py-3">
        <template v-slot:prepend>
          <v-avatar :color="event.event_type === 'occupied' ? 'error' : 'success'" size="40" class="mr-3">
            <v-icon color="white">{{ event.event_type === 'occupied' ? 'mdi-car' : 'mdi-car-off' }}</v-icon>
          </v-avatar>
        </template>

        <v-list-item-title class="font-weight-bold">
          {{ event.event_type === 'occupied' ? 'Space Occupied' : 'Space Vacated' }}
        </v-list-item-title>
        
        <v-list-item-subtitle class="text-caption mt-1">
          {{ formatDate(event.timestamp) }}
        </v-list-item-subtitle>

        <template v-slot:append>
          <v-btn
            icon="mdi-arrow-right-circle-outline"
            variant="text"
            color="primary"
            size="small"
            title="View in History Timeline"
            @click="jumpToHistory(event)"
          ></v-btn>
        </template>

        <div v-if="event.has_crop" class="mt-3 rounded overflow-hidden border" style="width: 128px; height: 128px; background: #000;">
          <v-img
            :src="getCropUrl(event.id)"
            width="128"
            height="128"
            cover
          >
            <template v-slot:placeholder>
              <div class="d-flex align-center justify-center fill-height">
                <v-progress-circular indeterminate size="20" width="2"></v-progress-circular>
              </div>
            </template>
          </v-img>
        </div>
      </v-list-item>
    </v-list>

    <div v-else class="d-flex flex-column align-center justify-center pa-10 text-grey">
      <v-icon size="48">mdi-history</v-icon>
      <div class="mt-2 text-center">No recent events recorded for this space.</div>
    </div>
  </v-navigation-drawer>
</template>

<script setup>
import { ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import axios from 'axios';

const router = useRouter();
const props = defineProps({
  modelValue: Boolean,
  space: Object
});
const emit = defineEmits(['update:modelValue']);

const visible = ref(false);
const events = ref([]);
const loading = ref(false);

const jumpToHistory = (event) => {
  visible.value = false;
  router.push({
    path: '/history',
    query: {
      camera_id: props.space.camera_id,
      timestamp: event.timestamp,
      space_id: props.space.id
    }
  });
};

watch(() => props.modelValue, (val) => {
  visible.value = val;
  if (val && props.space) {
    fetchEvents();
  }
});

watch(() => props.space?.id, (newId, oldId) => {
  if (visible.value && newId && newId !== oldId) {
    fetchEvents();
  }
});

watch(visible, (val) => {
  emit('update:modelValue', val);
});

const fetchEvents = async () => {
  console.log("[DEBUG] Fetching events for space:", props.space.id);
  loading.value = true;
  try {
    const res = await axios.get(`/api/history/spaces/${props.space.id}`);
    console.log("[DEBUG] Received events:", res.data);
    events.value = res.data;
  } catch (e) {
    console.error("Error fetching space events:", e);
  } finally {
    loading.value = false;
  }
};

const getCropUrl = (eventId) => {
  const token = localStorage.getItem('token');
  return token ? `/api/events/${eventId}/crop?token=${token}` : `/api/events/${eventId}/crop`;
};

const formatDate = (dateStr) => {
  if (!dateStr) return '—';
  // The previous version called ``dateStr.endsWith('Z')`` on a
  // possibly-null value (the API can return a row with no
  // timestamp), throwing a TypeError that broke the entire
  // dialog. Guard the input. Also normalize the trailing 'Z'
  // for naive timestamps and accept ISO offsets (-05:00) so we
  // don't produce the invalid "…-05:00Z" form.
  let s = String(dateStr);
  if (/[Zz]$|[+\-]\d{2}:?\d{2}$/.test(s)) {
    // already has a timezone marker
  } else {
    s += 'Z';
  }
  return new Intl.DateTimeFormat('default', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(new Date(s));
};
</script>
