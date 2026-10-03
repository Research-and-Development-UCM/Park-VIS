<template>
  <v-container fluid>
    <v-card elevation="2">
      <v-toolbar color="white" density="comfortable">
        <v-toolbar-title class="text-h6">User Management</v-toolbar-title>
        <v-spacer></v-spacer>
        <v-btn color="primary" prepend-icon="mdi-account-plus" @click="openAddDialog">Add User</v-btn>
      </v-toolbar>
      <v-divider></v-divider>
      
      <v-data-table :items="users" :headers="headers" hover density="comfortable">
        <template #item.username="{ item }">
          <div class="d-flex align-center">
            {{ item.username }}
            <v-chip v-if="item.is_admin" size="x-small" color="primary" class="ml-2" variant="flat">ADMIN</v-chip>
          </div>
        </template>
        <template #item.permissions="{ item }">
          <div v-if="item.is_admin" class="text-caption text-primary font-weight-bold d-flex align-center">
            <v-icon start icon="mdi-shield-check" size="small" class="mr-1"></v-icon>
            Full System Access
          </div>
          <div v-else>
            <v-chip v-for="p in item.permissions" :key="p" size="x-small" class="mr-1" color="secondary" variant="tonal">
              {{ formatPermission(p) }}
            </v-chip>
          </div>
        </template>
        
        <template #item.actions="{ item }">
          <div class="d-flex justify-end">
            <v-btn icon="mdi-pencil" variant="text" color="primary" size="small" @click="editUser(item)" title="Edit User"></v-btn>
            <v-btn icon="mdi-delete" variant="text" color="error" size="small" @click="confirmDelete(item)" title="Delete User"></v-btn>
          </div>
        </template>
      </v-data-table>
    </v-card>

    <!-- Add/Edit User Dialog -->
    <v-dialog v-model="dialog" max-width="500px">
      <v-card>
        <v-card-title>
          <span class="text-h5">{{ isEditing ? 'Edit User' : 'New User' }}</span>
        </v-card-title>
        <v-card-text>
          <v-form ref="form" v-model="valid" @submit.prevent="saveUser">
            <v-text-field
              v-model="editedUser.username"
              label="Username"
              variant="outlined"
              :rules="[v => !!v || 'Required']"
              :disabled="isEditing"
            ></v-text-field>
            
            <v-text-field
              v-model="editedUser.password"
              label="Password"
              type="password"
              variant="outlined"
              :placeholder="isEditing ? '(Leave blank to keep current)' : ''"
              :rules="isEditing ? [] : [v => !!v || 'Required']"
            ></v-text-field>

            <v-switch
              v-model="editedUser.is_admin"
              label="System Administrator"
              color="primary"
              hide-details
              class="mb-2"
            ></v-switch>

            <div class="text-subtitle-2 mb-2 mt-4">Granular Permissions</div>
            <div v-if="editedUser.is_admin" class="text-caption text-grey mb-4">
              Administrators automatically have all permissions.
            </div>
            <v-row no-gutters v-else>
              <v-col cols="12" sm="6" v-for="opt in permissionOptions" :key="opt.value">
                <v-checkbox
                  v-model="editedUser.permissions"
                  :label="opt.title"
                  :value="opt.value"
                  density="compact"
                  hide-details
                ></v-checkbox>
              </v-col>
            </v-row>
          </v-form>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="grey-darken-1" variant="text" @click="dialog = false">Cancel</v-btn>
          <v-btn color="primary" variant="text" :disabled="!valid" :loading="saving" @click="saveUser">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- Delete Confirmation -->
    <v-dialog v-model="deleteDialog" max-width="400px">
      <v-card>
        <v-card-title class="text-h5">Confirm Delete</v-card-title>
        <v-card-text>Are you sure you want to delete user <b>{{ userToDelete?.username }}</b>?</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="grey-darken-1" variant="text" @click="deleteDialog = false">Cancel</v-btn>
          <v-btn color="error" variant="text" :loading="deleting" @click="doDelete">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="3000">
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import axios from 'axios';

const users = ref([]);
const dialog = ref(false);
const valid = ref(false);
const isEditing = ref(false);
const saving = ref(false);
const deleteDialog = ref(false);
const deleting = ref(false);
const userToDelete = ref(null);
const snackbar = ref({ show: false, text: '', color: 'success' });

const editedUser = ref({
  id: null,
  username: '',
  password: '',
  is_admin: false,
  permissions: []
});

const headers = [
  { title: 'ID', key: 'id' },
  { title: 'Username', key: 'username' },
  { title: 'Permissions', key: 'permissions', sortable: false },
  { title: 'Actions', key: 'actions', sortable: false, align: 'end' }
];

const permissionOptions = [
  { title: 'Manage Cameras', value: 'manage_cameras' },
  { title: 'View History', value: 'view_history' },
  { title: 'View Metrics', value: 'view_metrics' },
  { title: 'Edit Settings', value: 'edit_settings' },
  { title: 'Manage Users', value: 'manage_users' },
  { title: 'Manage API Keys', value: 'manage_api_keys' },
  { title: 'View Diagnostics', value: 'view_diagnostics' },
  { title: 'Manage Alerts', value: 'manage_alerts' }
];

const formatPermission = (p) => {
  return permissionOptions.find(o => o.value === p)?.title || p;
};

const fetchUsers = async () => {
  try {
    const res = await axios.get('/api/users');
    users.value = res.data;
  } catch (e) {
    console.error("Error fetching users:", e);
  }
};

const openAddDialog = () => {
  isEditing.value = false;
  editedUser.value = {
    id: null,
    username: '',
    password: '',
    is_admin: false,
    permissions: ['manage_cameras', 'view_history']
  };
  dialog.value = true;
};

const editUser = (user) => {
  isEditing.value = true;
  editedUser.value = {
    id: user.id,
    username: user.username,
    password: '',
    is_admin: !!user.is_admin,
    permissions: [...user.permissions]
  };
  dialog.value = true;
};

const saveUser = async () => {
  saving.value = true;
  try {
    const payload = { ...editedUser.value };
    if (isEditing.value) {
      if (!payload.password) delete payload.password;
      await axios.put(`/api/users/${editedUser.value.id}`, payload);
      snackbar.value = { show: true, text: 'User updated', color: 'success' };
    } else {
      await axios.post('/api/users', payload);
      snackbar.value = { show: true, text: 'User created', color: 'success' };
    }
    dialog.value = false;
    fetchUsers();
  } catch (e) {
    const msg = e.response?.data?.detail || "Error saving user";
    snackbar.value = { show: true, text: msg, color: 'error' };
  } finally {
    saving.value = false;
  }
};

const confirmDelete = (user) => {
  userToDelete.value = user;
  deleteDialog.value = true;
};

const doDelete = async () => {
  deleting.value = true;
  try {
    await axios.delete(`/api/users/${userToDelete.value.id}`);
    snackbar.value = { show: true, text: 'User deleted', color: 'success' };
    deleteDialog.value = false;
    fetchUsers();
  } catch (e) {
    const msg = e.response?.data?.detail || "Error deleting user";
    snackbar.value = { show: true, text: msg, color: 'error' };
  } finally {
    deleting.value = false;
  }
};

onMounted(fetchUsers);
</script>
