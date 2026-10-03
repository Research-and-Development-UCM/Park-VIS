<template>
  <v-container fluid class="bg-grey-lighten-4 pa-4 parking-dashboard">

    <!-- Header Section with Parking Lot Selector -->
    <div class="dashboard-header mb-6 w-100">
      <v-row align="center" justify="space-between" class="g-4" style="min-height: 64px;">
        <v-col cols="12" lg="7" md="6" class="d-flex align-center flex-wrap ga-4">
          <div class="d-flex align-center flex-shrink-0">
            <v-sheet
              color="primary-lighten-5"
              rounded="lg"
              class="d-flex align-center justify-center mr-4 flex-shrink-0"
              style="width: 48px; height: 48px; background: rgba(var(--v-theme-primary), 0.1);"
            >
              <v-icon size="28" color="primary">mdi-parking</v-icon>
            </v-sheet>
            <div class="flex-shrink-0">
              <div class="text-h5 text-grey-darken-4 text-no-wrap">The parking observatory.</div>
              <div class="text-caption text-grey-darken-1 mt-n1 text-no-wrap">Every space. A little more clarity.</div>
            </div>
          </div>
          
          <!-- Parking Lot Selector Dropdown (Sleek Menu Button) -->
          <v-menu v-if="groupOptions.length > 1" offset-y transition="scale-transition">
            <template v-slot:activator="{ props }">
              <v-btn
                v-bind="props"
                variant="flat"
                bg-color="white"
                rounded="xl"
                class="px-4 text-none font-weight-bold elevation-2 border flex-shrink-0"
                append-icon="mdi-chevron-down"
                prepend-icon="mdi-filter-variant"
                style="height: 40px;"
              >
                <span class="text-truncate" style="max-width: 220px;">
                  {{ selectedGroupId === 'all' || !selectedGroupId ? 'All Parking Lots' : selectedGroupName }}
                </span>
              </v-btn>
            </template>
            <v-card class="elevation-8 border" rounded="lg" min-width="240">
              <v-list density="compact" class="pa-1">
                <v-list-item
                  v-for="opt in groupOptions"
                  :key="opt.id"
                  @click="selectedGroupId = opt.id"
                  :active="selectedGroupId === opt.id"
                  rounded="lg"
                  color="primary"
                  class="mb-1"
                >
                  <v-list-item-title class="font-weight-medium text-body-2">{{ opt.name }}</v-list-item-title>
                </v-list-item>
              </v-list>
            </v-card>
          </v-menu>
        </v-col>
        
        <!-- Combined Group Stats Card (Aggregated Summary) -->
        <v-col v-if="selectedGroupStats" cols="12" lg="5" md="6" class="d-flex align-center justify-md-end">
          <v-sheet
            elevation="0"
            rounded="xl"
            class="pa-3 px-5 bg-white d-flex align-center border elevation-1"
            style="gap: 20px; height: 64px;"
          >
            <!-- Circular Progress on the left -->
            <div class="position-relative d-flex align-center mr-2">
              <v-progress-circular
                :model-value="selectedGroupStats.rate"
                size="44"
                width="4"
                color="primary"
                bg-color="grey-lighten-3"
              >
                <span class="text-caption font-weight-black" style="font-size: 0.72rem !important; letter-spacing: 0;">
                  {{ selectedGroupStats.rate }}%
                </span>
              </v-progress-circular>
            </div>
            
            <v-divider vertical class="my-2"></v-divider>
            
            <div class="stat-item text-left">
              <div class="text-caption text-grey font-weight-bold line-height-1 mb-1" style="font-size: 0.7rem !important; letter-spacing: 0.5px;">TOTAL</div>
              <div class="text-h6 font-weight-black text-grey-darken-3 line-height-1">{{ selectedGroupStats.total }}</div>
            </div>
            
            <v-divider vertical class="my-2"></v-divider>
            
            <div class="stat-item text-left">
              <div class="text-caption text-grey font-weight-bold line-height-1 mb-1" style="font-size: 0.7rem !important; letter-spacing: 0.5px;">OCCUPIED</div>
              <div class="text-h6 font-weight-black text-error line-height-1">{{ selectedGroupStats.occupied }}</div>
            </div>
            
            <v-divider vertical class="my-2"></v-divider>
            
            <div class="stat-item text-left">
              <div class="text-caption text-grey font-weight-bold line-height-1 mb-1" style="font-size: 0.7rem !important; letter-spacing: 0.5px;">VACANT</div>
              <div class="text-h6 font-weight-black text-success line-height-1">{{ selectedGroupStats.available }}</div>
            </div>
          </v-sheet>
        </v-col>
      </v-row>
    </div>

    <div class="d-flex justify-end mb-4">
      <v-btn-toggle v-model="dashboardView" mandatory density="comfortable" color="primary" variant="outlined" aria-label="Dashboard view">
        <v-btn value="plan" prepend-icon="mdi-view-grid-outline">Top-down view</v-btn>
        <v-btn value="camera" prepend-icon="mdi-camera-outline">Camera view</v-btn>
      </v-btn-toggle>
    </div>
    <template v-if="dashboardView === 'plan' && filteredCameras.length"><ParkingPlan v-for="lot in visibleLots" :key="lot.id" :lot="lot.id" :lot-name="lot.name" :cameras="lot.cameras" @select="space => handleSpaceClick(null, space)" /></template>
    <v-row v-else-if="filteredCameras.length > 0" align="stretch" justify="center" class="camera-row">
      <v-col
        v-for="cam in filteredCameras"
        :key="cam.id"
        cols="12"
        sm="12"
        :md="cameraCols.md"
        :lg="cameraCols.lg"
        :xl="cameraCols.xl"
        v-intersect="{
          handler: (isIntersecting) => onIntersect(isIntersecting, cam),
          options: { threshold: [0.1] }
        }"
      >
        <v-card elevation="2" class="rounded-lg overflow-hidden d-flex flex-column camera-card">
          <v-toolbar color="white" density="comfortable" class="px-3">
            <v-tooltip location="top" :text="cam.name" open-delay="500" :disabled="!cam.showTooltip">
              <template v-slot:activator="{ props }">
                <div 
                  v-bind="props"
                  class="text-subtitle-1 font-weight-bold text-truncate" 
                  style="max-width: 60%; cursor: default;"
                  @mouseenter="(e) => cam.showTooltip = e.target.scrollWidth > e.target.clientWidth"
                >
                  {{ cam.name }}
                </div>
              </template>
            </v-tooltip>
            
            <v-spacer></v-spacer>
            
            <v-chip 
              size="small" 
              :color="cam.occupiedCount > 0 ? 'error' : 'success'" 
              variant="flat" 
              class="mr-1"
              :class="{ 'cursor-pointer': hasPermission('manage_cameras') }"
              @click="hasPermission('manage_cameras') ? router.push('/editor/' + cam.id) : null"
              :title="hasPermission('manage_cameras') ? 'Click to edit parking spaces' : ''"
            >
              {{ cam.occupiedCount }}/{{ cam.totalSpaces }}
            </v-chip>
            
            <v-btn icon="mdi-arrow-expand" size="small" variant="text" @click="expandCamera(cam)"></v-btn>
          </v-toolbar>
          
          <v-divider></v-divider>
          
          <div :id="'konva-wrapper-' + cam.id" class="konva-wrapper bg-black">
            <!-- Top Overlay for Metadata -->
            <div class="camera-metadata-overlay pa-2 d-flex align-center w-100">
              <v-chip size="x-small" variant="flat" color="grey-darken-4" class="text-white mr-1 backdrop-blur border">
                <v-icon start icon="mdi-clock-outline" size="x-small"></v-icon>
                {{ getRelativeTime(cam.lastUpdated) }}
              </v-chip>
              <v-chip size="x-small" variant="flat" color="grey-darken-4" class="text-white backdrop-blur border">
                <v-icon start icon="mdi-speedometer" size="x-small"></v-icon>
                <template v-if="cam.isProcessing">
                  <span class="mr-1">(...)</span>
                  <span>Processing...</span>
                </template>
                <template v-else>
                  {{ cam.inferenceTime > 0 ? (cam.inferenceTime * 1000).toFixed(0) + 'ms' : 'N/A' }}
                </template>
              </v-chip>
            </div>

            <v-stage 
              v-if="cam.bgImage"
              @click="(e) => handleStageClick(e, cam)"
              @mousemove="(e) => handleMouseMove(e, cam)"
              :config="{
                width: cam.stageWidth,
                height: cam.stageHeight,
                scaleX: cam.scale,
                scaleY: cam.scale
              }"
            >
              <v-layer>
                <v-image :config="{ image: cam.bgImage }" />
                <v-line
                  v-for="space in cam.spaces"
                  :key="space.id"
                  @click="(e) => handleSpaceClick(e, space)"
                  @mouseenter="(e) => { 
                    e.target.getStage().container().style.cursor = 'pointer';
                    cam.hoveredSpace = space;
                  }"
                  @mouseleave="(e) => { 
                    e.target.getStage().container().style.cursor = 'default';
                    if (cam.hoveredSpace?.id === space.id) cam.hoveredSpace = null;
                  }"
                  :config="{
                    points: space.points,
                    fill: space.occupied ? 'rgba(244, 67, 54, 0.4)' : 'rgba(76, 175, 80, 0.4)',
                    stroke: space.occupied ? '#F44336' : '#4CAF50',
                    strokeWidth: 2 / cam.scale,
                    closed: true,
                    name: 'space-polygon',
                    listening: true
                  }"
                />
                <v-label
                  v-if="cam.hoveredSpace"
                  :config="{
                    x: cam.tooltipPos.x,
                    y: cam.tooltipPos.y - 10,
                    opacity: 0.8,
                    listening: false
                  }"
                >
                  <v-tag :config="{ fill: 'black', pointerDirection: 'bottom', pointerWidth: 10, pointerHeight: 10, lineJoin: 'round', shadowColor: 'black', shadowBlur: 10, shadowOffset: { x: 5, y: 5 }, shadowOpacity: 0.5 }" />
                  <v-text :config="{ text: cam.hoveredSpace.name, fontSize: 14 / cam.scale, padding: 5 / cam.scale, fill: 'white' }" />
                </v-label>
              </v-layer>
            </v-stage>
            
            <div v-if="!cam.bgImage" class="d-flex align-center justify-center fill-height w-100 position-absolute">
              <v-progress-circular indeterminate color="primary"></v-progress-circular>
            </div>
          </div>
        </v-card>
      </v-col>
    </v-row>
    
    <v-row v-else-if="!loading" class="fill-height align-center justify-center">
      <v-col cols="12" class="text-center">
        <v-icon size="64" color="grey">mdi-camera-off</v-icon>
        <div class="text-h6 mt-4 text-grey">No cameras found</div>
        <v-btn color="primary" class="mt-4" to="/cameras">Add a Camera</v-btn>
      </v-col>
    </v-row>

    <div v-else class="d-flex align-center justify-center fill-height w-100">
       <v-progress-circular indeterminate size="64" color="primary"></v-progress-circular>
    </div>

    <v-footer app v-if="cameraStatus.length > 0" height="36" class="bg-grey-lighten-4 border-t d-flex align-center px-4">
      <div class="d-flex align-center mr-6">
        <v-icon :icon="isOverloaded ? 'mdi-alert-circle' : 'mdi-speedometer'" size="small" :color="isOverloaded ? 'error' : 'grey'" class="mr-2"></v-icon>
        <span class="text-caption text-grey-darken-1">
          Total System Load: 
          <span :class="['font-weight-bold', isOverloaded ? 'text-error' : 'text-grey-darken-3']">
            {{ totalInferenceTime.toFixed(1) }}s
          </span>
          <span class="text-grey-lighten-1 mx-1">/</span>
          <span>{{ inferenceInterval }}s interval</span>
        </span>
      </div>

      <v-divider vertical class="mx-2 my-2"></v-divider>

      <div class="d-flex align-center ml-4">
        <span class="text-caption text-grey-darken-1">
          Average Speed: 
          <span class="font-weight-bold text-grey-darken-3">{{ (overallInferenceTime * 1000).toFixed(0) }}ms</span>
          <span class="text-grey ml-1">per camera</span>
        </span>
      </div>

      <v-spacer></v-spacer>
      
      <v-tooltip v-if="isOverloaded" location="top">
        <template v-slot:activator="{ props }">
          <v-chip v-bind="props" size="x-small" color="error" variant="flat" class="font-weight-bold">
            SYSTEM OVERLOADED
          </v-chip>
        </template>
        Total inference time exceeds the update interval. The system cannot keep up with real-time requests.
      </v-tooltip>
    </v-footer>

    <!-- Expansion Dialog -->
    <v-dialog v-model="expandedDialog" fullscreen transition="dialog-bottom-transition">
      <v-card v-if="selectedCamera" class="bg-black d-flex flex-column fill-height">
        <v-toolbar color="white" density="comfortable" class="flex-grow-0">
          <v-btn icon="mdi-close" @click="expandedDialog = false"></v-btn>
          <v-toolbar-title>{{ expandedMeta.name }}</v-toolbar-title>
          <v-spacer></v-spacer>
          <v-btn
            v-if="hasPermission('view_history')"
            prepend-icon="mdi-history"
            variant="tonal"
            color="primary"
            class="mr-3 text-none"
            :to="{ path: '/history', query: { camera_id: selectedCamera.id } }"
            @click="expandedDialog = false"
          >
            History Review
          </v-btn>
          <v-chip color="primary" variant="flat" class="mr-4">
            Occupied: {{ expandedMeta.occupiedCount }} / {{ expandedMeta.totalSpaces }}
          </v-chip>
        </v-toolbar>
        
        <v-card-text class="pa-0 flex-grow-1 d-flex align-center justify-center overflow-hidden" ref="expandedContainer">
          <div class="expanded-stage-wrapper" v-if="expandedImage">
            <v-stage 
              :key="expandedStageKey"
              @click="(e) => handleStageClick(e, selectedCamera)"
              @mousemove="(e) => handleMouseMove(e, selectedCamera, expandedScale)"
              :config="{
                width: expandedWidth,
                height: expandedHeight,
                scaleX: expandedScale,
                scaleY: expandedScale
              }"
            >
              <v-layer>
                <v-image :config="{ image: expandedImage }" :key="expandedMeta.scanId" />
                <v-line
                  v-for="space in expandedSpaces"
                  :key="space.id"
                  @click="(e) => handleSpaceClick(e, space)"
                  @mouseenter="(e) => {
                    e.target.getStage().container().style.cursor = 'pointer';
                    selectedCamera.hoveredSpace = space;
                  }"
                  @mouseleave="(e) => {
                    e.target.getStage().container().style.cursor = 'default';
                    if (selectedCamera.hoveredSpace?.id === space.id) selectedCamera.hoveredSpace = null;
                  }"
                  :config="{
                    points: space.points,
                    fill: space.occupied ? 'rgba(244, 67, 54, 0.4)' : 'rgba(76, 175, 80, 0.4)',
                    stroke: space.occupied ? '#F44336' : '#4CAF50',
                    strokeWidth: 2 / expandedScale,
                    closed: true,
                    name: 'space-polygon',
                    listening: true
                  }"
                />
                <v-label
                  v-if="selectedCamera.hoveredSpace"
                  :config="{
                    x: selectedCamera.tooltipPos.x,
                    y: selectedCamera.tooltipPos.y - 15,
                    opacity: 0.8,
                    listening: false
                  }"
                >
                  <v-tag :config="{ fill: 'black', pointerDirection: 'bottom', pointerWidth: 10, pointerHeight: 10, lineJoin: 'round' }" />
                  <v-text :config="{ text: selectedCamera.hoveredSpace.name, fontSize: 18 / expandedScale, padding: 5 / expandedScale, fill: 'white' }" />
                </v-label>
              </v-layer>
            </v-stage>
          </div>
        </v-card-text>
      </v-card>
    </v-dialog>
    <SpaceHistoryDialog v-model="historyVisible" :space="targetSpace" />
  </v-container>
