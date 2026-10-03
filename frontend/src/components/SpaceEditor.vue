<template>
  <v-container fluid class="pa-0 fill-height d-flex flex-column overflow-hidden" style="max-width: none; width: 100%;">
    <v-row class="ma-0 no-gutters flex-grow-1 overflow-hidden" style="min-height: 0; width: 100%;">
      <v-col cols="12" md="9" class="d-flex flex-column pa-0 fill-height">
        <v-card class="flex-grow-1 d-flex flex-column rounded-0" flat style="height: 100%;">
          <v-toolbar density="compact" color="primary" class="px-2">
            <v-toolbar-title class="d-flex align-center">
              Space Editor
              <v-chip 
                v-if="hasUnsavedChanges" 
                size="x-small" 
                color="warning" 
                class="ml-2 font-weight-bold animate-pulse" 
                variant="flat"
              >
                UNSAVED CHANGES
              </v-chip>
            </v-toolbar-title>
            <div class="flex-grow-1"></div>
            <v-btn icon @click="fetchData({ forceRefresh: true })" title="Refresh">
              <v-icon>mdi-refresh</v-icon>
            </v-btn>

            <v-divider vertical class="mx-2"></v-divider>
            
            <div class="d-flex align-center bg-grey-lighten-3 rounded-lg px-1 py-1 mr-2 border">
              <!-- Elegant Interval Selector -->
              <v-menu location="bottom start">
                <template v-slot:activator="{ props }">
                  <v-btn variant="text" size="x-small" v-bind="props" class="font-weight-bold px-2" color="primary">
                    {{ Math.abs(jumpAmount) < 60 ? Math.abs(jumpAmount) + 'm' : Math.abs(jumpAmount)/60 + 'h' }}
                    <v-icon end icon="mdi-menu-down" size="x-small"></v-icon>
                  </v-btn>
                </template>
                <v-list density="compact">
                  <v-list-item v-for="opt in [
                    { t: '1m', v: -1 },
                    { t: '5m', v: -5 },
                    { t: '15m', v: -15 },
                    { t: '1h', v: -60 },
                    { t: '6h', v: -360 }
                  ]" :key="opt.v" @click="jumpAmount = opt.v" :active="jumpAmount === opt.v">
                    <v-list-item-title>{{ opt.t }}</v-list-item-title>
                  </v-list-item>
                </v-list>
              </v-menu>

              <v-btn icon="mdi-chevron-left" size="x-small" variant="text" @click="timeJump(jumpAmount)" title="Jump Back"></v-btn>
              
              <v-menu location="bottom center">
                <template v-slot:activator="{ props }">
                  <v-btn variant="plain" size="x-small" class="text-caption font-weight-black px-2" v-bind="props" style="min-width: 80px">
                    {{ currentImageLabel }}
                  </v-btn>
                </template>
                <v-list density="compact" max-height="300">
                  <v-list-item @click="currentScanIndex = -1; loadScanImage()" :active="currentScanIndex === -1">
                    <v-list-item-title class="font-weight-bold text-success">LIVE SNAPSHOT</v-list-item-title>
                  </v-list-item>
                  <v-divider></v-divider>
                  <v-list-item 
                    v-for="(scan, i) in recentScans" 
                    :key="scan.id" 
                    @click="currentScanIndex = i; loadScanImage()"
                    :active="currentScanIndex === i"
                  >
                    <v-list-item-title>{{ new Date(scan.timestamp).toLocaleString() }}</v-list-item-title>
                  </v-list-item>
                </v-list>
              </v-menu>

              <v-btn icon="mdi-chevron-right" size="x-small" variant="text" @click="timeJump(-jumpAmount)" title="Jump Forward"></v-btn>

              <v-chip 
                v-if="currentScanIndex !== -1"
                size="x-small" 
                color="secondary" 
                variant="flat" 
                class="ml-1 px-2 cursor-pointer" 
                @click="currentScanIndex = -1; loadScanImage()"
              >
                LIVE
              </v-chip>
            </div>

            <v-btn 
              variant="text" 
              @click="isDrawingGuide = !isDrawingGuide" 
              :color="isDrawingGuide ? 'secondary' : ''"
              :prepend-icon="isDrawingGuide ? 'mdi-vector-line' : 'mdi-vector-line'"
              class="mx-1"
            >
              {{ isDrawingGuide ? 'Stop Guide' : 'Draw Guide' }}
            </v-btn>
            <v-btn v-if="hasPermission('manage_cameras')" variant="text" @click="testOccupancy" :loading="testing" prepend-icon="mdi-test-tube">
              Test
            </v-btn>
            <v-btn 
              v-if="hasPermission('manage_cameras')" 
              :variant="hasUnsavedChanges ? 'elevated' : 'text'" 
              :color="hasUnsavedChanges ? 'warning' : ''" 
              @click="saveSpaces" 
              :loading="saving" 
              prepend-icon="mdi-content-save"
              class="mx-1"
            >
              Save
            </v-btn>
          </v-toolbar>
          
          <div class="flex-grow-1 bg-grey-lighten-4" style="position: relative; overflow: hidden; height: 0;">
            <div style="position: absolute; top: 10px; left: 10px; z-index: 10; pointer-events: none;">
              <v-chip size="small" color="secondary" variant="flat" class="opacity-75">
                <v-icon start size="small">mdi-mouse</v-icon>
                Click to draw • Wheel to zoom • Arrows/Middle-Click to pan • Esc to cancel
              </v-chip>
            </div>
            <div class="stage-container fill-height w-100" ref="stageWrapper">
              <v-stage 
                ref="stage" 
                :config="stageConfig"
                @mousedown="handleStageClick"
                @touchstart="handleStageClick"
                @wheel="handleWheel"
                @mousemove="handleMouseMove"
                @mouseup="handleMouseUp"
                @mouseleave="handleMouseUp"
                style="cursor: crosshair;"
              >
                <v-layer>
                  <v-image :config="imageConfig" :key="currentScanId" />
                
                <!-- Guide Lines (Dashed Magenta) -->
                <v-line 
                  v-for="(guide, i) in guides"
                  :key="`guide-line-${i}`"
                  :config="{
                    points: guide.points,
                    stroke: hoveredGuideIndex === i ? '#00FFFF' : '#FF00FF',
                    strokeWidth: (hoveredGuideIndex === i ? 3 : 1.5) / stageConfig.scaleX,
                    dash: [4 / stageConfig.scaleX, 4 / stageConfig.scaleX],
                    listening: false
                  }"
                />

                <!-- Current Guide Drawing -->
                <v-line 
                  v-if="currentGuidePoints.length > 0"
                  :config="{
                    points: currentGuidePoints,
                    stroke: '#FF00FF',
                    strokeWidth: 1.5 / stageConfig.scaleX,
                    dash: [4 / stageConfig.scaleX, 4 / stageConfig.scaleX],
                    closed: false
                  }"
                />
                <v-circle 
                  v-for="(pt, i) in getSpacePointPairs(currentGuidePoints)" 
                  :key="`guide-pt-${i}`"
                  :config="{
                    x: pt.x,
                    y: pt.y,
                    radius: 8 / stageConfig.scaleX,
                    fill: '#FF00FF'
                  }"
                />
                
                <!-- Existing Spaces -->
                <v-group 
                  v-for="(space, i) in sortedSpaces" 
                  :key="space.originalIndex"
                  :config="{ 
                    draggable: editingSpaceIndex === space.originalIndex,
                    listening: editingSpaceIndex === null || editingSpaceIndex === space.originalIndex
                  }"
                  @dragend="(e) => handleGroupDragEnd(e, space.originalIndex)"
                >
                  <v-line 
                    :config="{
                      points: space.points,
                      closed: true,
                      stroke: space.testResult !== undefined 
                        ? (space.testResult ? '#F44336' : '#4CAF50')
                        : ((hoveredSpaceIndex === space.originalIndex || editingSpaceIndex === space.originalIndex) ? '#2196F3' : 'rgba(33, 150, 243, 0.5)'),
                      strokeWidth: ((hoveredSpaceIndex === space.originalIndex || editingSpaceIndex === space.originalIndex) ? 4 : 2) / stageConfig.scaleX,
                      fill: space.testResult !== undefined
                        ? (space.testResult ? 'rgba(244, 67, 54, 0.4)' : 'rgba(76, 175, 80, 0.4)')
                        : ((hoveredSpaceIndex === space.originalIndex || editingSpaceIndex === space.originalIndex) ? 'rgba(33, 150, 243, 0.4)' : 'rgba(33, 150, 243, 0.2)'),
                      listening: editingSpaceIndex === null || editingSpaceIndex === space.originalIndex
                    }"
                  />
                  <v-group v-if="editingSpaceIndex === space.originalIndex">
                    <v-circle 
                      v-for="(pt, j) in getSpacePointPairs(space.points)"
                      :key="j"
                      :config="{ x: pt.x, y: pt.y, radius: 8 / stageConfig.scaleX, fill: 'white', stroke: 'red', strokeWidth: 2 / stageConfig.scaleX, draggable: true }"
                      @dragmove="(e) => handlePointDrag(e, space.originalIndex, j)"
                    />
                  </v-group>
                </v-group>
                
                <!-- Current Drawing -->
                <v-line 
                  v-if="currentPoints.length > 0"
                  :config="{
                    points: currentPoints,
                    stroke: 'blue',
                    strokeWidth: 2 / stageConfig.scaleX,
                    closed: false
                  }"
                />
                
                <!-- Drawing Points -->
                <v-circle 
                  v-for="(pt, i) in currentPointPairs" 
                  :key="`pt-${i}`"
                  :config="{
                    x: pt.x,
                    y: pt.y,
                    radius: 8 / stageConfig.scaleX,
                    fill: 'blue'
                  }"
                />

                <!-- Snap Indicator -->
                <v-circle 
                  v-if="snapPoint"
                  :config="{
                    x: snapPoint.x,
                    y: snapPoint.y,
                    radius: 15 / stageConfig.scaleX,
                    stroke: 'yellow',
                    strokeWidth: 1 / stageConfig.scaleX,
                    listening: false
                  }"
                />
              </v-layer>
            </v-stage>
            </div>
          </div>
        </v-card>
      </v-col>
      
      <v-col cols="12" md="3" class="d-flex flex-column pa-0 border-s" style="height: 100%; overflow: hidden;">
        <v-card class="fill-height d-flex flex-column rounded-0" flat style="height: 100%; overflow: hidden;">
          <!-- Guides Section (Fixed/Shrinkable) -->
          <v-card-item class="bg-grey-lighten-5 py-2 flex-shrink-0">
            <v-card-title class="text-subtitle-2 font-weight-bold d-flex align-center">
              Guide Lines
              <v-chip size="x-small" class="ml-2" color="secondary">{{ guides.length }}</v-chip>
              <v-spacer></v-spacer>
              <v-btn icon="mdi-plus" size="x-small" variant="text" @click="isDrawingGuide = true" :color="isDrawingGuide ? 'secondary' : ''"></v-btn>
            </v-card-title>
          </v-card-item>
          <v-divider class="flex-shrink-0"></v-divider>
          <div style="max-height: 25%; overflow-y: auto;" class="flex-shrink-0">
            <v-list density="compact" class="pa-0">
              <v-list-item 
                v-for="(guide, i) in guides" 
                :key="`guide-${i}`" 
                class="border-b"
                @mouseenter="hoveredGuideIndex = i"
                @mouseleave="hoveredGuideIndex = null"
              >
                <v-list-item-title class="text-caption">Guide {{ i + 1 }}</v-list-item-title>
                <template v-slot:append>
                  <v-btn icon="mdi-delete" size="x-small" variant="text" color="error" @click="guides.splice(i, 1)"></v-btn>
                </template>
              </v-list-item>
              <div v-if="guides.length === 0" class="pa-4 text-center text-caption text-grey">
                No guides defined
              </div>
            </v-list>
          </div>
          <v-divider class="flex-shrink-0"></v-divider>

          <!-- Parking Spaces Section (Flexible/Scrollable) -->
          <v-card-item class="bg-grey-lighten-5 py-3 flex-shrink-0">
            <v-card-title class="text-subtitle-1 font-weight-bold">
              Parking Spaces
              <v-chip size="x-small" class="ml-2" color="primary">{{ spaces.length }}</v-chip>
            </v-card-title>
          </v-card-item>
          <v-divider class="flex-shrink-0"></v-divider>
          <v-card-text class="pa-0 overflow-y-auto" style="flex: 1 1 0; min-height: 0;">
            <v-list density="compact">
              <v-list-item 
                v-for="(space, i) in spaces" 
                :key="i"
                @mouseenter="hoveredSpaceIndex = i"
                @mouseleave="hoveredSpaceIndex = null"
                :active="editingSpaceIndex === i"
                color="primary"
                class="border-b"
              >
                <template v-slot:prepend>
                  <v-avatar size="24" color="grey-lighten-3" class="mr-2">
                    <span class="text-caption">{{ i + 1 }}</span>
                  </v-avatar>
                </template>
                <v-list-item-title v-if="editingSpaceIndex !== i" class="font-weight-medium">
                  {{ space.name || `Space ${i + 1}` }}
                </v-list-item-title>
                <v-text-field
                  v-else
                  v-model="space.name"
                  density="compact"
                  variant="outlined"
                  hide-details
                  class="my-1"
                  autofocus
                  @keydown.enter="toggleEdit(i)"
                ></v-text-field>
                <template v-slot:append>
                  <div class="d-flex">
                  <v-btn icon size="x-small" variant="text" :color="editingSpaceIndex === i ? 'success' : 'grey-darken-1'" @click.stop="toggleEdit(i)">
                    <v-icon>{{ editingSpaceIndex === i ? 'mdi-check' : 'mdi-pencil' }}</v-icon>
                  </v-btn>
                  <v-btn v-if="hasPermission('manage_cameras')" icon size="x-small" variant="text" color="error" @click.stop="removeSpace(i)">
                    <v-icon>mdi-delete</v-icon>
                  </v-btn>
                  </div>
                </template>
              </v-list-item>
              
              <div v-if="spaces.length === 0" class="d-flex flex-column align-center justify-center pa-6 text-grey">
                <v-icon size="48" class="mb-2">mdi-vector-square-plus</v-icon>
                <div class="text-center">No spaces defined.<br>Click on the image to start drawing.</div>
              </div>
            </v-list>
          </v-card-text>
          <v-divider class="flex-shrink-0"></v-divider>
          <v-card-actions class="pa-3 bg-grey-lighten-5 flex-shrink-0">
             <v-btn block color="warning" variant="tonal" @click="currentPoints = []" :disabled="currentPoints.length === 0" prepend-icon="mdi-undo">
               Clear Drawing
             </v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>
    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="3000">
      {{ snackbar.text }}
      <template v-slot:actions>
        <v-btn variant="text" @click="snackbar.show = false">Close</v-btn>
      </template>
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue';
import { useRoute, onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router';
import axios from 'axios';

const route = useRoute();
// Vue Router reuses the SpaceEditor component instance when the
// user navigates between cameras (e.g. /editor/1 → /editor/5)
// because the route component is the same — only the params
// change. A ``const cameraId = route.params.cameraId`` would
// capture the value at setup time and never update. We use the
// route param as a reactive ref so a ``watch`` can react to
// cameraId changes and reload the camera-specific state (spaces
// from the server, guides from localStorage, the image, etc.).
const cameraId = ref(route.params.cameraId);
watch(
  () => route.params.cameraId,
  (newId) => {
    if (newId !== undefined && newId !== cameraId.value) {
      cameraId.value = newId;
    }
  }
);

const stage = ref(null);
const stageWrapper = ref(null);
// stageConfig.width/height are the stage's LOCAL coordinate system
// size — i.e., the image's natural pixel dimensions after a real
// image is loaded. Konva's getRelativePointerPosition() returns
// coordinates in this local system (post the inverse of the stage
// transform), so making this the image's natural pixel size is what
// makes a click at "the upper-left car" land on the same pixel as
// the backend's inference sees. The stage's DOM size on screen is
// the result of multiplying this by scaleX/scaleY and adding x/y;
// it does not have to equal width/height here.
const stageConfig = ref({ width: 800, height: 600, scaleX: 1, scaleY: 1, x: 0, y: 0 });
// imageConfig holds:
//   image         - HTMLImageElement for vue-konva to draw
//   width/height  - the image's natural pixel size (so the
//                   v-image is drawn at full size in stage
//                   coords; the stage's scaleX/scaleY transform
//                   then renders it at fit-to-wrapper size)
//   naturalWidth  - the JPEG's actual pixel width — used to
//     /naturalHeight  convert between stage-local (CSS pixel)
//                   coords and image-natural pixel coords.
//
// The stage's local coordinate system equals the wrapper's CSS
// pixel size. The image is drawn at (0, 0) to (naturalW,
// naturalH) in stage coords; the stage's scaleX/scaleY/x/y
// transform renders it centered at fit-to-wrapper size. User
// clicks in the v-stage return stage-local (CSS pixel) coords
// from getRelativePointerPosition(); we convert to image-natural
// at the API boundary (testOccupancy, saveSpaces) and back at
// read time (fetchData, updateCameraPoints).
const imageConfig = ref({ image: null, width: 800, height: 600, naturalWidth: 800, naturalHeight: 600 });
const spaces = ref([]);
const guides = ref([]);
const recentScans = ref([]);
const currentScanIndex = ref(-1);
const currentScanId = ref('live');
const jumpAmount = ref(-1); // Default to -1 minute for arrows
const currentPoints = ref([]);
const currentGuidePoints = ref([]);
const isDrawingGuide = ref(false);
const saving = ref(false);
const testing = ref(false);
const editingSpaceIndex = ref(null);
const hoveredSpaceIndex = ref(null);
const hoveredGuideIndex = ref(null);
const snapPoint = ref(null);
const isPanning = ref(false);
const lastPanPos = ref({ x: 0, y: 0 });
const snackbar = ref({ show: false, text: '', color: 'success' });

const sortedSpaces = computed(() => {
  return spaces.value.map((s, index) => ({
    ...s,
    originalIndex: index
  })).sort((a, b) => {
    // Bring the currently editing space to the very front (end of array)
    if (editingSpaceIndex.value === a.originalIndex) return 1;
    if (editingSpaceIndex.value === b.originalIndex) return -1;
    return 0;
  });
});

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

const currentPointPairs = computed(() => {
  const pairs = [];
  for (let i = 0; i < currentPoints.value.length; i += 2) {
    pairs.push({ x: currentPoints.value[i], y: currentPoints.value[i + 1] });
  }
  return pairs;
});

const getSpacePointPairs = (points) => {
  const pairs = [];
  if (!points) return pairs;
  for (let i = 0; i < points.length; i += 2) {
    pairs.push({ x: points[i], y: points[i + 1] });
  }
  return pairs;
};

const handlePointDrag = (e, spaceIndex, pointIndex) => {
  const pos = e.target.position();
  const newPoints = [...spaces.value[spaceIndex].points];
  newPoints[pointIndex * 2] = pos.x;
  newPoints[pointIndex * 2 + 1] = pos.y;
  spaces.value[spaceIndex].points = newPoints;
  spaces.value[spaceIndex].testResult = undefined; // Clear result on move
};

const handleGroupDragEnd = (e, index) => {
  const group = e.currentTarget;
  const x = group.x();
  const y = group.y();
  const space = spaces.value[index];
  const newPoints = [];
  for (let i = 0; i < space.points.length; i += 2) {
    newPoints.push(space.points[i] + x);
    newPoints.push(space.points[i+1] + y);
  }
  space.points = newPoints;
  space.testResult = undefined; // Clear result on move
  group.position({ x: 0, y: 0 });
};

const toggleEdit = (index) => {
  if (editingSpaceIndex.value === index) {
    editingSpaceIndex.value = null;
  } else {
    editingSpaceIndex.value = index;
  }
};

const handleWheel = (e) => {
  e.evt.preventDefault();
  const scaleBy = 1.1;
  const stage = e.target.getStage();
  const oldScale = stage.scaleX();
  const pointer = stage.getPointerPosition();

  const mousePointTo = {
    x: (pointer.x - stage.x()) / oldScale,
    y: (pointer.y - stage.y()) / oldScale,
  };

  const newScale = e.evt.deltaY > 0 ? oldScale / scaleBy : oldScale * scaleBy;

  stageConfig.value = {
    ...stageConfig.value,
    width: stage.width(), // Stage's local coordinate system is the
    height: stage.height(), // wrapper's CSS pixel size. We pin the
                              // config's width/height to the current
                              // stage size so a wheel zoom doesn't
                              // accidentally change the local coord
                              // system mid-zoom.
    scaleX: newScale,
    scaleY: newScale,
    x: pointer.x - mousePointTo.x * newScale,
    y: pointer.y - mousePointTo.y * newScale,
  };
};

const handleMouseMove = (e) => {
  const stage = e.target.getStage();

  if (isPanning.value) {
    const pos = stage.getPointerPosition();
    const dx = pos.x - lastPanPos.value.x;
    const dy = pos.y - lastPanPos.value.y;
    
    stageConfig.value.x += dx;
    stageConfig.value.y += dy;
    lastPanPos.value = { x: pos.x, y: pos.y };
    return;
  }

  const pos = stage.getRelativePointerPosition();
  
  if (!pos) return;

  if (editingSpaceIndex.value !== null) {
    snapPoint.value = null;
    return;
  }

  const threshold = 15 / stage.scaleX(); // Adjust threshold based on zoom
  let closest = null;
  let minDist = Infinity;

  // 1. Snapping to Points (HIGHER PRIORITY)
  // Check existing spaces
  for (const space of spaces.value) {
    for (let i = 0; i < space.points.length; i += 2) {
      const px = space.points[i];
      const py = space.points[i+1];
      const dist = Math.hypot(px - pos.x, py - pos.y);
      if (dist < threshold && dist < minDist) {
        minDist = dist;
        closest = { x: px, y: py };
      }
    }
  }

  // Check current drawing
  for (let i = 0; i < currentPoints.value.length; i += 2) {
      const px = currentPoints.value[i];
      const py = currentPoints.value[i+1];
      const dist = Math.hypot(px - pos.x, py - pos.y);
      if (dist < threshold && dist < minDist) {
        minDist = dist;
        closest = { x: px, y: py };
      }
  }

  // Check current guide drawing
  for (let i = 0; i < currentGuidePoints.value.length; i += 2) {
      const px = currentGuidePoints.value[i];
      const py = currentGuidePoints.value[i+1];
      const dist = Math.hypot(px - pos.x, py - pos.y);
      if (dist < threshold && dist < minDist) {
        minDist = dist;
        closest = { x: px, y: py };
      }
  }

  // 2. Snapping to Guide Lines (LOWER PRIORITY - only if no point found)
  if (!closest) {
    for (const guide of guides.value) {
      const pts = guide.points;
      for (let i = 0; i < pts.length - 2; i += 2) {
        const x1 = pts[i], y1 = pts[i+1];
        const x2 = pts[i+2], y2 = pts[i+3];
        
        // Distance from point to line segment
        const A = pos.x - x1;
        const B = pos.y - y1;
        const C = x2 - x1;
        const D = y2 - y1;
        const dot = A * C + B * D;
        const len_sq = C * C + D * D;
        let param = -1;
        if (len_sq !== 0) param = dot / len_sq;

        let xx, yy;
        if (param < 0) { xx = x1; yy = y1; }
        else if (param > 1) { xx = x2; yy = y2; }
        else { xx = x1 + param * C; yy = y1 + param * D; }

        const dist = Math.hypot(pos.x - xx, pos.y - yy);
        if (dist < threshold && dist < minDist) {
          minDist = dist;
          closest = { x: xx, y: yy };
        }
      }
    }
  }
  
  snapPoint.value = closest;
};

const canJumpPrev = computed(() => currentScanIndex.value < recentScans.value.length - 1);
const canJumpNext = computed(() => currentScanIndex.value > 0 || currentScanIndex.value === -1);

const currentImageLabel = computed(() => {
  if (currentScanIndex.value === -1) return 'LIVE';
  const scan = recentScans.value[currentScanIndex.value];
  if (!scan) return '---';
  return new Date(scan.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
});

async function jumpImage(delta) {
  // delta -1 = older (inc index), delta 1 = newer (dec index)
  if (currentScanIndex.value === -1) {
    if (delta === -1 && recentScans.value.length > 0) {
      currentScanIndex.value = 0;
    }
  } else {
    // We are in history
    const nextIdx = currentScanIndex.value - delta; // -1 becomes +1 (older), +1 becomes -1 (newer)
    if (nextIdx < 0) {
      currentScanIndex.value = -1; // back to LIVE
    } else if (nextIdx < recentScans.value.length) {
      currentScanIndex.value = nextIdx;
    }
  }
  await loadScanImage();
}

async function timeJump(minutes) {
  let baseTime;
  let currentId = null;
  
  console.log(`[JUMP] START - currentScanIndex: ${currentScanIndex.value}`);

  if (currentScanIndex.value !== -1) {
    const currentScan = recentScans.value[currentScanIndex.value];
    baseTime = new Date(currentScan.timestamp);
    currentId = currentScan.id;
  } else {
    // If at LIVE, use the timestamp of the first (newest) scan in our list if available
    if (recentScans.value.length > 0) {
      baseTime = new Date(recentScans.value[0].timestamp);
    } else {
      baseTime = new Date();
    }
  }
  
  const targetTime = new Date(baseTime.getTime() + minutes * 60000);
  
  try {
    const res = await axios.get(`/api/cameras/${cameraId.value}/scan-at-time`, {
      params: { 
        target_time: targetTime.toISOString(),
        direction: minutes < 0 ? 'older' : 'newer',
        exclude_id: currentId
      }
    });
    
    // Check if this scan is already in our list
    const existingIdx = recentScans.value.findIndex(s => s.id === res.data.id);
    if (existingIdx !== -1) {
      currentScanIndex.value = existingIdx;
    } else {
      recentScans.value.push(res.data);
      recentScans.value.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
      currentScanIndex.value = recentScans.value.findIndex(s => s.id === res.data.id);
    }
    
    await loadScanImage();
  } catch (e) {
    console.error("Error jumping time", e);
    snackbar.value = { show: true, text: 'No other scan found for that time period', color: 'warning' };
  }
}

const updateCameraPoints = (cam, img) => {
  // Called on ResizeObserver (the wrapper changed size) — the
  // stage's scaleX/scaleY/x/y has been re-fit to the new
  // wrapper, so any stage-local (CSS pixel) points need to be
  // re-projected. We re-convert from the canonical image-natural
  // pixel coords (``rawPoints``) to stage-local (CSS) so the
  // v-line/v-group points stay aligned with the visible image.
  const nativeWidth = img.naturalWidth || img.width;
  const nativeHeight = img.naturalHeight || img.height;
  if (!nativeWidth || !cam.spaces.length) return;

  cam.spaces.forEach(s => {
    if (!s.rawPoints) return;
    const isNormalized = s.rawPoints.every(p => p >= -2.0 && p <= 2.0);
    const newPts = [];
    for (let i = 0; i < s.rawPoints.length; i += 2) {
      const px = isNormalized ? s.rawPoints[i] * nativeWidth : s.rawPoints[i];
      const py = isNormalized ? s.rawPoints[i + 1] * nativeHeight : s.rawPoints[i + 1];
      const stage = imageNaturalToStageLocal(px, py);
      newPts.push(stage.x, stage.y);
    }
    s.points = newPts;
  });
};

async function loadScanImage(opts = {}) {
    const { forceRefresh = false } = opts;
    // Clear all per-space test results. A Test result was inferred
    // against the PREVIOUS image; when the user switches to a
    // different scan (or a fresh LIVE frame after Refresh), the
    // previous red/green colors no longer reflect anything and
    // would be misleading. Reset to undefined (uncolored / blue
    // edit-mode) so the user sees the new image with the spaces in
    // a clean state.
    if (spaces.value.length > 0) {
      spaces.value.forEach((s) => { s.testResult = undefined; });
    }
    const img = new Image();
    let url = `/api/cameras/${cameraId.value}/snapshot`;
    const params = [];
    if (currentScanIndex.value !== -1) {
      const scan = recentScans.value[currentScanIndex.value];
      params.push(`v=${scan.id}`);
      currentScanId.value = scan.id;
    } else {
      currentScanId.value = 'live';
      // On a LIVE load, ask the backend to bypass its frame cache
      // when the user explicitly hit Refresh. Without this, the
      // cache makes Test deterministic but also means Refresh
      // wouldn't update the displayed image. The Trade-off:
      // explicit refresh = new image, then tests on that image are
      // deterministic for the next 30s.
      if (forceRefresh) {
        params.push('force=true');
      }
    }
    if (params.length) {
      url += `?${params.join('&')}`;
    }
    // Append a unique query param for the browser cache too. The
    // backend ignores this; it just makes the browser issue a
    // network request rather than serve the cached image bytes.
    if (forceRefresh) {
      url += (params.length ? '&' : '?') + `_t=${Date.now()}`;
    }

  await new Promise((resolve, reject) => {
    img.crossOrigin = "Anonymous";
    img.onload = () => {
      // The v-image is drawn at imageConfig.width × imageConfig.height
      // (= the JPEG's natural pixel size) in stage space. The stage
      // itself is sized to the wrapper's CSS size; the image is
      // rendered to fill the wrapper via the stage's scaleX/scaleY/x/y
      // transform (set by updateStageSizeForImage). User clicks in
      // the v-stage return stage-local (CSS pixel) coords from
      // getRelativePointerPosition(); we convert to/from the image's
      // natural pixel space at the API boundary
      // (testOccupancy, saveSpaces, fetchData).
      imageConfig.value.image = img;
      imageConfig.value.width = img.naturalWidth;
      imageConfig.value.height = img.naturalHeight;
      imageConfig.value.naturalWidth = img.naturalWidth;
      imageConfig.value.naturalHeight = img.naturalHeight;

      // Force the stage transform to re-fit the (possibly different)
      // new image into the wrapper. ``onlyIfNone=true`` is the right
      // choice for the *initial* load but the wrong choice when the
      // user picks a different scan from the menu — otherwise the
      // previous scan's transform (computed for the previous
      // image's naturalWidth) is reused, leaving the new image
      // off-center or at the wrong scale.
      const isInitialLoad = currentScanIndex.value === -1 && !stageConfig.value._initialized;
      updateStageSizeForImage(img, isInitialLoad);
      if (isInitialLoad) stageConfig.value._initialized = true;

      // Give Vue time to update the v-image key
      nextTick(() => {
        setTimeout(() => {
          updateCameraPoints({ spaces: spaces.value }, img);
          resolve();
        }, 50); // Tiny delay to ensure browser layout is stable
      });
    };
    img.onerror = (err) => {
      console.error("[LOAD] Image failed:", url, err);
      reject(err);
    };
    img.src = url;
  });
}

function updateStageSizeForImage(img, onlyIfNone = false) {
  const wrapper = stageWrapper.value;
  const nativeWidth = img.naturalWidth || img.width;
  const nativeHeight = img.naturalHeight || img.height;

  // If onlyIfNone is true, we only set the initial scale if stage hasn't been moved/scaled yet
  if (onlyIfNone && (stageConfig.value.scaleX !== 1 || stageConfig.value.x !== 0)) {
    return;
  }

  if (wrapper && nativeWidth > 0) {
    const availWidth = wrapper.clientWidth;
    const availHeight = wrapper.clientHeight;

    const fitScaleX = (availWidth - 40) / nativeWidth;
    const fitScaleY = (availHeight - 40) / nativeHeight;
    const scale = Math.min(fitScaleX, fitScaleY, 1.0);

    // The stage's local coordinate system is the LARGER of the
    // wrapper's CSS size and the image's natural size. The
    // Konva container's DOM size = stage.width × stage.height
    // (CSS pixels), so for typical cameras (image > wrapper)
    // the container is the image's natural size and the entire
    // image is in the buffer. The container is clipped by the
    // wrapper's `overflow: hidden` to the visible area, and the
    // stage's scaleX/scaleY/x/y transform renders the image
    // centered and fit-to-wrapper.
    //
    // Why not just the wrapper size? Because saved annotations
    // (drawn at stage coords in the image's pixel range, e.g.
    // (1500, 800) for a 1920×1080 image) would be clipped by
    // the stage's buffer if the stage is smaller than the
    // image. That made the v-line for an annotation at image
    // pixel (1800, 900) disappear when the wrapper is 1500
    // wide (the stage would be 1500×880, clipping everything
    // past x=1500 in stage coords).
    //
    // The image is drawn at (0, 0) to (nativeW, nativeH) in
    // stage coords. With stage local = max(nativeW, wrapperW) ×
    // max(nativeH, wrapperH), the entire image is in the
    // stage's buffer. A v-line at image pixel (X, Y) is drawn
    // at stage (X, Y) and rendered at DOM (X*scaleX + x,
    // Y*scaleY + y) — the right DOM position for that image
    // pixel.
    const stageW = Math.max(nativeWidth, availWidth);
    const stageH = Math.max(nativeHeight, availHeight);

    stageConfig.value = {
      ...stageConfig.value,
      width: stageW,
      height: stageH,
      scaleX: scale,
      scaleY: scale,
      x: (availWidth - nativeWidth * scale) / 2,
      y: (availHeight - nativeHeight * scale) / 2
    };
  }
}

/**
 * Convert a point from the v-stage's local coordinate system
 * (which equals the wrapper's CSS pixel size when the wrapper
 * is larger than the image, or the image's natural pixel size
 * when the image is larger) to the image's natural pixel
 * coordinate system. The image's natural pixel coords are what
 * the backend's inference frame uses; the C++ engine receives
 * ROIs in [0, 1] of the natural pixel space.
 *
 * The image is drawn at (0, 0) to (naturalW, naturalH) in stage
 * coords (imageConfig.width = naturalW). A click at DOM (X, Y)
 * returns stage coord (X, Y) from getRelativePointerPosition
 * after applying the inverse of the stage transform — but in
 * fact, getRelativePointerPosition returns the click in stage
 * coords WITHOUT the transform applied (it returns
 * (X - tx) / scaleX, which equals image pixel X for X in
 * [0, naturalW]). So a stage coord (X, Y) for X in [0, naturalW]
 * IS the image pixel (X, Y). The conversion is the identity.
 *
 * Returns null if the stage transform isn't initialized yet.
 */
function stageLocalToImageNatural(stageLocalX, stageLocalY) {
  return { x: stageLocalX, y: stageLocalY };
}

/**
 * Inverse of stageLocalToImageNatural — convert from image-natural
 * pixel coords to stage-local coords for rendering. Same identity
 * argument as above: image pixel (X, Y) is already stage coord
 * (X, Y) because the v-image is drawn at its natural size in
 * stage coords.
 */
function imageNaturalToStageLocal(imageX, imageY) {
  return { x: imageX, y: imageY };
}

const fetchData = async (opts = {}) => {
  try {
    // 1. Get recent scans
    const scansRes = await axios.get(`/api/cameras/${cameraId.value}/recent-scans`);
    recentScans.value = scansRes.data;

    // 2. Load latest (LIVE) Image. ``forceRefresh`` (the Refresh
    //    button) busts the backend's LIVE frame cache so the user
    //    actually sees a new image; the auto-load on mount and the
    //    recent-scans refreshes don't force, so the cache stays
    //    warm and Test results remain deterministic.
    await loadScanImage(opts);
    
    // 3. Get Spaces
    const spacesRes = await axios.get(`/api/spaces`, { params: { camera_id: cameraId.value } });
    spaces.value = spacesRes.data.map((s, i) => {
      let pts = typeof s.points === 'string' ? JSON.parse(s.points) : s.points;

      // Saved points are in *image-natural* pixel coords (the
      // coord system the backend's inference frame uses).
      // Convert to stage-local (CSS pixel) coords so the v-line
      // /v-group points overlay the visible image at the right
      // position. Two cases:
      //
      //   1. points is normalized [0, 1]: de-normalize to
      //      image-natural first, then to stage-local.
      //   2. points is already image-natural pixel: convert
      //      directly to stage-local.
      const isNormalized = pts.every(p => p >= -2.0 && p <= 2.0);
      const w = imageConfig.value.naturalWidth;
      const h = imageConfig.value.naturalHeight;
      const newPts = [];
      for (let j = 0; j < pts.length; j += 2) {
        const px = isNormalized ? pts[j] * w : pts[j];
        const py = isNormalized ? pts[j + 1] * h : pts[j + 1];
        const stage = imageNaturalToStageLocal(px, py);
        newPts.push(stage.x, stage.y);
      }
      pts = newPts;

      return {
        ...s,
        points: pts,
        rawPoints: typeof s.points === 'string' ? JSON.parse(s.points) : s.points,  // keep image-natural for API calls
        name: s.name || `Space ${i + 1}`
      };
    });

    originalSpacesState.value = getSpacesState(spaces.value);

    loadGuidesFromLocal();

    // 4. Setup Resize Observer (only once)
    if (stageWrapper.value && !resizeObserver) {
      resizeObserver = new ResizeObserver(() => {
        if (imageConfig.value.image) {
          updateStageSizeForImage(imageConfig.value.image, true);
          // Recalculate points too as scale likely changed
          updateCameraPoints({ spaces: spaces.value }, imageConfig.value.image);
        }
      });
      resizeObserver.observe(stageWrapper.value);
    }
  } catch (e) {
    console.error("Error fetching data", e);
  }
};

const handleStageClick = (e) => {
  // Middle mouse button (button 1) for panning
  if (e.evt && e.evt.button === 1) {
    isPanning.value = true;
    const stage = e.target.getStage();
    const pos = stage.getPointerPosition();
    lastPanPos.value = { x: pos.x, y: pos.y };
    return;
  }

  if (e.target.attrs.draggable) return;
  if (editingSpaceIndex.value !== null) return;

  const stage = e.target.getStage();
  // Use snap point if available, otherwise use relative pointer position
  const pos = snapPoint.value || stage.getRelativePointerPosition();
  
  if (!pos) return;
  
  if (isDrawingGuide.value) {
    currentGuidePoints.value.push(pos.x, pos.y);
    // Guides are simple line segments (2 points)
    if (currentGuidePoints.value.length >= 4) {
      guides.value.push({
        points: [...currentGuidePoints.value]
      });
      currentGuidePoints.value = [];
      // Stay in drawing mode but reset current line
    }
  } else {
    currentPoints.value.push(pos.x, pos.y);

    // If 4 points (8 coords), close polygon. The drawn points
    // are in stage-local (CSS pixel) space; we also compute and
    // store ``rawPoints`` (image-natural pixel space) so the
    // ResizeObserver and other paths that re-derive points have
    // the canonical image-natural coords to work from.
    if (currentPoints.value.length >= 8) {
      const stagePoints = [...currentPoints.value];
      const w = imageConfig.value.naturalWidth || 1;
      const h = imageConfig.value.naturalHeight || 1;
      const rawPoints = [];
      for (let i = 0; i < stagePoints.length; i += 2) {
        const imageNat = stageLocalToImageNatural(stagePoints[i], stagePoints[i + 1]);
        rawPoints.push(imageNat.x, imageNat.y);
      }
      spaces.value.push({
        points: stagePoints,
        rawPoints,
        camera_id: cameraId.value,
        name: `Space ${spaces.value.length + 1}`
      });
      currentPoints.value = [];
      snapPoint.value = null; // Clear snap after finishing
    }
  }
};

const handleMouseUp = () => {
  isPanning.value = false;
};

const removeSpace = (index) => {
  spaces.value.splice(index, 1);
  if (editingSpaceIndex.value === index) {
    editingSpaceIndex.value = null;
  }
};

const saveSpaces = async () => {
  saving.value = true;
  try {
    // Persist points in image-natural pixel space — that's the
    // coord system the backend's inference frame uses, and what
    // we roundtrip back through `fetchData`/`updateCameraPoints`.
    // The `spaces.value[].points` is in stage-local (CSS pixel)
    // space (the space the user clicks in), so we convert back to
    // image-natural via stageLocalToImageNatural before sending.
    const w = imageConfig.value.naturalWidth;
    const h = imageConfig.value.naturalHeight;

    const normalizedSpaces = spaces.value.map(s => {
      const newPts = [];
      for (let i = 0; i < s.points.length; i += 2) {
        const imageNat = stageLocalToImageNatural(s.points[i], s.points[i + 1]);
        // Backend stores normalized [0, 1] so the JSON stays small
        // and the data shape matches what `apply_transforms` in
        // the validation pipeline produces.
        newPts.push(
          imageNat.x / w,
          imageNat.y / h,
        );
      }
      return { ...s, points: newPts };
    });

    // Assuming backend supports bulk update via PUT
    await axios.put(`/api/spaces`, normalizedSpaces);
    originalSpacesState.value = getSpacesState(spaces.value);
    snackbar.value = { show: true, text: 'Spaces saved successfully (Normalized)', color: 'success' };
  } catch (e) {
    console.error("Error saving spaces", e);
    snackbar.value = { show: true, text: 'Error saving spaces', color: 'error' };
  } finally {
    saving.value = false;
  }
};

const testOccupancy = async () => {
  if (spaces.value.length === 0) {
    snackbar.value = { show: true, text: 'No spaces to test', color: 'warning' };
    return;
  }
  testing.value = true;
  snackbar.value = { show: true, text: 'Analyzing occupancy (Running AI Model)...', color: 'info' };
  try {
    // Send points in [0, 1] of the image's natural pixel size
    // (what the backend's inference frame uses). The
    // `spaces.value[].points` is in stage-local (CSS pixel)
    // space, so we convert back to image-natural first, then
    // normalize.
    const w = imageConfig.value.naturalWidth;
    const h = imageConfig.value.naturalHeight;
    const normalizedSpaces = spaces.value.map(s => {
      const newPts = [];
      for (let i = 0; i < s.points.length; i += 2) {
        const imageNat = stageLocalToImageNatural(s.points[i], s.points[i + 1]);
        newPts.push(imageNat.x / w, imageNat.y / h);
      }
      return { ...s, points: newPts };
    });

    const res = await axios.post(`/api/cameras/${cameraId.value}/test-occupancy`, normalizedSpaces, {
      params: { v: currentScanId.value === 'live' ? null : currentScanId.value }
    });
    const results = res.data; // [true, false, ...]
    spaces.value.forEach((s, i) => {
      s.testResult = results[i];
    });
    snackbar.value = { show: true, text: 'Test complete', color: 'success' };
  } catch (e) {
    console.error("Error testing occupancy", e);
    snackbar.value = { show: true, text: 'Error testing occupancy', color: 'error' };
  } finally {
    testing.value = false;
  }
};

const handleKeyDown = (e) => {
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', '+', '-', '=', 'Escape'].includes(e.key)) {
    // Only prevent default if we're not in an input field
    if (e.target.tagName === 'INPUT') return;
    e.preventDefault();
  }

  const panStep = 20 / stageConfig.value.scaleX;
  const scaleBy = 1.1;

  switch (e.key) {
    case 'ArrowUp':
      stageConfig.value.y += panStep;
      break;
    case 'ArrowDown':
      stageConfig.value.y -= panStep;
      break;
    case 'ArrowLeft':
      stageConfig.value.x += panStep;
      break;
    case 'ArrowRight':
      stageConfig.value.x -= panStep;
      break;
    case '+':
    case '=':
      zoomStage(scaleBy);
      break;
    case '-':
    case '_':
      zoomStage(1 / scaleBy);
      break;
    case 'Escape':
      currentPoints.value = [];
      currentGuidePoints.value = [];
      isDrawingGuide.value = false;
      snapPoint.value = null;
      editingSpaceIndex.value = null;
      break;
  }
};

const saveGuidesToLocal = () => {
  // cameraId is a ref (so it can react to route changes — see the
  // cameraId declaration above). Use .value to get the string id.
  if (!cameraId.value) return;
  localStorage.setItem(`guides_${cameraId.value}`, JSON.stringify(guides.value));
};

const loadGuidesFromLocal = () => {
  if (!cameraId.value) return;
  const saved = localStorage.getItem(`guides_${cameraId.value}`);
  if (saved) {
    try {
      guides.value = JSON.parse(saved);
    } catch (e) {
      console.error("Error loading guides from local storage", e);
    }
  }
};

watch(guides, saveGuidesToLocal, { deep: true });

const zoomStage = (scaleBy) => {
  const oldScale = stageConfig.value.scaleX;
  const newScale = oldScale * scaleBy;

  // Zoom about the visible center of the wrapper. The stage's
  // local coordinate system is the wrapper's CSS pixel size
  // (so the Konva container fits the wrapper), and the visible
  // center on screen is at (wrapperW/2, wrapperH/2) in DOM
  // coords which equals (wrapperW/2, wrapperH/2) in stage-local
  // coords. We keep the stage-local point at the visible center
  // after the zoom by adjusting the stage's x/y offset.
  const wrapper = stageWrapper.value;
  const centerX = (wrapper ? wrapper.clientWidth : stageConfig.value.width) / 2;
  const centerY = (wrapper ? wrapper.clientHeight : stageConfig.value.height) / 2;

  const mousePointTo = {
    x: (centerX - stageConfig.value.x) / oldScale,
    y: (centerY - stageConfig.value.y) / oldScale,
  };

  stageConfig.value = {
    ...stageConfig.value,
    scaleX: newScale,
    scaleY: newScale,
    x: centerX - mousePointTo.x * newScale,
    y: centerY - mousePointTo.y * newScale,
  };
};

const originalSpacesState = ref('[]');

const getSpacesState = (spacesList) => {
  if (!spacesList) return '[]';
  return JSON.stringify(
    spacesList.map(s => ({
      name: s.name,
      points: s.points ? [...s.points] : [],
      camera_id: s.camera_id
    }))
  );
};

const hasUnsavedChanges = computed(() => {
  return getSpacesState(spaces.value) !== originalSpacesState.value;
});

onBeforeRouteLeave((to, from, next) => {
  if (hasUnsavedChanges.value) {
    const answer = window.confirm('You have unsaved changes. Are you sure you want to leave?');
    if (!answer) {
      next(false);
      return;
    }
  }
  next();
});

onBeforeRouteUpdate((to, from, next) => {
  if (hasUnsavedChanges.value) {
    const answer = window.confirm('You have unsaved changes. Are you sure you want to leave?');
    if (!answer) {
      next(false);
      return;
    }
  }
  next();
});

const handleBeforeUnload = (e) => {
  if (hasUnsavedChanges.value) {
    e.preventDefault();
    e.returnValue = '';
  }
};

let resizeObserver = null;

onMounted(() => {
  fetchData();
  window.addEventListener('keydown', handleKeyDown);
  window.addEventListener('beforeunload', handleBeforeUnload);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown);
  window.removeEventListener('beforeunload', handleBeforeUnload);
  if (resizeObserver) resizeObserver.disconnect();
});

