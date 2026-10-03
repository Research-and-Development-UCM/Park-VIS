<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <v-card elevation="2" class="mb-4">
          <v-toolbar color="primary" density="comfortable">
            <v-toolbar-title class="text-h6 text-white">Occupancy Analytics</v-toolbar-title>
            <v-spacer></v-spacer>
            <v-btn variant="tonal" class="bg-white mr-2" @click="setRange('today')">Today</v-btn>
            <v-btn variant="tonal" class="bg-white mr-2" @click="setRange('week')">Last 7 Days</v-btn>
          </v-toolbar>
          
          <v-card-text>
            <v-row class="align-center">
              <v-col v-if="groups.length > 0" cols="12" md="3">
                <v-select
                  v-model="selectedGroupId"
                  :items="groupOptions"
                  item-title="name"
                  item-value="id"
                  label="Filter by Parking Lot"
                  variant="outlined"
                  density="compact"
                  hide-details
                  prepend-inner-icon="mdi-parking"
                  @update:model-value="onGroupChange"
                ></v-select>
              </v-col>
              <v-col cols="12" md="3">
                <v-select
                  v-model="selectedCameras"
                  :items="cameraOptions"
                  :key="cameras.length"
                  :label="`Cameras (${cameraOptions.length})`"
                  :menu-props="{ maxHeight: 600 }"
                  multiple
                  chips
                  variant="outlined"
                  density="compact"
                  hide-details
                  placeholder="All Cameras"
                >
                  <template v-slot:selection="{ item, index }">
                    <v-chip v-if="index === 0" size="small">
                      <span>{{ item.title }}</span>
                    </v-chip>
                    <span v-if="index === 1" class="text-grey text-caption align-self-center">
                      (+{{ selectedCameras.length - 1 }} others)
                    </span>
                  </template>
                </v-select>
              </v-col>
              <v-col cols="12" md="2">
                <v-text-field
                  v-model="startDate"
                  label="Start Date"
                  type="date"
                  variant="outlined"
                  hide-details
                  prepend-inner-icon="mdi-calendar"
                ></v-text-field>
              </v-col>
              <v-col cols="12" md="2">
                <v-text-field
                  v-model="endDate"
                  label="End Date"
                  type="date"
                  variant="outlined"
                  hide-details
                  prepend-inner-icon="mdi-calendar"
                ></v-text-field>
              </v-col>
              <v-col cols="12" md="3">
                <v-select
                  v-model="selectedMetric"
                  :items="metricOptions"
                  label="Metric"
                  variant="outlined"
                  density="compact"
                  hide-details
                  @update:model-value="processDataAndRender"
                ></v-select>
              </v-col>
              <v-col cols="12" md="2">
                <v-btn color="primary" block @click="fetchStats" :loading="loading" prepend-icon="mdi-magnify">
                  Fetch
                </v-btn>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12">
        <v-card elevation="2">
          <v-card-title class="d-flex align-center">
            <v-icon color="primary" class="mr-2">mdi-chart-line</v-icon>
            {{ currentMetricTitle }} Over Time
            <v-chip v-if="selectedCameras.length > 0" class="ml-4" size="x-small">
              {{ selectedCameras.length }} Camera(s) Selected
            </v-chip>
            <v-chip v-else class="ml-4" size="x-small">
              All Cameras
            </v-chip>
          </v-card-title>
          <v-divider></v-divider>
          <v-card-text>
            <div style="height: 450px; position: relative;">
              <canvas id="historyChart"></canvas>
            </div>
            
            <div v-if="!hasData && !loading" class="d-flex flex-column align-center justify-center pa-10 text-grey">
                <v-icon size="64">mdi-chart-line-variant</v-icon>
                <div class="text-h6 mt-2">No data for selected criteria</div>
                <div>Try changing your camera selection or date range.</div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import axios from 'axios'
import { Chart, registerables } from 'chart.js'

Chart.register(...registerables)

const route = useRoute()

const cameras = ref([])
const selectedCameras = ref([])
const groups = ref([])
const selectedGroupId = ref(null)

