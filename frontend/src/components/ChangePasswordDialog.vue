<template>
  <v-dialog v-model="visible" max-width="500px">
    <v-card>
      <v-toolbar color="secondary" density="comfortable">
        <v-toolbar-title class="text-h6 text-white">Change Password</v-toolbar-title>
        <v-spacer></v-spacer>
        <v-btn icon="mdi-close" @click="visible = false"></v-btn>
      </v-toolbar>
      <v-divider></v-divider>
      <v-card-text class="pa-6">
        <v-form ref="pwForm" v-model="pwValid" @submit.prevent="submit">
          <v-text-field
            v-model="oldPassword"
            label="Current Password"
            type="password"
            variant="outlined"
            density="comfortable"
            :rules="[v => !!v || 'Required']"
          ></v-text-field>
          <v-text-field
            v-model="newPassword"
            label="New Password"
            type="password"
            variant="outlined"
            density="comfortable"
            :rules="[v => !!v || 'Required', v => v.length >= 4 || 'Min 4 characters']"
          ></v-text-field>
          <v-text-field
            v-model="confirmPassword"
            label="Confirm New Password"
            type="password"
            variant="outlined"
            density="comfortable"
            :rules="[v => !!v || 'Required', v => v === newPassword || 'Passwords do not match']"
          ></v-text-field>
        </v-form>
      </v-card-text>
      <v-divider></v-divider>
      <v-card-actions class="pa-4 bg-grey-lighten-5">
        <v-spacer></v-spacer>
        <v-btn color="grey-darken-1" variant="text" @click="visible = false">Cancel</v-btn>
        <v-btn
          color="secondary"
          variant="flat"
          :loading="loading"
          :disabled="!pwValid"
          prepend-icon="mdi-lock-reset"
          @click="submit"
        >
          Update Password
        </v-btn>
      </v-card-actions>
    </v-card>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="3000">
      {{ snackbar.text }}
      <template v-slot:actions>
        <v-btn variant="text" @click="snackbar.show = false">Close</v-btn>
      </template>
    </v-snackbar>
  </v-dialog>
</template>

<script setup>
import { ref, watch } from 'vue';
import axios from 'axios';

const props = defineProps({
  modelValue: Boolean
});
const emit = defineEmits(['update:modelValue']);

const visible = ref(false);
const pwValid = ref(false);
const loading = ref(false);
const oldPassword = ref('');
const newPassword = ref('');
const confirmPassword = ref('');
const pwForm = ref(null);
const snackbar = ref({ show: false, text: '', color: 'success' });

watch(() => props.modelValue, (val) => {
  visible.value = val;
  if (val) {
    oldPassword.value = '';
    newPassword.value = '';
    confirmPassword.value = '';
    if (pwForm.value) pwForm.value.resetValidation();
  }
});

watch(visible, (val) => {
  emit('update:modelValue', val);
});

const submit = async () => {
  if (!pwValid.value) return;
  loading.value = true;
  try {
    await axios.post('/api/change-password', {
      old_password: oldPassword.value,
      new_password: newPassword.value
    });
    snackbar.value = { show: true, text: 'Password updated successfully', color: 'success' };
    setTimeout(() => {
      visible.value = false;
    }, 1500);
  } catch (error) {
    console.error('Error changing password:', error);
    const detail = error.response?.data?.detail || 'Error updating password';
    snackbar.value = { show: true, text: detail, color: 'error' };
  } finally {
    loading.value = false;
  }
};
</script>
