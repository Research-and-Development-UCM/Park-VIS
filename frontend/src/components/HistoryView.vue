<template>
  <v-container fluid class="pa-0 d-flex flex-column bg-black" style="min-height: 800px; height: calc(100vh - 100px);">
    <!-- Top Toolbar: Controls -->
    <v-toolbar color="grey-darken-3" density="comfortable" class="border-b border-grey-darken-2" height="72" style="flex: 0 0 auto;">
      <v-toolbar-title class="text-subtitle-1 font-weight-bold d-flex align-center mr-4" style="min-width: 150px;">
        <v-icon color="primary" class="mr-2">mdi-history</v-icon>
        History
      </v-toolbar-title>

      <v-select
        v-model="selectedCameraId"
        :items="cameraOptions"
        :key="cameras.length"
        :label="`Camera (${cameraOptions.length})`"
        :menu-props="{ maxHeight: 600 }"
        variant="outlined"
        density="compact"
        hide-details
        class="mr-3"
        style="max-width: 250px;"
        @update:model-value="onCameraChange"
      ></v-select>

      <v-text-field
        v-model="selectedDate"
        label="Date"
        type="date"
        variant="outlined"
        density="compact"
        hide-details
        class="mr-3"
        style="max-width: 180px;"
        @change="onDateChange"
        @keydown.enter="onDateChange"
      ></v-text-field>

      <v-text-field
        v-model="manualTime"
        label="Jump to Time"
        type="time"
        step="1"
        variant="outlined"
        density="compact"
        hide-details
        class="mr-3"
        style="max-width: 150px;"
        @blur="e => onManualTimeChange(manualTime)"
        @keydown.enter="e => onManualTimeChange(manualTime)"
      ></v-text-field>

      <v-divider vertical class="mx-2 my-3"></v-divider>

      <v-select
        v-model="selectedSpaceId"
        :items="spaceOptions"
        label="Filter by Space"
        clearable
        variant="outlined"
        density="compact"
        hide-details
        class="ml-3"
        style="max-width: 200px;"
        @update:model-value="fetchSpaceEvents"
      ></v-select>

      <v-btn
        v-if="hasPermission('manage_cameras') && bgImage"
        :color="isFeedbackMode ? 'error' : 'secondary'"
        variant="tonal"
        class="ml-3"
        @click="toggleFeedbackMode"
        :prepend-icon="isFeedbackMode ? 'mdi-close' : 'mdi-auto-fix'"
      >
        {{ isFeedbackMode ? 'Cancel' : 'Improve AI' }}
      </v-btn>

      <div class="d-flex align-center" v-show="selectedSpaceId && spaceEvents.length > 0">
        <v-btn
          icon="mdi-chevron-left"
          variant="text"
          size="small"
          color="white"
          @click="nextEventPage"
          :disabled="eventPage >= totalPages - 1"
        ></v-btn>
        <v-chip
          color="grey-darken-1"
          variant="outlined"
          size="small"
          class="mx-1 text-white font-weight-medium"
          prepend-icon="mdi-format-list-bulleted"
        >
          {{ displayedEvents.length }} of {{ spaceEvents.length }}
        </v-chip>
        <v-btn
          icon="mdi-chevron-right"
          variant="text"
          size="small"
          color="white"
          @click="prevEventPage"
          :disabled="eventPage === 0"
        ></v-btn>
        <v-menu :close-on-content-click="false" location="bottom end">
          <template v-slot:activator="{ props }">
            <v-btn
              v-bind="props"
              icon="mdi-format-list-bulleted"
              variant="text"
              size="small"
              class="ml-1"
            ></v-btn>
          </template>
          <v-list :key="selectedSpaceId" density="compact" class="bg-grey-darken-3 pa-0" style="width: 250px;">
            <v-virtual-scroll
              :items="spaceEvents"
              height="400"
              item-height="50"
            >
              <template v-slot:default="{ item }">
                <v-list-item
                  @click="jumpToEvent(item)"
                  :active="isEventSelected(item)"
                  color="primary"
                  class="border-b border-grey-darken-2"
                >
                  <template v-slot:prepend>
                    <v-icon size="small" :color="item.event_type === 'occupied' ? '#F44336' : '#4CAF50'">
                      {{ item.event_type === 'occupied' ? 'mdi-car' : 'mdi-car-off' }}
                    </v-icon>
                  </template>
                  <v-list-item-title class="text-caption">
                    {{ formatTime(item.timestamp) }}
                  </v-list-item-title>
                  <v-list-item-subtitle class="text-xxs d-flex align-center">
                    <span class="mr-2">{{ item.event_type.toUpperCase() }}</span>
                    <v-icon v-if="!item.has_crop" size="x-small" color="warning" class="mr-1">mdi-image-off</v-icon>
                    <v-icon v-else size="x-small" color="success">mdi-image</v-icon>
                  </v-list-item-subtitle>
                </v-list-item>
              </template>
            </v-virtual-scroll>
          </v-list>
        </v-menu>
      </div>

      <v-spacer></v-spacer>
    </v-toolbar>

    <!-- Main Content Area (Image Area) -->
    <div class="flex-grow-1 position-relative bg-black" style="min-height: 0;" ref="container">
      <!-- Feedback Overlay -->
      <div v-if="isFeedbackMode" class="feedback-overlay pa-4 w-100">
        <v-card color="error" elevation="10" class="backdrop-blur border-white border">
          <v-card-text class="py-2 px-4 d-flex align-center">
            <v-icon icon="mdi-brain" class="mr-3"></v-icon>
            <div>
              <div class="text-subtitle-2 font-weight-bold">AI Improvement Mode</div>
              <div class="text-caption text-white opacity-90">
                Click spots to fix detection. Every space MUST be perfectly labeled before submitting.
                <span v-if="correctionsCount > 0" class="font-weight-bold text-warning text-amber-lighten-2">
                  · {{ correctionsCount }} correction{{ correctionsCount === 1 ? '' : 's' }}
                </span>
              </div>
            </div>
            <v-spacer></v-spacer>
            <v-btn variant="text" color="white" class="mr-2" @click="toggleFeedbackMode">Cancel</v-btn>
            <v-btn color="white" variant="flat" class="text-error font-weight-bold" @click="submitFeedback" :loading="feedbackSubmitting" prepend-icon="mdi-check-all">Submit Improvement</v-btn>
          </v-card-text>
        </v-card>
      </div>

      <v-stage 
        v-if="bgImage"
        :config="stageConfig"
        style="position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);"
      >
        <v-layer>
          <v-image :config="{ image: bgImage }" />
          <v-line
            v-for="space in spaces"
            :key="space.id"
            @click="isFeedbackMode ? toggleSpaceFeedback(space) : onSpaceClick(space)"
            @mouseenter="(e) => e.target.getStage().container().style.cursor = 'pointer'"
            @mouseleave="(e) => e.target.getStage().container().style.cursor = 'default'"
            :config="{
              points: space.points,
              fill: getSpaceFill(space),
              stroke: getSpaceStroke(space),
              strokeWidth: (selectedSpaceId === space.id || isFeedbackMode ? 4 : 2) / scale,
              closed: true,
              opacity: selectedSpaceId && selectedSpaceId !== space.id ? 0.3 : 0.8,
              dash: isFeedbackMode ? [5, 2] : null
            }"
          />
        </v-layer>
      </v-stage>

      <div v-if="!bgImage && !loading && !isImageLoading" class="d-flex fill-height w-100 align-center justify-center text-grey">
        <div class="text-center pa-10 bg-grey-darken-4 rounded-xl border border-grey-darken-3 elevation-10">
          <v-icon size="80" color="grey-darken-2" class="mb-4">mdi-image-off-outline</v-icon>
          <div class="text-h5 font-weight-light mb-2">No Visual Available</div>
          <div class="text-body-2 text-grey-darken-1" style="max-width: 380px;">
            The occupancy data for this moment is available, but the snapshot
            image has been removed from storage per the retention policy.
            Only scans newer than the retention window have images available.
          </div>
          <div v-if="!selectedCameraId" class="mt-4">
            <v-btn color="primary" variant="flat" prepend-icon="mdi-camera">Select Camera</v-btn>
          </div>
        </div>
      </div>

      <v-overlay v-model="loading" persistent class="align-center justify-center" contained>
        <v-progress-circular indeterminate color="primary" size="64"></v-progress-circular>
      </v-overlay>

      <!-- Top Status Bar (Floating) -->
      <div v-if="currentScan" class="status-bar pa-2 rounded-lg elevation-4 d-flex align-center">
        <v-chip size="small" color="white" variant="outlined" class="mr-2">
          Captured: {{ formatDateTime(currentScan.timestamp) }}
        </v-chip>
        <v-chip v-if="currentScan.inference_speed" size="small" color="grey-lighten-1" variant="text">
          <v-icon start icon="mdi-speedometer" size="x-small"></v-icon>
          {{ (currentScan.inference_speed * 1000).toFixed(0) }}ms
        </v-chip>
        <v-chip v-if="isBeforeRetention" size="small" color="warning" variant="flat" class="ml-1">
          <v-icon start icon="mdi-image-off" size="x-small"></v-icon>
          Past Retention — No Image
        </v-chip>
      </div>
    </div>

    <!-- Bottom: Timeline Slider (Fixed Height) -->
    <v-sheet height="110" class="bg-grey-darken-3 px-4 d-flex flex-column justify-center border-t border-grey-darken-2 w-100" style="flex: 0 0 auto; z-index: 5;">
      <div class="d-flex align-center w-100 px-2">
        <div class="text-caption text-grey mr-2" style="width: 40px;">00:00</div>
        <v-btn icon="mdi-chevron-left" variant="text" size="small" @click="prevScan" :disabled="scanIndex <= 0" class="mr-2"></v-btn>
        
        <div class="flex-grow-1 position-relative pt-4">
          <!-- Event markers on the track -->
          <v-progress-linear
            v-if="loadingEvents"
            indeterminate
            absolute
            top
            color="primary"
            height="2"
            style="z-index: 3;"
          ></v-progress-linear>
          
          <!-- Retention cutoff overlay (oldest snapshot boundary) -->
          <v-tooltip v-if="retentionPct > 0 && retentionPct < 100" location="top" max-width="260">
            <template v-slot:activator="{ props: tipProps }">
              <div class="retention-overlay" v-bind="tipProps"
                   :style="{ width: retentionPct + '%' }"></div>
            </template>
            <span>Scans before {{ formatMinute(retentionCutoffMinute) }} have no image</span>
          </v-tooltip>

          <!-- Future time overlay (today only) -->
          <v-tooltip v-if="futurePct > 0" location="top" max-width="260">
            <template v-slot:activator="{ props: tipProps }">
              <div class="future-overlay" v-bind="tipProps"
                   :style="{ left: futureStartPct + '%', width: futurePct + '%' }"></div>
            </template>
            <span>{{ formatMinute(nowMinute) }} – Future time (no image yet)</span>
          </v-tooltip>

          <div 
            class="event-markers" 
            v-if="selectedSpaceId && spaceEvents.length > 0"
            @mousemove="handleTimelineMouseMove"
            @mouseleave="activeEventId = null"
          >
            <div 
              v-for="event in displayedEvents" 
              :key="'mark-'+event.id"
              :class="['event-marker', { 'active': activeEventId === event.id }]"
              :style="{ 
                left: getEventPosition(event) + '%',
                backgroundColor: event.event_type === 'occupied' ? '#F44336' : '#4CAF50'
              }"
              :title="event.event_type"
              @click.stop="jumpToEvent(event)"
            ></div>
          </div>

          <v-slider
            v-model="currentTimeMinute"
            :max="sliderMax"
            :min="0"
            :step="0.01"
            hide-details
            color="primary"
            track-color="grey-darken-1"
            thumb-label="always"
            show-ticks="always"
            :tick-size="2"
            :ticks="hourlyTicks"
            @end="onSliderChange"
            :disabled="scans.length === 0"
          >
            <template v-slot:thumb-label="{ modelValue }">
              {{ formatMinute(modelValue) }}
            </template>
          </v-slider>
        </div>

        <v-btn icon="mdi-chevron-right" variant="text" size="small" @click="nextScan" :disabled="scanIndex >= scans.length - 1" class="ml-2"></v-btn>
        <div class="text-caption text-grey ml-2" style="width: 40px;">23:59</div>
      </div>
    </v-sheet>

    <v-dialog v-model="showFeedbackDialog" max-width="500">
      <v-card>
        <v-card-title class="text-h6 bg-error text-white">
          <v-icon start icon="mdi-send" class="mr-2"></v-icon>
          Save feedback
        </v-card-title>
        <v-card-text class="pa-6">
          <p class="text-body-1">Save the selected camera image and occupancy corrections locally for model training?</p>
        </v-card-text>
        <v-divider></v-divider>
        <v-card-actions class="pa-4">
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="showFeedbackDialog = false">Cancel</v-btn>
          <v-btn color="error" variant="flat" @click="confirmSubmit">Save feedback</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="4000">
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, onMounted, computed, reactive, watch, nextTick, onUnmounted } from 'vue';
import { useRoute } from 'vue-router';
import axios from 'axios';
import { parseTimestamp } from '../utils/format';