const groupOptions = computed(() => {
  return [
    { id: 'all', name: 'All Parking Lots / Cameras' },
    ...groups.value.map(g => ({ id: g.id, name: g.name }))
  ]
})

const onGroupChange = (groupId) => {
  if (!groupId || groupId === 'all') {
    selectedCameras.value = []
    return
  }
  const group = groups.value.find(g => g.id === groupId)
  if (group) {
    selectedCameras.value = [...(group.camera_ids || [])]
  }
}

watch(selectedCameras, (newVal) => {
  if (selectedGroupId.value && selectedGroupId.value !== 'all') {
    const group = groups.value.find(g => g.id === selectedGroupId.value)
    if (group) {
      const groupCamIds = [...(group.camera_ids || [])].sort()
      const currentCamIds = [...newVal].sort()
      const match = groupCamIds.length === currentCamIds.length && groupCamIds.every((val, index) => val === currentCamIds[index])
      if (!match) {
        selectedGroupId.value = null
      }
    }
  }
  fetchStats()
})

const startDate = ref('')
const endDate = ref('')
const loading = ref(false)
const hasData = ref(false)
const selectedMetric = ref('occupancy_pct')
let chart = null
let rawStats = []

const cameraOptions = computed(() => {
  return cameras.value.map(c => ({ title: c.name, value: c.id }))
})

const metricOptions = [
  { title: 'Occupancy (%)', value: 'occupancy_pct' },
  { title: 'Occupied Spaces', value: 'occupied_count' },
  { title: 'Inference Time (ms)', value: 'inference_speed' }
]

const currentMetricTitle = computed(() => {
  return metricOptions.find(m => m.value === selectedMetric.value)?.title || ''
})

async function fetchCameras() {
  const res = await axios.get('/api/cameras')
  cameras.value = res.data
  
  try {
    const groupsRes = await axios.get('/api/camera-groups')
    groups.value = groupsRes.data || []
    if (groups.value.length > 0) {
      selectedGroupId.value = 'all'
    }
  } catch (e) {
    console.warn('Failed to fetch camera groups:', e)
    groups.value = []
  }
  
  if (route.params.cameraId) {
    selectedCameras.value = [Number(route.params.cameraId)]
  }
}

function setRange(type) {
  const now = new Date()
  endDate.value = now.toISOString().split('T')[0]
  
  if (type === 'today') {
    startDate.value = endDate.value
  } else if (type === 'week') {
    const weekAgo = new Date()
    weekAgo.setDate(now.getDate() - 7)
    startDate.value = weekAgo.toISOString().split('T')[0]
  }
  fetchStats()
}

async function fetchStats() {
  if (!startDate.value || !endDate.value) return
  
  loading.value = true
  hasData.value = false
  
  try {
    const startStr = `${startDate.value}T00:00:00`
    const endStr = `${endDate.value}T23:59:59`
    
    // If range > 2 days, use hourly stats
    const diffDays = (new Date(endDate.value) - new Date(startDate.value)) / (1000 * 60 * 60 * 24);
    const endpoint = diffDays > 2 ? '/api/stats/hourly' : '/api/stats';

    const params = new URLSearchParams()
    params.append('start', startStr)
    params.append('end', endStr)
    selectedCameras.value.forEach(id => {
      params.append('camera_ids', id)
    })
    
    const res = await axios.get(endpoint, { params })
    rawStats = res.data;
    isHourlyData = diffDays > 2;
    processDataAndRender()
  } catch (error) {
    console.error('Error fetching stats:', error)
  } finally {
    loading.value = false
  }
}

let isHourlyData = false;

function processDataAndRender() {
    if (!rawStats || rawStats.length === 0) {
        hasData.value = false
        if (chart) chart.destroy()
        chart = null
        return
    }

    hasData.value = true
    
    // Backend now returns aggregated/bucketed data for both endpoints in a consistent format:
    // {timestamp, occupied, total}
    
    let chartData = rawStats.map(record => {
        return {
            x: new Date(record.timestamp + (record.timestamp.includes('Z') ? '' : 'Z')),
            occupied: Math.round(record.occupied * 10) / 10,
            total: Math.round(record.total * 10) / 10,
            pct: record.total > 0 ? Math.round((record.occupied / record.total) * 1000) / 10 : 0,
            speed: record.speed ? Math.round(record.speed * 1000) : 0
        };
    });

    renderChart(chartData)
}