</template>

<script setup>
import { ref, onMounted, onUnmounted, reactive, nextTick, computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import axios from 'axios';
import { parseTimestamp } from '../utils/format';
import SpaceHistoryDialog from './SpaceHistoryDialog.vue';
import ParkingPlan from './ParkingPlan.vue';


const route = useRoute();
const router = useRouter();
const cameraStatus = ref([]);
const dashboardView = ref('plan');
watch(dashboardView, async () => {
  await nextTick();
  if (dashboardView.value === 'camera') filteredCameras.value.forEach(updateStageSize);
});
const groups = ref([]);
function selectionFromLot(lot) {return typeof lot === 'string' && lot.startsWith('group:') ? Number(lot.split(':')[1]) : typeof lot === 'string' && lot.startsWith('camera:') ? lot : null;}
const selectedGroupId = ref(selectionFromLot(route.query.lot));
watch(()=>route.query.lot,lot=>{selectedGroupId.value=selectionFromLot(lot);});

const groupOptions = computed(() => {
  return [
    { id: 'all', name: 'All Parking Lots / Cameras' },
    ...groups.value,
    ...cameraStatus.value.map(c=>({id:`camera:${c.id}`,name:`${c.name} · single camera`}))
  ];
});

const selectedGroupStats = computed(() => {
  if (!selectedGroupId.value || selectedGroupId.value === 'all') {
    return null;
  }
  const group = groups.value.find(g => g.id === selectedGroupId.value);
  if (!group) return null;
  
  let total = 0;
  let occupied = 0;
  
  const cameraIdsInGroup = group.camera_ids || [];
  cameraStatus.value.forEach(cam => {
    if (cameraIdsInGroup.includes(cam.id)) {
      total += cam.totalSpaces || 0;
      occupied += cam.occupiedCount || 0;
    }
  });
  
  const available = total - occupied;
  const rate = total > 0 ? Math.round((occupied / total) * 100) : 0;
  
  return {
    total,
    occupied,
    available,
    rate
  };
});

const selectedGroupName = computed(() => {
  if(typeof selectedGroupId.value==='string' && selectedGroupId.value.startsWith('camera:'))return cameraStatus.value.find(c=>`camera:${c.id}`===selectedGroupId.value)?.name||'';
  const group = groups.value.find(g => g.id === selectedGroupId.value);
  return group ? group.name : '';
});

const filteredCameras = computed(() => {
  if(typeof selectedGroupId.value==='string' && selectedGroupId.value.startsWith('camera:'))return cameraStatus.value.filter(c=>`camera:${c.id}`===selectedGroupId.value);
  if (!selectedGroupId.value || selectedGroupId.value === 'all') {
    return cameraStatus.value;
  }
  const group = groups.value.find(g => g.id === selectedGroupId.value);
  if (!group) return cameraStatus.value;
  const cameraIdsInGroup = group.camera_ids || [];
  return cameraStatus.value.filter(c => cameraIdsInGroup.includes(c.id));
});

const visibleLots = computed(() => {
  if (selectedGroupId.value && selectedGroupId.value !== 'all') {
    const id=typeof selectedGroupId.value==='number'?`group:${selectedGroupId.value}`:selectedGroupId.value;
    return [{id,name:selectedGroupName.value,cameras:filteredCameras.value}];
  }
  const grouped=new Set(groups.value.flatMap(g=>g.camera_ids||[]));
  return [...groups.value.map(g=>({id:`group:${g.id}`,name:g.name,cameras:cameraStatus.value.filter(c=>g.camera_ids?.includes(c.id))})),...cameraStatus.value.filter(c=>!grouped.has(c.id)).map(c=>({id:`camera:${c.id}`,name:c.name,cameras:[c]}))].filter(l=>l.cameras.length);
});

watch(selectedGroupId, async () => {
  await nextTick();
  setTimeout(() => {
    filteredCameras.value.forEach(updateStageSize);
  }, 100);
});

const loading = ref(true);
const isFetching = ref(false);
const sockets = ref([]);
const inferenceInterval = ref(60);
const now = ref(new Date());
let nowInterval = null;

const hasPermission = (p) => {
  if (localStorage.getItem('is_admin') === 'true') return true;
  const permsStr = localStorage.getItem('permissions');
  if (!permsStr) return false;
  try {
    const perms = JSON.parse(permsStr);
    return Array.isArray(perms) && perms.includes(p);
  } catch (e) {
    return false;
  }
};

// Adaptive grid columns for the camera cards. With one camera the
// standard 4-of-12 (lg) / 3-of-12 (xl) tile looks tiny and lonely
// against a wide viewport. Scale up so a single card takes the
// majority of the row, and step down as more cameras join. At sm
// and below every camera is full-width regardless of count, so the
// responsive tiers only kick in at md+.
const cameraCols = computed(() => {
  const n = filteredCameras.value.length
  if (n <= 1) return { md: 12, lg: 10, xl: 8 }
  if (n === 2) return { md: 12, lg: 6, xl: 6 }
  if (n === 3) return { md: 6, lg: 4, xl: 4 }
  return { md: 6, lg: 4, xl: 3 }
})

const closeAllSockets = () => {
  // H9 audit fix: also clear the pending reconnect timer so a
  // scheduled retry doesn't fire after the component unmounts.
  if (globalReconnectTimer) {
    clearTimeout(globalReconnectTimer);
    globalReconnectTimer = null;
  }
  if (sockets.value.length > 0) {
    sockets.value.forEach(s => {
      s.onmessage = null;
      s.onclose = null;
      s.onerror = null;
      s.close();
    });
    sockets.value = [];
  }
};

const totalInferenceTime = computed(() => {
  return cameraStatus.value
    .filter(c => {
      // Ignore cameras that haven't updated in > 4 intervals
      const staleThreshold = inferenceInterval.value * 4 * 1000;
      return (now.value.getTime() - c.lastUpdated.getTime()) < staleThreshold;
    })
    .reduce((acc, c) => acc + (c.inferenceTime || 0), 0);
});

const overallInferenceTime = computed(() => {
  const activeCams = cameraStatus.value.filter(c => {
    const staleThreshold = inferenceInterval.value * 4 * 1000;
    const isRecentlyUpdated = (now.value.getTime() - c.lastUpdated.getTime()) < staleThreshold;
    return isRecentlyUpdated && c.inferenceTime > 0;
  });
  if (activeCams.length === 0) return 0;
  return totalInferenceTime.value / activeCams.length;
});

const isOverloaded = computed(() => {
  return totalInferenceTime.value > inferenceInterval.value;
});

const expandedDialog = ref(false);
const expandedStageKey = ref(0);
const selectedCamera = ref(null);
const isTransitioning = ref(false);
const expandedWidth = ref(0);
const expandedHeight = ref(0);
const expandedScale = ref(1);
const expandedContainer = ref(null);
const expandedSpaces = ref([]);
const expandedImage = ref(null);
const expandedMeta = reactive({
  name: '',
  occupiedCount: 0,
  totalSpaces: 0,
  scanId: null
});

// Re-sync points when closing expansion
watch(expandedDialog, (val) => {
  if (!val) {
    // RESET all buffer state so next camera opens clean
    expandedImage.value = null;
    expandedSpaces.value = [];
    expandedMeta.name = '';
    expandedMeta.occupiedCount = 0;
    expandedMeta.totalSpaces = 0;
    expandedMeta.scanId = null;
    selectedCamera.value = null;
  } else if (selectedCamera.value && selectedCamera.value.bgImage) {
    updateCameraPoints(selectedCamera.value, selectedCamera.value.bgImage);
  }
});

const historyVisible = ref(false);
const targetSpace = ref(null);

function onIntersect(isIntersecting, cam) {
  cam.isIntersecting = isIntersecting;
  if (isIntersecting && !cam.bgImage) {
    loadGridImage(cam);
  }
}

function loadGridImage(cam) {
  const img = new Image();
  // v is scan_id to bypass browser cache
  img.src = `/api/cameras/${cam.id}/snapshot?v=${cam.lastScanId || ''}&width=600`;
  img.onload = () => {
    cam.bgImage = img;
    updateStageSize(cam);
    updateCameraPoints(cam, img);
    cam.lastWidth = img.naturalWidth;
    cam.lastHeight = img.naturalHeight;
  };
}

function handleSpaceClick(e, space) {
  if (e) e.cancelBubble = true;
  targetSpace.value = space;
  historyVisible.value = true;
}

function handleStageClick(e, cam) {
  if (e.target.attrs.name === 'space-polygon') return;
  expandCamera(cam);
}

function handleMouseMove(e, cam, customScale = null) {
  const stage = e.target.getStage();
  const pos = stage.getPointerPosition();
  if (!pos) return;
  const scale = customScale || cam.scale;
  cam.tooltipPos = { x: pos.x / scale, y: pos.y / scale };
}

const updateCameraPoints = (cam, img) => {
  if (!img.naturalWidth || !cam.spaces.length) return;
  const nativeWidth = img.naturalWidth;
  const nativeHeight = img.naturalHeight;

  cam.spaces.forEach(s => {
    if (!s.rawPoints) return;
    const isNormalized = s.rawPoints.every(p => p >= -2.0 && p <= 2.0);
    if (isNormalized) {
      // Atomic update: replace the whole array at once
      s.points = s.rawPoints.map((p, idx) => idx % 2 === 0 ? p * nativeWidth : p * nativeHeight);
    } else {
      s.points = [...s.rawPoints];
    }
  });
};

async function fetchCameras() {
  if (isFetching.value) return;
  isFetching.value = true;
  
  // PURGE existing connections
  closeAllSockets();
  
  if (cameraStatus.value.length === 0) loading.value = true;

  try {
    const fullRes = await axios.get('/api/dashboard/full');
    const allData = fullRes.data;

    // Fetch interval separately so failure doesn't break the dashboard
    try {
      const intervalRes = await axios.get('/api/settings/inference_interval');
      inferenceInterval.value = parseInt(intervalRes.data.value) || 60;
    } catch (e) {
      console.warn('Failed to fetch inference_interval, using default.');
      inferenceInterval.value = 60;
    }

    // Fetch camera groups
    try {
      const groupsRes = await axios.get('/api/camera-groups');
      groups.value = groupsRes.data || [];
      if (groups.value.length > 0 && !selectedGroupId.value) {
        selectedGroupId.value = 'all';
      }
    } catch (e) {
      console.warn('Failed to fetch camera groups:', e);
      groups.value = [];
    }
    
    cameraStatus.value = allData.map((data) => {
      const initialMeta = data.metadata || {};

      const camState = reactive({
        id: data.id,
        name: data.name,
        source_type: data.source_type,
        bgImage: null,
        spaces: data.spaces.map(s => ({
          id: s.id,
          camera_id: s.camera_id,
          name: s.name,
          rawPoints: JSON.parse(s.points),
          points: [],
          occupied: !!(data.occupancy && data.occupancy[s.id]?.occupied),
          occupancyKnown: typeof data.occupancy?.[s.id]?.occupied === 'boolean'
        })),
        occupiedCount: 0,
        totalSpaces: data.spaces.length,
        inferenceTime: initialMeta.inference_speed || 0,
        isProcessing: !!initialMeta.is_processing,
        lastUpdated: parseTimestamp(initialMeta.timestamp) || new Date(),
        lastScanId: initialMeta.scan_id || null,

        lastWidth: 0,
        lastHeight: 0,
        stageWidth: 100,
        stageHeight: 100,
        scale: 1,
        hoveredSpace: null,
        tooltipPos: { x: 0, y: 0 },
        isIntersecting: false
      });

      // Calculate initial occupied count
      camState.occupiedCount = camState.spaces.filter(s => s.occupied).length;

      return camState;
    });
    
    // Connect to the single global websocket
    connectGlobalWs();
    
    window.addEventListener('resize', handleResize);
  } catch (error) {
    console.error('Error fetching cameras:', error);
  } finally {
    loading.value = false;
    isFetching.value = false;
  }
}

function updateStageSize(cam) {
  if (!cam.bgImage) return;
  const wrapper = document.getElementById('konva-wrapper-' + cam.id);
  const containerWidth = wrapper?.clientWidth || 400;
  const nativeWidth = cam.bgImage.naturalWidth;
  const nativeHeight = cam.bgImage.naturalHeight;
  cam.scale = containerWidth / nativeWidth;
  cam.stageWidth = containerWidth;
  cam.stageHeight = nativeHeight * cam.scale;
}

function handleResize() {
  cameraStatus.value.forEach(updateStageSize);
  if (expandedDialog.value && selectedCamera.value) {
    updateExpandedSize();
  }
}

function expandCamera(cam) {
  selectedCamera.value = cam;
  expandedDialog.value = true;
  
  // Load full resolution image for the zoomed view
  loadFullRes(cam);
  
  nextTick(() => {
    updateExpandedSize();
  });
}

function loadFullRes(cam) {
  const img = new Image();
  const targetScanId = cam.lastScanId;
  img.src = `/api/cameras/${cam.id}/snapshot?v=${targetScanId || ''}`;
  img.onload = () => {
    if (cam.lastScanId !== targetScanId) return;

    const padding = 32;
    const availableWidth = window.innerWidth - padding;
    const availableHeight = window.innerHeight - 64 - padding; 
    
    const nativeWidth = img.naturalWidth;
    const nativeHeight = img.naturalHeight;
    
    const scaleX = availableWidth / nativeWidth;
    const scaleY = availableHeight / nativeHeight;
    const scale = Math.min(scaleX, scaleY);
    
    // Calculate new points locally first
    const newSpaces = cam.spaces.map(s => {
      const isNormalized = s.rawPoints.every(p => p >= -2.0 && p <= 2.0);
      return {
        ...s,
        points: isNormalized 
          ? s.rawPoints.map((p, idx) => idx % 2 === 0 ? p * nativeWidth : p * nativeHeight)
          : [...s.rawPoints]
      };
    });
    
    // COMMIT: Apply everything to the local buffer state in one go
    expandedScale.value = scale;
    expandedWidth.value = nativeWidth * scale;
    expandedHeight.value = nativeHeight * scale;
    
    expandedSpaces.value = newSpaces;
    expandedMeta.name = cam.name;
    expandedMeta.occupiedCount = cam.occupiedCount;
    expandedMeta.totalSpaces = cam.totalSpaces;
    expandedMeta.scanId = targetScanId;
    expandedImage.value = img;
    
    nextTick(() => {
      expandedStageKey.value++;
    });
  };
}

function updateExpandedSize() {
  if (!selectedCamera.value) return;
  // Use fullResImage if available, fallback to the current grid image
  const displayImg = selectedCamera.value.fullResImage || selectedCamera.value.bgImage;
  if (!displayImg) return;
  
  const padding = 32;
  const availableWidth = window.innerWidth - padding;
  const availableHeight = window.innerHeight - 64 - padding; 
  
  const nativeWidth = displayImg.naturalWidth;
  const nativeHeight = displayImg.naturalHeight;
  
  const scaleX = availableWidth / nativeWidth;
  const scaleY = availableHeight / nativeHeight;
  const scale = Math.min(scaleX, scaleY);
  
  expandedScale.value = scale;
  expandedWidth.value = nativeWidth * scale;
  expandedHeight.value = nativeHeight * scale;

  // CRITICAL: Re-calculate shapes for the current expansion resolution
  updateCameraPoints(selectedCamera.value, displayImg);
}

let reconnectAttempts = 0;
let globalReconnectTimer = null;
const MAX_RECONNECT_ATTEMPTS = 5;

function connectGlobalWs() {
  // CRITICAL: Ensure only one global socket is active
  if (sockets.value.length > 0) return;

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${window.location.host}/api/live/ws`);
  
  ws.onopen = () => {
    // Suppress noisy log
    reconnectAttempts = 0;
  };

  ws.onmessage = ev => {
    const allUpdates = JSON.parse(ev.data);
    
    // Handle system-wide updates
    if (allUpdates._system) {
      inferenceInterval.value = allUpdates._system.inference_interval;
    }

    // allUpdates is { camera_id: { metadata: ..., space_id: ... } }
    Object.entries(allUpdates).forEach(([cid, data]) => {
      if (cid === '_system') return;
      const cam = cameraStatus.value.find(c => c.id == cid);
      if (!cam) return;

      Object.entries(data).forEach(([sid, info]) => {
        if (sid === 'metadata') {
          cam.inferenceTime = info.inference_speed;
          cam.isProcessing = !!info.is_processing;
          if (info.timestamp) {
            cam.lastUpdated = parseTimestamp(info.timestamp) || new Date();
          }

          
          if (info.scan_id && info.scan_id !== cam.lastScanId) {
            console.log(`[WS] New scan for camera ${cam.id}: ${info.scan_id}. Refreshing if visible.`);
            cam.lastScanId = info.scan_id;
            
            if (cam.isIntersecting) {
              loadGridImage(cam);
              
              // If this camera is currently expanded, we also need to trigger a full-res reload
              if (expandedDialog.value && selectedCamera.value?.id === cam.id) {
                loadFullRes(cam);
              }
            }
          }
          return;
        }
        
        // Update space occupancy
        const sp = cam.spaces.find(s => s.id == sid);
        if (sp) { sp.occupied = info.occupied; sp.occupancyKnown = typeof info.occupied === 'boolean'; }
      });
      cam.occupiedCount = cam.spaces.filter(s => s.occupied).length;
    });
  };

  ws.onclose = (ev) => {
    // CRITICAL: Clear the failed socket from our state so we can retry
    sockets.value = [];

    // Stop retrying if the server sent a 410 Gone or if we reached max attempts
    if (ev.code === 4010 || ev.code === 4003 || reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      console.error(`[WS] Connection terminal (Code: ${ev.code}). Stopping retries.`);
      return;
    }

    reconnectAttempts++;
    const delay = 1000 + Math.random() * 2000; // 1-3s with jitter
    console.warn(`[WS] Global connection lost. Retry ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS} in ${delay}ms...`);

    // H9 audit fix: track the reconnect timer so onUnmounted can
    // clear it.  Without this, ``ws.close()`` in onUnmounted
    // fires ``onclose`` which schedules a fresh ``setTimeout`` that
    // calls ``connectGlobalWs()`` after unmount, opening a new
    // WebSocket on an unmounted component.
    globalReconnectTimer = setTimeout(() => {
      // Re-verify that we still need a connection before retrying
      if (sockets.value.length === 0) {
        connectGlobalWs();
      }
    }, delay);
  };

  sockets.value = [ws];
}

function getRelativeTime(date) {
  if (!date || isNaN(date.getTime())) return '';
  const seconds = Math.floor((now.value.getTime() - date.getTime()) / 1000);
  
  if (seconds < 2) return 'Just now';
  if (seconds < 60) return `${seconds}s ago`;
  
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

onMounted(() => {
  fetchCameras();
  nowInterval = setInterval(() => {
    now.value = new Date();
  }, 1000);

});

onUnmounted(() => {
  if (nowInterval) clearInterval(nowInterval);
  closeAllSockets();
  window.removeEventListener('resize', handleResize);
});
</script>

<style scoped>
/* Custom alert banners. We previously used Vuetify's <v-alert> here,
   but v-alert renders as display:grid with auto-sized content tracks.
   Combined with the parent v-container.fill-height (which is
   display:flex; flex-wrap:wrap because of Vuetify's utility class),
   the alert was sized to its content and ended up sitting beside the
   camera grid rather than spanning the full container width above it.

   These rules build the banner as a plain block element with explicit
   width:100% so it reliably takes the full container width and stacks
   above the v-row of camera cards. */
.community-banner {
  position: relative;
  display: block;
  /* ``flex: 0 0 100%`` keeps the banner at full container width even
     though its parent (v-container.fill-height) is a flex container.
     Without ``flex-shrink: 0`` the banner would shrink to its content
     width and end up beside the camera grid below. */
  flex: 0 0 100%;
  box-sizing: border-box;
  padding: 18px 48px 18px 24px;
  margin-bottom: 24px;
  border-radius: 6px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
  border-left: 4px solid rgb(var(--v-theme-primary));
  background: linear-gradient(
    90deg,
    rgba(var(--v-theme-primary), 0.10) 0%,
    rgba(var(--v-theme-primary), 0.02) 100%
  );
}
.community-banner--warning {
  border-left-color: rgb(var(--v-theme-warning));
  background: linear-gradient(
    90deg,
    rgba(var(--v-theme-warning), 0.10) 0%,
    rgba(var(--v-theme-warning), 0.02) 100%
  );
}
/* Info variant — used for the pool-exceeded banner after the
   cloud-side refactor that auto-bumps/decrements the pool.  The
   system handles the adjustment automatically now, so this is a
   notification rather than a problem the user must fix. */
.community-banner--info {
  border-left-color: rgb(var(--v-theme-info));
  background: linear-gradient(
    90deg,
    rgba(var(--v-theme-info), 0.10) 0%,
    rgba(var(--v-theme-info), 0.02) 100%
  );
}
.community-banner__body {
  display: block;
  width: 100%;
}
.community-banner__main {
  display: block;
  /* ``min-width: 0`` lets the text flex item shrink below its
     intrinsic width — without it the description would push the
     action panel past the banner edge on narrow displays. */
  min-width: 0;
}
.community-banner__title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.community-banner__description {
  line-height: 1.5;
  max-width: 80ch;
  margin-bottom: 0;
}
.community-banner__actions {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}

/* On md+ screens the banner becomes a two-panel layout: textual
   explanation on the left (flex-grow), action buttons on the
   right (flex-shrink-0, stacked vertically). This uses the full
   width of a wide viewport instead of leaving the right half
   empty — the previous left-aligned content looked awkward on
   large screens. Stacks vertically below md. */
@media (min-width: 960px) {
  .community-banner__body {
    display: flex;
    align-items: center;
    gap: 32px;
  }
  .community-banner__main {
    flex: 1 1 0;
    /* Cap text width so very wide screens don't stretch the
       paragraph into a single enormous line. */
    max-width: calc(100% - 220px);
  }
  .community-banner__actions {
    flex: 0 0 200px;
    flex-direction: column;
    margin-top: 0;
  }
  .community-banner__actions .v-btn {
    /* Inside the flex column, the buttons fill the 200px column. */
    width: 100%;
  }
}
.community-banner__close {
  position: absolute !important;
  top: 8px;
  right: 8px;
  z-index: 2;
}

.konva-wrapper {
  width: 100%;
  position: relative;
  /* Larger floor than the previous 200px. With one or two cameras
     the image needs to fill more of the available vertical space
     — 200px looked cramped against a tall viewport. With more
     cameras the rows wrap and users scroll, so a slightly taller
     card doesn't hurt dense grids either. */
  min-height: 360px;
  overflow: hidden;
  flex-grow: 1;
  display: flex;
  flex-direction: column;
}
.camera-card {
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  border: 1px solid transparent !important;
  will-change: transform;
  height: 100%;
}
/* ``align="stretch"`` on the v-row makes v-cols share the row's
   height. The card uses ``flex-grow: 1`` so the konva wrapper
   absorbs any extra vertical space, preventing the lonely single-
   camera card from floating at the top with a void below it. */
.camera-row :deep(.v-col) {
  display: flex;
}
.camera-card:hover {
  border-color: #3b82f6 !important;
  box-shadow: 0 8px 16px rgba(0,0,0,0.1) !important;
}
.camera-metadata-overlay {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 10;
  background: linear-gradient(rgba(0,0,0,0.6), transparent);
  pointer-events: none;
}
.backdrop-blur {
  backdrop-filter: blur(4px);
}
.cursor-pointer {
  cursor: pointer;
}
</style>
