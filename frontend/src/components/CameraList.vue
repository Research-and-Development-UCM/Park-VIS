<template>
  <v-container fluid>
    <v-card elevation="2">
      <v-toolbar color="white" density="comfortable">
        <v-toolbar-title class="text-h6">Cameras</v-toolbar-title>
        <v-spacer></v-spacer>
        <v-btn v-if="hasPermission('manage_cameras')" color="primary" prepend-icon="mdi-plus" @click="openAddDialog">Add Camera</v-btn>
      </v-toolbar>
      <v-divider></v-divider>
      <v-data-table :items="cameras" :headers="headers" :loading="loading" hover density="comfortable">
        <template #item.snapshot_url="{ item }">
          <span v-if="item.source_type === 'test'" class="text-grey italic">Local Test Image</span>
          <span v-else-if="item.source_type === 'snapshot'">{{ item.snapshot_url }}</span>
          <span v-else>{{ item.stream_url }}</span>
        </template>
        <template #item.source_type="{ item }">
          <v-chip size="x-small" label border :color="item.is_enabled ? '' : 'grey'">
            {{ item.source_type.toUpperCase() }}
            <span v-if="item.source_type === 'youtube'" class="ml-1 text-caption" style="font-size: 0.6rem">
              ({{ item.youtube_mode === 'thumbnail' ? 'THUMB' : 'STREAM' }})
            </span>
          </v-chip>
        </template>
        <template #item.is_enabled="{ item }">
          <v-switch
            v-model="item.is_enabled"
            density="compact"
            hide-details
            color="success"
            @change="toggleCamera(item)"
            :readonly="!hasPermission('manage_cameras')"
          ></v-switch>
        </template>
        <template #item.actions="{ item }">
          <v-btn icon="mdi-vector-square" variant="text" color="primary" size="small" @click="editCamera(item)" title="Edit Parking Spaces"></v-btn>
          <v-btn v-if="hasPermission('manage_cameras')" icon="mdi-pencil" variant="text" color="secondary" size="small" @click="openEditDialog(item)" title="Edit Camera Info"></v-btn>
          <v-btn v-if="hasPermission('manage_cameras')" icon="mdi-delete" variant="text" color="error" size="small" @click="confirmDeleteCamera(item)" title="Delete"></v-btn>
        </template>
      </v-data-table>
    </v-card>

    <!-- Camera Dialog -->
    <v-dialog v-model="dialog" max-width="500px">
      <v-card>
        <v-card-title>
          <span class="text-h5">{{ editingId ? 'Edit Camera' : 'New Camera' }}</span>
        </v-card-title>
        <v-card-text>
          <v-form ref="form" v-model="valid" @submit.prevent="saveCamera">
            <v-text-field
              v-model="editedItem.name"
              label="Camera Name"
              variant="outlined"
              :rules="[v => !!v || 'Required']"
            ></v-text-field>

            <v-select
              v-model="editedItem.source_type"
              :items="[
                { title: 'HTTP Snapshot', value: 'snapshot' },
                { title: 'RTSP Stream', value: 'rtsp' },
                { title: 'YouTube Live', value: 'youtube' },
                { title: 'Local Video Loop', value: 'video' },
                { title: 'Static Test Image', value: 'test' }
              ]"
              label="Source Type"
              variant="outlined"
            ></v-select>

            <v-file-input
              v-if="editedItem.source_type === 'video'"
              v-model="videoFile"
              label="Select Video File (.mp4, .avi)"
              variant="outlined"
              accept="video/mp4,video/x-msvideo"
              prepend-icon="mdi-video"
              :rules="editingId ? [] : [v => !!v || 'Video file is required for this source type']"
              hint="Selecting a new file will overwrite the existing video loop"
              :persistent-hint="!!editingId"
            ></v-file-input>

            <v-text-field
              v-if="editedItem.source_type === 'snapshot'"
              v-model="editedItem.snapshot_url"
              label="Snapshot URL"
              placeholder="http://192.168.1.10/snap.jpg"
              variant="outlined"
              :rules="[v => !!v || 'Required']"
            ></v-text-field>

            <v-file-input
              v-else-if="editedItem.source_type === 'test'"
              v-model="testFile"
              label="Upload Test JPEG"
              accept="image/jpeg,image/png"
              variant="outlined"
              prepend-icon="mdi-camera"
              :rules="editingId ? [] : [v => !!v || 'Required']"
              hint="Selecting a new file will overwrite the existing test image"
              persistent-hint
            ></v-file-input>

            <v-text-field
              v-else-if="!['snapshot', 'test', 'video'].includes(editedItem.source_type)"
              v-model="editedItem.stream_url"
              :label="editedItem.source_type === 'youtube' ? 'YouTube URL' : 'RTSP URL'"
              :placeholder="editedItem.source_type === 'youtube' ? 'https://www.youtube.com/watch?v=...' : 'rtsp://192.168.1.10:554/live'"
              variant="outlined"
              :rules="[v => !!v || 'Required']"
            ></v-text-field>

            <v-select
              v-if="editedItem.source_type === 'youtube'"
              v-model="editedItem.youtube_mode"
              :items="[
                { title: 'Live Stream (Heavy)', value: 'stream' },
                { title: 'Latest Thumbnail (Light)', value: 'thumbnail' }
              ]"
              label="YouTube Fetch Mode"
              variant="outlined"
              hint="Thumbnail mode is less likely to be blocked by YouTube"
              persistent-hint
              class="mb-4"
            ></v-select>

            <v-select
              v-if="editedItem.source_type === 'youtube' && editedItem.youtube_mode === 'stream'"
              v-model="editedItem.stream_resolution"
              :items="[
                { title: '1080p (FHD)', value: '1080' },
                { title: '720p (HD)', value: '720' },
                { title: '480p (SD)', value: '480' },
                { title: '360p', value: '360' }
              ]"
              label="Preferred YouTube Quality"
              variant="outlined"
              hint="The system will pull the best quality available up to this limit"
              persistent-hint
              class="mb-4"
            ></v-select>

            <v-select
              v-if="['rtsp', 'youtube'].includes(editedItem.source_type) && (editedItem.source_type !== 'youtube' || editedItem.youtube_mode === 'stream')"
              v-model="editedItem.stream_fps"
              :items="[1, 2, 5, 10, 15, 30]"
              label="Inference FPS"
              variant="outlined"
              hint="Lower FPS saves CPU and bandwidth"
              persistent-hint
            ></v-select>

            <v-row v-if="editedItem.source_type === 'rtsp'">
              <v-col cols="6">
                <v-text-field
                  v-model="editedItem.stream_user"
                  label="Username (Optional)"
                  variant="outlined"
                  hide-details
                ></v-text-field>
              </v-col>
              <v-col cols="6">
                <v-text-field
                  v-model="editedItem.stream_password"
                  label="Password (Optional)"
                  type="password"
                  variant="outlined"
                  hide-details
                ></v-text-field>
              </v-col>
            </v-row>

            <v-switch
              v-model="editedItem.is_enabled"
              label="Camera Enabled"
              color="success"
              hide-details
              class="mt-4"
            ></v-switch>
          </v-form>
        </v-card-text>
        <v-divider v-if="saving"></v-divider>
        <div v-if="saving" class="pa-4">
          <div v-if="(editedItem.source_type === 'test' && testFile) || (editedItem.source_type === 'video' && videoFile)" class="text-caption mb-1">
            Uploading {{ 
              editedItem.source_type === 'video' 
                ? (Array.isArray(videoFile) ? videoFile[0]?.name : videoFile?.name) 
                : (Array.isArray(testFile) ? testFile[0]?.name : testFile?.name) 
            }}... {{ uploadProgress }}%
          </div>
          <div v-else class="text-caption mb-1">
            Saving camera configuration...
          </div>
          <v-progress-linear
            v-model="uploadProgress"
            :indeterminate="!testFile && !videoFile"
            color="primary"
            height="10"
            striped
          ></v-progress-linear>
        </div>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="grey-darken-1" variant="text" @click="dialog = false">Cancel</v-btn>
          <v-btn color="primary" variant="text" :disabled="!valid" @click="saveCamera" :loading="saving">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!--
      Camera Groups section.

      Moved here from the Alerts page so that the "groups" concept
      lives next to the cameras it groups — operators editing a
      camera also see which groups it belongs to, and don't have
      to context-switch into a separate Alerts tab to manage the
      group set.  The backend endpoint and CameraGroupForm
      component are unchanged.
    -->
    <v-card elevation="2" class="mt-6">
      <v-toolbar color="white" density="comfortable">
        <v-toolbar-title class="text-h6">Parking Lots</v-toolbar-title>
        <v-spacer></v-spacer>
        <v-btn
          v-if="hasPermission('manage_cameras')"
          color="primary" prepend-icon="mdi-plus"
          @click="openNewGroup"
        >Add Parking Lot</v-btn>
      </v-toolbar>
      <v-divider></v-divider>
      <v-data-table
        :items="groups"
        :headers="groupHeaders"
        :loading="loadingGroups"
        hover
        density="comfortable"
      >
        <template #item.camera_count="{ item }">
          <v-chip size="x-small" label border variant="tonal">
            {{ item.camera_ids.length }} {{ item.camera_ids.length === 1 ? 'camera' : 'cameras' }}
          </v-chip>
        </template>
        <template #item.last_edited_by_username="{ item }">
          <span class="text-caption">{{ item.last_edited_by_username || '—' }}</span>
        </template>
        <template #item.actions="{ item }">
          <div v-if="hasPermission('manage_cameras')" class="d-flex justify-end">
            <v-btn
              icon="mdi-pencil" variant="text" color="primary" size="small"
              @click="openEditGroup(item)" title="Edit"
            ></v-btn>
            <v-btn
              icon="mdi-delete" variant="text" color="error" size="small"
              @click="confirmDeleteGroup(item)" title="Delete"
            ></v-btn>
          </div>
        </template>
      </v-data-table>
    </v-card>

    <CameraGroupForm
      v-model="groupDialogOpen"
      :group="editingGroup"
      @saved="onGroupSaved"
      @error="(msg) => showSnackbar(msg, 'error')"
    />

    <!-- Delete confirmation (groups) -->
    <v-dialog v-model="groupDeleteDialog" max-width="400px">
      <v-card>
        <v-card-title class="text-h5">Confirm Delete</v-card-title>
        <v-card-text>
          Delete parking lot <b>{{ groupToDelete?.name }}</b>?
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="grey-darken-1" variant="text" @click="groupDeleteDialog = false">Cancel</v-btn>
          <v-btn color="error" variant="text" :loading="groupDeleting" @click="doDeleteGroup">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!--
      Delete confirmation (cameras). A camera delete cascades to its
      spaces, scans, and history (see backend.crud.delete_camera), so
      an accidental click is destructive.  Previously deleteCamera had
      no confirm and no error handling — the icon's tiny hit area
      made a mis-click very easy.
    -->
    <v-dialog v-model="cameraDeleteDialog" max-width="450px">
      <v-card>
        <v-card-title class="text-h5">Confirm Delete</v-card-title>
        <v-card-text>
          Delete camera <b>{{ cameraToDelete?.name }}</b> and all of its
          spaces, scans, and history?
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="grey-darken-1" variant="text" @click="cameraDeleteDialog = false">Cancel</v-btn>
          <v-btn color="error" variant="text" :loading="cameraDeleting" @click="doDeleteCamera">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- Snackbar -->
    <v-snackbar
      v-model="snackbar.show"
      :color="snackbar.color"
      :timeout="3000"
      location="bottom right"
    >
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { useRouter } from 'vue-router'
import CameraGroupForm from './CameraGroupForm.vue'