// React to cameraId changes (the user navigated from one camera's
// editor to another's). Without this, the component is reused
// (Vue Router reuses the same component instance for the same
// route component), and ``cameraId`` is the only thing that
// changes — so we have to manually re-fetch the new camera's
// spaces from the server, reload guides from localStorage, clear
// in-progress drawing state, and reset the stage transform. All
// other state (imageConfig, spaces, guides, currentPoints,
// currentGuidePoints, recentScans, currentScanId, currentScanIndex)
// needs to be cleared so the new camera's data isn't blended
// with the old camera's.
watch(cameraId, (newId, oldId) => {
  if (newId === oldId || newId === undefined) return;
  // Clear all per-camera state. Don't await — the user sees a
  // brief flash of empty state, then the new data loads.
  spaces.value = [];
  originalSpacesState.value = '[]';
  guides.value = [];
  currentPoints.value = [];
  currentGuidePoints.value = [];
  recentScans.value = [];
  currentScanIndex.value = -1;
  currentScanId.value = 'live';
  editingSpaceIndex.value = null;
  hoveredSpaceIndex.value = null;
  hoveredGuideIndex.value = null;
  snapPoint.value = null;
  // Reset the stage transform — the new image may have a
  // different natural size, and the previous transform was fit
  // to the previous image.
  stageConfig.value = {
    width: 800,
    height: 600,
    scaleX: 1,
    scaleY: 1,
    x: 0,
    y: 0,
  };
  stageConfig.value._initialized = false;
  // Refetch for the new camera.
  fetchData();
});
</script>

<style>
/* Lock the entire page to prevent scrolling while in the editor */
html, body {
  overflow: hidden !important;
  height: 100vh !important;
}

.stage-container {
  overflow: hidden;
  position: relative;
  background-color: #000;
  min-height: 0;
}

.hover-card {
  transition: all 0.2s ease-in-out;
}

.animate-pulse {
  animation: pulse 2s infinite ease-in-out;
}

@keyframes pulse {
  0% { opacity: 0.8; }
  50% { opacity: 1; transform: scale(1.02); }
  100% { opacity: 0.8; }
}
</style>