const route = useRoute();
const getLocalDateISO = () => {
  const date = new Date();
  const year = date.getFullYear();
  const month = (date.getMonth() + 1).toString().padStart(2, '0');
  const day = date.getDate().toString().padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const selectedCameraId = ref(null);
const selectedDate = ref(getLocalDateISO());
const selectedSpaceId = ref(null);
const manualTime = ref("");

const hasPermission = (p) => {
  if (localStorage.getItem('is_admin') === 'true') return true;
  const permsStr = localStorage.getItem('permissions');
  if (!permsStr) return false;
  try {
    const perms = JSON.parse(permsStr);
    return perms.includes(p);
  } catch (e) {
    return false;
  }
};

const isFeedbackMode = ref(false);
const feedbackSubmitting = ref(false);
const showFeedbackDialog = ref(false);
const snackbar = ref({ show: false, text: '', color: 'success' });

const cameras = ref([]);
const spaces = ref([]);
const scans = ref([]);
const scanIndex = ref(0);
const currentTimeMinute = ref(0);
const currentScan = ref(null);
const spaceEvents = ref([]);
const eventPage = ref(0);
const eventsPerPage = 300;
const displayedEvents = computed(() => {
  const total = spaceEvents.value.length;
  if (total === 0) return [];
  const start = eventPage.value * eventsPerPage;
  const end = Math.min(start + eventsPerPage, total);
  return spaceEvents.value.slice(start, end);
});
const totalPages = computed(() => Math.ceil(spaceEvents.value.length / eventsPerPage));
const activeEventId = ref(null);
const loadingEvents = ref(false);
const isImageLoading = ref(false);
const oldestSnapshotDate = ref(null);
const now = ref(new Date());
let nowInterval = null;

const bgImage = ref(null);
const loading = ref(false);
const container = ref(null);

const scale = ref(1);
const stageWidth = ref(100);
const stageHeight = ref(100);

const cameraOptions = computed(() => cameras.value.map(c => ({ title: c.name, value: c.id })));

watch(cameraOptions, (newVal) => {
  console.log("[HISTORY-DEBUG] cameraOptions updated:", JSON.stringify(newVal, null, 2));
}, { immediate: true, deep: true });

const spaceOptions = computed(() => spaces.value.map(s => ({ title: s.name, value: s.id })));

const retentionCutoff = computed(() => {
  if (!oldestSnapshotDate.value || !selectedDate.value) return null;
  const dayStart = new Date(selectedDate.value + 'T00:00:00');
  const dayEnd = new Date(selectedDate.value + 'T23:59:59');
  console.log('[RETENTION] oldestSnapshotDate:', oldestSnapshotDate.value.toISOString(), 'dayStart:', dayStart.toISOString(), 'dayEnd:', dayEnd.toISOString());
  if (oldestSnapshotDate.value > dayEnd) return null;
  if (oldestSnapshotDate.value < dayStart) return dayStart;
  return oldestSnapshotDate.value;
});

const retentionCutoffMinute = computed(() => {
  if (!retentionCutoff.value) return -1;
  const m = retentionCutoff.value.getHours() * 60 + retentionCutoff.value.getMinutes();
  console.log('[RETENTION] cutoff minute:', m, 'hours:', retentionCutoff.value.getHours(), 'minutes:', retentionCutoff.value.getMinutes());
  return m;
});

const isBeforeRetention = computed(() => {
  if (!currentScan.value || !oldestSnapshotDate.value) return false;
  const ts = parseTimestamp(currentScan.value.timestamp);
  return ts < oldestSnapshotDate.value;
});


const retentionPct = computed(() => {
  if (retentionCutoffMinute.value < 0 || sliderMax.value <= 0) return 0;
  const pct = Math.min(100, (retentionCutoffMinute.value / sliderMax.value) * 100);
  console.log('[RETENTION] pct:', pct, 'cutoffMin:', retentionCutoffMinute.value, 'sliderMax:', sliderMax.value);
  return pct;
});

const stageConfig = computed(() => ({
  width: stageWidth.value,
  height: stageHeight.value,
  scaleX: scale.value,
  scaleY: scale.value
}));

const isTodaySelected = computed(() => {
  return selectedDate.value === getLocalDateISO();
});

const sliderMax = computed(() => {
  return 1440; // Full day (exclusive max, 00:00 next day)
});

const nowMinute = computed(() => {
  return now.value.getHours() * 60 + now.value.getMinutes() + (now.value.getSeconds() / 60);
});

const futurePct = computed(() => {
  if (!isTodaySelected.value || sliderMax.value <= 0) return 0;
  return Math.min(100, Math.max(0, ((sliderMax.value - nowMinute.value) / sliderMax.value) * 100));
});

const futureStartPct = computed(() => {
  if (!isTodaySelected.value || sliderMax.value <= 0) return 100;
  return (nowMinute.value / sliderMax.value) * 100;
});

function startNowTimer() {
  if (nowInterval) return;
  nowInterval = setInterval(() => {
    now.value = new Date();
  }, 1000);
}

function stopNowTimer() {
  if (nowInterval) {
    clearInterval(nowInterval);
    nowInterval = null;
  }
}

watch(isTodaySelected, (val) => {
  if (val) startNowTimer();
  else stopNowTimer();
});

const hourlyTicks = computed(() => {
  const ticks = {};
  for (let h = 0; h < 24; h++) {
    const min = h * 60;
    // Only add tick if it's within the active slider range
    if (min <= sliderMax.value) {
      ticks[min] = h % 3 === 0 ? `${h}:00` : '';
    }
  }
  return ticks;
});

async function fetchCameras() {
  console.log("[HISTORY] fetchCameras started");
  const res = await axios.get('/api/cameras', { params: { _t: Date.now() } });
  cameras.value = res.data;
  
  if (cameras.value.length > 0) {
    const queryCamId = parseInt(route.query.camera_id);
    const camInList = cameras.value.find(c => c.id === queryCamId);
    
    if (queryCamId && camInList) {
      console.log(`[DEEP-LINK] Priority Camera Found: ${camInList.name} (ID: ${queryCamId})`);
      selectedCameraId.value = queryCamId;
    } else {
      console.log(`[HISTORY] No valid deep-link camera, defaulting to: ${cameras.value[0].name}`);
      selectedCameraId.value = cameras.value[0].id;
    }
    
    // Explicitly await the camera change logic
    await onCameraChange(!!route.query.timestamp);
  }
}

async function onCameraChange(isInitialDeepLink = false) {
  if (!selectedCameraId.value) {
    console.warn("[HISTORY] onCameraChange called but no camera selected.");
    return;
  }
  console.log(`[HISTORY] onCameraChange triggered for ID: ${selectedCameraId.value} (DeepLink: ${isInitialDeepLink})`);
  
  // Load spaces for this camera
  const res = await axios.get('/api/spaces', { params: { camera_id: selectedCameraId.value } });
  spaces.value = res.data.map(s => ({
    ...s,
    rawPoints: JSON.parse(s.points),
    points: [] 
  }));

  if (isInitialDeepLink && route.query.space_id) {
    selectedSpaceId.value = parseInt(route.query.space_id);
    console.log(`[DEEP-LINK] Selecting Space ID: ${selectedSpaceId.value}`);
  } else if (!isInitialDeepLink) {
    selectedSpaceId.value = null;
  }
  
  spaceEvents.value = [];
  await fetchScans(isInitialDeepLink);
}

async function fetchScans(isInitialDeepLink = false) {
  if (!selectedCameraId.value || !selectedDate.value) return;
  
  loading.value = true;
  try {
    const localStart = new Date(`${selectedDate.value}T00:00:00`);
    const localEnd = new Date(`${selectedDate.value}T23:59:59`);
    const start = localStart.toISOString();
    const end = localEnd.toISOString();

    const res = await axios.get('/api/history/scans', {
      params: { camera_id: selectedCameraId.value, start, end }
    });
    scans.value = res.data;
    console.log(`[HISTORY] Loaded ${scans.value.length} scans for camera ${selectedCameraId.value} on ${selectedDate.value}`);

    if (scans.value.length > 0) {
      if (isInitialDeepLink && route.query.timestamp) {
        const targetTs = route.query.timestamp;
        const targetDate = parseTimestamp(targetTs);
        const minOfDay = targetDate.getHours() * 60 + targetDate.getMinutes() + (targetDate.getSeconds() / 60);
        console.log(`[DEEP-LINK] Jumping to timestamp: ${targetTs} (${minOfDay.toFixed(2)} mins)`);
        currentTimeMinute.value = minOfDay;
        onSliderChange(minOfDay);
        // Also fetch events for the selected space if deep linking
        if (selectedSpaceId.value) fetchSpaceEvents();
      } else {

        scanIndex.value = scans.value.length - 1; // Start at latest
        loadScanDetails(scans.value[scanIndex.value].id);
      }
    } else {
      currentScan.value = null;
      bgImage.value = null;
    }
  } catch (e) {
    console.error("Error fetching scans", e);
  } finally {
    loading.value = false;
  }
  fetchOldestSnapshot();
}

async function fetchOldestSnapshot() {
  if (!selectedCameraId.value) return;
  try {
    const res = await axios.get('/api/history/oldest-snapshot', {
      params: { camera_id: selectedCameraId.value }
    });
    console.log('[RETENTION] API response:', res.data);
    oldestSnapshotDate.value = res.data.oldest_timestamp ? new Date(res.data.oldest_timestamp) : null;
    if (oldestSnapshotDate.value) {
      console.log('[RETENTION] parsed Date:', oldestSnapshotDate.value.toISOString(), 'invalid:', isNaN(oldestSnapshotDate.value.getTime()));
    }
  } catch (_) {
    oldestSnapshotDate.value = null;
  }
}

function onDateChange() {
  fetchScans();
  if (selectedSpaceId.value) fetchSpaceEvents();
}

let isInternalChange = false;

  async function loadScanDetails(scanId) {
    loading.value = true;
    isImageLoading.value = true;
    try {
      const res = await axios.get(`/api/history/scans/${scanId}`);
      currentScan.value = res.data;
      
      // Update manual time input without triggering a search loop
      isInternalChange = true;
      const scanDate = parseTimestamp(currentScan.value.timestamp);
      manualTime.value = scanDate.toTimeString().split(' ')[0];

      currentTimeMinute.value = scanDate.getHours() * 60 + scanDate.getMinutes() + (scanDate.getSeconds() / 60);
      
      // Crucial: Update scanIndex so prev/next buttons work correctly
      const idx = scans.value.findIndex(s => s.id === scanId);
      if (idx !== -1) scanIndex.value = idx;
      
      nextTick(() => { isInternalChange = false; });

      // Update space occupancy from scan details.
      // ``originalOccupied`` is the AI's prediction — the backend
      // compares it against the user's final answer to compute
      // corrections for the training pipeline.
      spaces.value.forEach(s => {
        const aiPrediction = currentScan.value.occupancy[s.id]
        s.originalOccupied = !!aiPrediction
        s.occupied = s.originalOccupied
      });

      // Check if this scan has an image before trying to load it
      const scanInfo = scans.value.find(s => s.id === scanId);
      if (!scanInfo || !scanInfo.has_image) {
        bgImage.value = null;
        isImageLoading.value = false;
        loading.value = false;
        return;
      }

      // Load Image
      const img = new Image();
      img.src = `/api/cameras/${selectedCameraId.value}/snapshot?v=${scanId}`;
      img.onload = () => {
        bgImage.value = img;
        
        // Update display points based on THIS image resolution
        spaces.value.forEach(s => {
          // Heuristic: values in range [-2, 2] are treated as normalized
          const isNormalized = s.rawPoints.every(p => p >= -2.0 && p <= 2.0);
          if (isNormalized) {
            s.points = s.rawPoints.map((p, idx) => idx % 2 === 0 ? p * img.naturalWidth : p * img.naturalHeight);
          } else {
            s.points = [...s.rawPoints];
          }
        });

        updateStageSize();
        isImageLoading.value = false;
        loading.value = false;
      };
      img.onerror = () => {
        console.warn(`[HISTORY] Failed to load snapshot for scan ${scanId} (404/Pruned)`);
        bgImage.value = null;
        isImageLoading.value = false;
        loading.value = false;
      };
    } catch (e) {
      console.error("Error loading scan details", e);
      isImageLoading.value = false;
      loading.value = false;
    }
  }
async function fetchSpaceEvents() {
  // Clear immediately for visual feedback
  spaceEvents.value = [];
  eventPage.value = 0;
  
  if (!selectedSpaceId.value || !selectedDate.value) {
    return;
  }

  loadingEvents.value = true;
  try {
    const localStart = new Date(`${selectedDate.value}T00:00:00`);
    const localEnd = new Date(`${selectedDate.value}T23:59:59`);
    const start = localStart.toISOString();
    const end = localEnd.toISOString();

    const res = await axios.get(`/api/history/spaces/${selectedSpaceId.value}/events`, {
      params: { start, end }
    });
    spaceEvents.value = res.data;
  } catch (e) {
    console.error("Error fetching space events", e);
  } finally {
    loadingEvents.value = false;
  }
}

function nextEventPage() {
  if (eventPage.value < totalPages.value - 1) {
    eventPage.value++;
  }
}

function prevEventPage() {
  if (eventPage.value > 0) {
    eventPage.value--;
  }
}

function onSliderChange(minuteValue) {
  if (scans.value.length === 0) return;
  
  // Find closest scan to this high-precision minute value
  const targetDate = new Date(selectedDate.value + 'T00:00:00');
  // Use milliseconds for maximum precision (minuteValue * 60 * 1000)
  targetDate.setTime(targetDate.getTime() + (minuteValue * 60 * 1000));
  
  let closestIdx = 0;
  let minDiff = Infinity;
  
  scans.value.forEach((s, idx) => {
    const diff = Math.abs(parseTimestamp(s.timestamp) - targetDate);
    if (diff < minDiff) {

      minDiff = diff;
      closestIdx = idx;
    }
  });
  
  scanIndex.value = closestIdx;
  loadScanDetails(scans.value[closestIdx].id);
}

function onManualTimeChange(timeStr) {
  if (isInternalChange || !timeStr || scans.value.length === 0) return;
  
  const [hours, minutes, seconds] = timeStr.split(':').map(Number);
  const minOfDay = (hours || 0) * 60 + (minutes || 0);
  currentTimeMinute.value = minOfDay;
  onSliderChange(minOfDay);
}

function formatMinute(val) {
  // Use Math.round to avoid floating point precision issues (e.g. 59.99999)
  const totalSeconds = Math.round(val * 60);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

const formatTimeOnly = (ts) => {
  if (!ts) return "";
  const date = parseTimestamp(ts);
  return new Intl.DateTimeFormat('default', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
  }).format(date);
}


const toggleFeedbackMode = () => {
  if (isFeedbackMode.value) {
    isFeedbackMode.value = false;
    if (currentScan.value) loadScanDetails(currentScan.value.id);
  } else {
    isFeedbackMode.value = true;
  }
};

const toggleSpaceFeedback = (space) => {
  space.occupied = !space.occupied;
};

const submitFeedback = () => {
  if (!currentScan.value || !selectedCameraId.value) return;
  showFeedbackDialog.value = true;
};

const confirmSubmit = async () => {
  showFeedbackDialog.value = false;
  feedbackSubmitting.value = true;
  try {
    const payload = {
      scan_id: currentScan.value.id,
      camera_id: selectedCameraId.value,
      spaces: spaces.value.map(s => ({
        space_id: s.id,
        points: s.rawPoints,
        occupied: !!s.occupied,
        // AI's original prediction — backend uses this to compute
        // false-positive / false-negative corrections for retraining.
        original_occupied: s.originalOccupied ?? null,
      }))
    };
    
    await axios.post('/api/admin/feedback/submit', payload);

    snackbar.value = { show: true, text: 'Training data submitted! Thank you.', color: 'success' };
    isFeedbackMode.value = false;
    // Refresh the pending counter so the warning chip (if any) reflects
    // the new file immediately rather than waiting for the next poll.
  } catch (e) {
    console.error("Feedback failed:", e);
    snackbar.value = { show: true, text: 'Failed to submit feedback: ' + (e.response?.data?.detail || e.message), color: 'error' };
  } finally {
    feedbackSubmitting.value = false;
  }
};

function prevScan() { if (scanIndex.value > 0) { scanIndex.value--; loadScanDetails(scans.value[scanIndex.value].id); } }
function nextScan() { if (scanIndex.value < scans.value.length - 1) { scanIndex.value++; loadScanDetails(scans.value[scanIndex.value].id); } }

function handleGlobalKeyDown(e) {
  // Ignore if user is typing in an input field
  if (['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;

  if (e.key === 'ArrowLeft') {
    e.preventDefault();
    prevScan();
  } else if (e.key === 'ArrowRight') {
    e.preventDefault();
    nextScan();
  }
}

function handleTimelineMouseMove(e) {
  if (!spaceEvents.value.length) return;
  
  const rect = e.currentTarget.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const width = rect.width;
  const percentage = (x / width) * 100;
  
  let minDiff = Infinity;
  let closestId = null;
  
  spaceEvents.value.forEach(event => {
    const pos = getEventPosition(event);
    const diff = Math.abs(pos - percentage);
    // Threshold: Only highlight if reasonably close (e.g. within 5% of timeline width)
    if (diff < 5 && diff < minDiff) {
      minDiff = diff;
      closestId = event.id;
    }
  });
  
  activeEventId.value = closestId;
}

function jumpToEvent(event) {
  console.log(`[EVENT-DEBUG] Jump to Event ID: ${event.id} | Type: ${event.event_type} | Confidence: ${event.confidence ? (event.confidence * 100).toFixed(1) + '%' : 'N/A'}`);
  const eventDate = parseTimestamp(event.timestamp);
  const minOfDay = eventDate.getHours() * 60 + eventDate.getMinutes() + (eventDate.getSeconds() / 60);
  currentTimeMinute.value = minOfDay;
  onSliderChange(minOfDay);
}

function onSpaceClick(space) {
  selectedSpaceId.value = space.id;
  fetchSpaceEvents();
}

function updateStageSize() {
  if (!bgImage.value || !container.value) return;
  
  const availWidth = container.value.offsetWidth;
  const availHeight = container.value.offsetHeight;
  console.log(`[HISTORY-DEBUG] Container size: ${availWidth}x${availHeight}`);
  
  if (availWidth < 10 || availHeight < 10) return;

  const nativeWidth = bgImage.value.naturalWidth || 1;
  const nativeHeight = bgImage.value.naturalHeight || 1;
  
  const scaleX = availWidth / nativeWidth;
  const scaleY = availHeight / nativeHeight;
  const newScale = Math.min(scaleX, scaleY); // Full fit, no padding
  
  scale.value = newScale;
  stageWidth.value = nativeWidth * newScale;
  stageHeight.value = nativeHeight * newScale;
}

function getSpaceFill(space) {
  if (selectedSpaceId.value === space.id) {
    return space.occupied ? 'rgba(244, 67, 54, 0.6)' : 'rgba(76, 175, 80, 0.6)';
  }
  return space.occupied ? 'rgba(244, 67, 54, 0.3)' : 'rgba(76, 175, 80, 0.3)';
}

function isCorrected(space) {
  // A "correction" is a space where the user flipped the AI's
  // answer. We only know the original when the scan was loaded;
  // undefined means we never had an original to compare against.
  if (space.originalOccupied === undefined || space.originalOccupied === null) {
    return false;
  }
  return !!space.occupied !== !!space.originalOccupied;
}

const correctionsCount = computed(() => spaces.value.filter(isCorrected).length)

function getSpaceStroke(space) {
  if (selectedSpaceId.value === space.id) return '#FFF';
  // Amber stroke on corrections so the user can see at a glance
  // which spaces they're flipping before submitting.
  if (isFeedbackMode.value && isCorrected(space)) return '#FFB300';
  return space.occupied ? '#F44336' : '#4CAF50';
}

function getEventPosition(event) {
  // Range is always 0 to sliderMax for correct alignment
  const current = parseTimestamp(event.timestamp);
  const eventMin = current.getHours() * 60 + current.getMinutes() + (current.getSeconds() / 60);
  
  // Percentage across the ACTIVE slider track
  return Math.max(0, Math.min(100, (eventMin / sliderMax.value) * 100));
}

function isEventSelected(event) {
  if (!currentScan.value) return false;
  // If event timestamp is within 2 seconds of current scan (handles minor clock drift/db rounding)
  return Math.abs(parseTimestamp(event.timestamp) - parseTimestamp(currentScan.value.timestamp)) < 2000;
}

function formatDateTime(ts) {
  return new Intl.DateTimeFormat('default', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'
  }).format(parseTimestamp(ts));
}

function formatTime(ts) {
  const date = parseTimestamp(ts);
  return new Intl.DateTimeFormat('default', {
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  }).format(date);
}


let resizeObserver = null;
let resizeTimeout = null;

onMounted(async () => {
  // If we have a deep-linked timestamp, extract the date from it
  if (route.query.timestamp) {
    const ts = route.query.timestamp;
    selectedDate.value = ts.split('T')[0];
  }


  fetchCameras();
  if (isTodaySelected.value) startNowTimer();
  
  window.addEventListener('keydown', handleGlobalKeyDown);

  resizeObserver = new ResizeObserver(() => {
    if (resizeTimeout) clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(updateStageSize, 50);
  });
  if (container.value) {
    resizeObserver.observe(container.value);
  }
});

onUnmounted(() => {
  if (nowInterval) clearInterval(nowInterval);
  if (resizeTimeout) clearTimeout(resizeTimeout);
  if (resizeObserver) resizeObserver.disconnect();
  window.removeEventListener('keydown', handleGlobalKeyDown);
});
</script>

<style scoped>
.canvas-container {
  overflow: hidden;
  user-select: none;
}
.center-stage {
  display: flex;
  align-items: center;
  justify-content: center;
}
.status-bar {
  position: absolute;
  top: 20px;
  background: rgba(0,0,0,0.7);
  backdrop-filter: blur(4px);
  z-index: 10;
}
.feedback-overlay {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 20;
  pointer-events: none;
}
.feedback-overlay > * {
  pointer-events: auto;
}
.timeline-wrapper {
  padding: 0 12px;
}
.retention-overlay {
  position: absolute;
  top: 22px; left: 12px;
  height: 20px;
  background: rgba(0, 0, 0, 0.35);
  border-right: 2px dashed rgba(255, 183, 77, 0.7);
  pointer-events: auto;
  cursor: help;
  z-index: 2;
  border-radius: 4px 0 0 4px;
}
.future-overlay {
  position: absolute;
  top: 22px;
  height: 20px;
  background: rgba(33, 150, 243, 0.15);
  border-left: 2px dashed rgba(33, 150, 243, 0.5);
  pointer-events: auto;
  cursor: help;
  z-index: 2;
  border-radius: 0 4px 4px 0;
}
.event-markers {
  position: absolute;
  top: 8px; /* Moved up from 18px */
  left: 12px; /* Matches v-slider default track padding */
  right: 12px; /* Matches v-slider default track padding */
  height: 12px;
  pointer-events: none;
  z-index: 1;
}
.event-marker {
  position: absolute;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  transform: translateX(-50%);
  box-shadow: 0 0 4px rgba(0, 0, 0, 0.5);
  pointer-events: auto;
  cursor: pointer;
  border: 1px solid white;
  z-index: 2;
  transition: transform 0.1s ease-in-out, box-shadow 0.1s ease-in-out;
}
.event-marker:hover, .event-marker.active {
  transform: translateX(-50%) scale(1.5);
  box-shadow: 0 0 8px rgba(255, 255, 255, 0.8);
  z-index: 10;
}
/* Enlarged click area */
.event-marker::after {
  content: '';
  position: absolute;
  top: -10px;
  left: -10px;
  right: -10px;
  bottom: -10px;
  border-radius: 50%;
}
.text-xxs {
  font-size: 0.65rem;
}
:deep(.v-slider-thumb__label) {
  transform: translate(-50%, -8px) !important;
}
</style>