const router = useRouter()
const cameras = ref([])
const loading = ref(true)
const valid = ref(false)
const dialog = ref(false)
const editingId = ref(null)
const saving = ref(false)
const uploadProgress = ref(0)
const testFile = ref(null)
const videoFile = ref(null)

const snackbar = ref({ show: false, text: '', color: 'success' })
const showSnackbar = (text, color = 'success') => {
  snackbar.value = { show: true, text, color }
}

const editedItem = ref({
  name: '',
  source_type: 'snapshot',
  snapshot_url: '',
  stream_url: '',
  stream_user: '',
  stream_password: '',
  stream_resolution: '1080',
  stream_fps: 5,
  youtube_mode: 'stream',
  is_enabled: true
})

const headers = [
  { title: 'ID', key: 'id' },
  { title: 'Name', key: 'name' },
  { title: 'Source', key: 'source_type' },
  { title: 'Endpoint/URL', key: 'snapshot_url' },
  { title: 'Enabled', key: 'is_enabled', width: '100px' },
  { title: 'Actions', key: 'actions', sortable: false, align: 'end' }
]

const fetchCameras = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/cameras')
    cameras.value = res.data
  } finally {
    loading.value = false
  }
}

const toggleCamera = async (camera) => {
  try {
    await axios.put(`/api/cameras/${camera.id}`, {
      ...camera,
      // Ensure all fields are present for the backend update_camera logic
    })
  } catch (error) {
    console.error('Error toggling camera status:', error)
    // Revert state on failure
    camera.is_enabled = !camera.is_enabled
  }
}

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