function renderChart(data) {
  const chartCanvas = document.getElementById('historyChart')
  if (!chartCanvas) return
  
  const ctx = chartCanvas.getContext('2d')
  
  // Clean up any existing instance thoroughly
  const existingChart = Chart.getChart(chartCanvas)
  if (existingChart) {
    existingChart.destroy()
  }
  if (chart) {
    chart.destroy()
    chart = null
  }
  
  const isPercentage = selectedMetric.value === 'occupancy_pct'
  const isSpeed = selectedMetric.value === 'inference_speed'
  const datasets = []

  if (selectedMetric.value === 'occupancy_pct') {
    datasets.push({
      label: 'Occupancy (%)',
      data: data.map(d => d.pct),
      borderColor: '#2196F3',
      backgroundColor: 'rgba(33, 150, 243, 0.1)',
      fill: true,
      tension: 0.3,
      pointRadius: data.length > 100 ? 0 : 3
    })
  } else if (selectedMetric.value === 'occupied_count') {
    datasets.push({
      label: 'Occupied Spaces',
      data: data.map(d => d.occupied),
      borderColor: '#F44336',
      backgroundColor: 'rgba(244, 67, 54, 0.1)',
      fill: true,
      tension: 0.3,
      pointRadius: data.length > 100 ? 0 : 3
    })
    datasets.push({
      label: 'Total Spaces',
      data: data.map(d => d.total),
      borderColor: '#4CAF50',
      backgroundColor: 'transparent',
      borderDash: [5, 5],
      fill: false,
      tension: 0.3,
      pointRadius: data.length > 100 ? 0 : 3
    })
  } else if (selectedMetric.value === 'inference_speed') {
    datasets.push({
      label: 'Inference Time (ms)',
      data: data.map(d => d.speed),
      borderColor: '#607D8B',
      backgroundColor: 'rgba(96, 125, 139, 0.1)',
      fill: true,
      tension: 0.3,
      step: 'middle',
      pointRadius: data.length > 100 ? 0 : 3
    })
  } else if (selectedMetric.value === 'total_count') {
    datasets.push({
      label: 'Total Spaces',
      data: data.map(d => d.total),
      borderColor: '#4CAF50',
      backgroundColor: 'rgba(76, 175, 80, 0.1)',
      fill: true,
      tension: 0.3,
      pointRadius: data.length > 100 ? 0 : 3
    })
  }
  
  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.map(d => {
          return new Intl.DateTimeFormat('default', {
              month: 'short',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit'
          }).format(d.x)
      }),
      datasets: datasets
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        intersect: false,
        mode: 'index',
      },
      scales: {
        y: {
          beginAtZero: true,
          title: {
              display: true,
              text: isPercentage ? 'Occupancy (%)' : (isSpeed ? 'Time (ms)' : 'Spaces')
          },
          ticks: {
            callback: function(value) {
                if (isPercentage) return value + '%'
                if (isSpeed) return value + 'ms'
                return value
            }
          },
          suggestedMax: isPercentage ? 100 : undefined
        }
      },
      plugins: {
          tooltip: {
              callbacks: {
                  label: function(context) {
                      let label = context.dataset.label || ''
                      if (label) {
                          label += ': '
                      }
                      label += context.parsed.y
                      if (isPercentage) label += '%'
                      else if (isSpeed) label += 'ms'
                      return label
                  }
              }
          },
          legend: {
              display: true,
              position: 'top'
          }
      }
    }
  })
}

onMounted(async () => {
  await fetchCameras()
  setRange('today')
})

onUnmounted(() => {
  if (chart) chart.destroy()
})
</script>

<style scoped>
canvas {
  width: 100% !important;
  height: 100% !important;
}
</style>