const openAddDialog = () => {
  editingId.value = null
  testFile.value = null
  videoFile.value = null
  uploadProgress.value = 0
  editedItem.value = {
    name: '',
    source_type: 'snapshot',
    snapshot_url: '',
    stream_url: '',
    stream_user: '',
    stream_password: '',
    stream_resolution: '1080',
    stream_fps: 5,
    youtube_mode: 'stream',
    is_enabled: true
  }
  dialog.value = true
}

const openEditDialog = (cam) => {
  editingId.value = cam.id
  testFile.value = null
  videoFile.value = null
  uploadProgress.value = 0
  editedItem.value = {
    name: cam.name,
    source_type: cam.source_type || 'snapshot',
    snapshot_url: cam.snapshot_url || '',
    stream_url: cam.stream_url || '',
    stream_user: cam.stream_user || '',
    stream_password: cam.stream_password || '',
    stream_resolution: cam.stream_resolution || '1080',
    stream_fps: cam.stream_fps || 5,
    youtube_mode: cam.youtube_mode || 'stream',
    is_enabled: cam.is_enabled !== undefined ? cam.is_enabled : true
  }
  dialog.value = true
}

const saveCamera = async () => {
  saving.value = true
  uploadProgress.value = 0
  try {
    // If it's a test camera with a new file, we use the multipart endpoint
    if (editedItem.value.source_type === 'test' && testFile.value) {
      const formData = new FormData();
      const fileToUpload = Array.isArray(testFile.value) ? testFile.value[0] : testFile.value;
      formData.append('file', fileToUpload);
      
      let url = `/api/cameras/test?name=${encodeURIComponent(editedItem.value.name)}`;
      if (editingId.value) {
        url += `&camera_id=${editingId.value}`;
      }
      
      await axios.post(url, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (progressEvent) => {
          uploadProgress.value = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        }
      });
    } else if (editedItem.value.source_type === 'video' && videoFile.value) {
      const formData = new FormData();
      const fileToUpload = Array.isArray(videoFile.value) ? videoFile.value[0] : videoFile.value;
      formData.append('file', fileToUpload);
      
      let url = `/api/cameras/video?name=${encodeURIComponent(editedItem.value.name)}`;
      if (editingId.value) {
        url += `&camera_id=${editingId.value}`;
      }
      
      await axios.post(url, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (progressEvent) => {
          uploadProgress.value = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        }
      });
    } else {
      // Standard JSON update
      const payload = { ...editedItem.value }
      if (editingId.value) {
        await axios.put(`/api/cameras/${editingId.value}`, payload)
      } else {
        await axios.post('/api/cameras', payload)
      }
    }
    
    dialog.value = false
    fetchCameras()
  } catch (error) {
    console.error("Error saving camera:", error);
  } finally {
    saving.value = false
  }
}

const editCamera = (cam) => {
  router.push(`/editor/${cam.id}`)
}

const deleteCamera = async (id) => {
  // Kept for direct callers (none currently); the table now uses
  // confirmDeleteCamera -> doDeleteCamera for the safer flow.
  try {
    await axios.delete(`/api/cameras/${id}`)
    fetchCameras()
  } catch (e) {
    showSnackbar(e.response?.data?.detail || 'Error deleting camera', 'error')
  }
}

const cameraToDelete = ref(null)
const cameraDeleteDialog = ref(false)
const cameraDeleting = ref(false)

const confirmDeleteCamera = (item) => {
  cameraToDelete.value = item
  cameraDeleteDialog.value = true
}

const doDeleteCamera = async () => {
  if (!cameraToDelete.value) return
  cameraDeleting.value = true
  try {
    await axios.delete(`/api/cameras/${cameraToDelete.value.id}`)
    showSnackbar(`Camera "${cameraToDelete.value.name}" deleted`)
    cameraDeleteDialog.value = false
    fetchCameras()
  } catch (e) {
    showSnackbar(e.response?.data?.detail || 'Error deleting camera', 'error')
  } finally {
    cameraDeleting.value = false
  }
}

// --- Camera Groups (moved from Alerts page) ---

const groups = ref([])
const loadingGroups = ref(true)
const groupDialogOpen = ref(false)
const editingGroup = ref(null)
const groupDeleteDialog = ref(false)
const groupDeleting = ref(false)
const groupToDelete = ref(null)

const groupHeaders = [
  { title: 'ID', key: 'id', width: 60 },
  { title: 'Name', key: 'name' },
  { title: 'Cameras', key: 'camera_count', width: 130 },
  { title: 'Last edited by', key: 'last_edited_by_username', width: 160 },
  { title: 'Actions', key: 'actions', sortable: false, align: 'end', width: 120 },
];

const fetchGroups = async () => {
  loadingGroups.value = true;
  try {
    const res = await axios.get('/api/camera-groups');
    groups.value = res.data;
  } catch (e) {
    showSnackbar('Error loading parking lots', 'error');
  } finally {
    loadingGroups.value = false;
  }
};

const openNewGroup = () => {
  editingGroup.value = null;
  groupDialogOpen.value = true;
};

const openEditGroup = (item) => {
  editingGroup.value = item;
  groupDialogOpen.value = true;
};

const onGroupSaved = () => {
  showSnackbar(editingGroup.value ? 'Parking lot updated' : 'Parking lot created');
  fetchGroups();
};

const confirmDeleteGroup = (item) => {
  groupToDelete.value = item;
  groupDeleteDialog.value = true;
};

const doDeleteGroup = async () => {
  groupDeleting.value = true;
  try {
    await axios.delete(`/api/camera-groups/${groupToDelete.value.id}`);
    showSnackbar('Parking lot deleted');
    groupDeleteDialog.value = false;
    fetchGroups();
  } catch (e) {
    const msg = e.response?.data?.detail || 'Error deleting parking lot';
    showSnackbar(msg, 'error');
  } finally {
    groupDeleting.value = false;
  }
};

onMounted(() => {
  fetchCameras();
  fetchGroups();
});
</script>

<style scoped>
</style>
