<template>
  <v-app class="app-container">
    <v-navigation-drawer v-if="!isPublicPage" v-model="drawer" :permanent="!mobile" :temporary="mobile" :width="244" class="sidebar-drawer" elevation="0">
      <router-link to="/dashboard" class="pv-brand"><span class="pv-brand-mark">P<span>V</span></span><div><strong>PARKING VIS</strong><small>SPACE OBSERVATORY</small></div></router-link>
      <div class="sidebar-rule"></div>
      <v-list nav density="compact" class="pv-navigation">
        <template v-for="section in navigation" :key="section.name">
          <div class="nav-subheader">{{ section.name }}</div>
          <v-list-item v-for="item in section.items.filter(i => !i.permission || hasPermission(i.permission))" :key="item.to" :to="item.to" :title="item.title" :prepend-icon="item.icon" class="nav-item" color="primary" :rounded="false"><template #append><span class="nav-index">{{ item.index }}</span></template></v-list-item>
        </template>
      </v-list>
      <template #append><div class="sidebar-footer"><div class="station-label"><span class="station-dot"></span> LOCAL STATION</div><div class="sidebar-clock">{{ currentTime }}</div><small>PARKING VIS / CONTROL ROOM</small></div></template>
    </v-navigation-drawer>
    <v-app-bar v-if="!isPublicPage" class="app-header" elevation="0" height="72">
      <v-btn v-if="mobile" icon="mdi-menu" variant="text" aria-label="Open navigation" @click="drawer = !drawer"></v-btn>
      <div class="header-location"><small>CONTROL ROOM / {{ pageNumber }}</small><strong>{{ pageTitle }}</strong></div>
      <v-spacer></v-spacer><span class="header-date d-none d-lg-block">{{ currentDate }}</span>
      <v-menu location="bottom end"><template #activator="{ props }"><v-btn v-bind="props" variant="text" class="user-menu-btn" aria-label="Account menu"><span class="user-monogram">{{ username?.charAt(0).toUpperCase() }}</span><span class="d-none d-sm-block user-label"><strong>{{ username }}</strong><small>{{ roleLabel }}</small></span><v-icon size="16">mdi-chevron-down</v-icon></v-btn></template><v-card min-width="220"><v-list density="compact"><v-list-item @click="showPasswordDialog = true" prepend-icon="mdi-lock-reset" title="Change Password"></v-list-item><v-list-item @click="logout" prepend-icon="mdi-logout" title="Sign out"></v-list-item></v-list></v-card></v-menu>
    </v-app-bar>
    <v-main :scrollable="!isPublicPage" :class="['main-content', isPublicPage ? 'pa-0' : 'content-bg']"><div :class="['page-wrapper', { 'pa-5': !isPublicPage && !isHistoryPage }]"><router-view /></div></v-main>
    <ChangePasswordDialog v-model="showPasswordDialog" />
  </v-app>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useDisplay } from 'vuetify';
import { useRouter, useRoute } from 'vue-router';
import axios from 'axios';
import ChangePasswordDialog from './components/ChangePasswordDialog.vue';
const router=useRouter(),route=useRoute(),{mobile}=useDisplay();
const drawer=ref(!mobile.value),showPasswordDialog=ref(false),username=ref(localStorage.getItem('username')||'Operator'),isAdmin=ref(localStorage.getItem('is_admin')==='true'),currentTime=ref(''),currentDate=ref('');
let timer;
const navigation=[
  {name:'01 / OBSERVE',items:[{title:'Live overview',to:'/dashboard',icon:'mdi-view-dashboard-outline',index:'01'},{title:'History',to:'/history',icon:'mdi-history',index:'02',permission:'view_history'},{title:'Analytics',to:'/metrics',icon:'mdi-chart-box-outline',index:'03',permission:'view_metrics'}]},
  {name:'02 / ARRANGE',items:[{title:'Parking layout',to:'/layout',icon:'mdi-map-outline',index:'04',permission:'manage_cameras'},{title:'Cameras & spaces',to:'/cameras',icon:'mdi-camera-outline',index:'05',permission:'manage_cameras'},{title:'Alerts',to:'/alerts',icon:'mdi-bell-ring-outline',index:'06'}]},
  {name:'03 / SYSTEM',items:[{title:'API access',to:'/access',icon:'mdi-api',index:'07',permission:'manage_api_keys'},{title:'Users',to:'/users',icon:'mdi-account-group-outline',index:'08',permission:'manage_users'},{title:'Settings',to:'/settings',icon:'mdi-cog-outline',index:'09',permission:'edit_settings'},{title:'Diagnostics',to:'/diagnostics',icon:'mdi-shield-bug-outline',index:'10',permission:'view_diagnostics'}]},
];
const activeItem=computed(()=>navigation.flatMap(s=>s.items).find(i=>route.path===i.to||route.path.startsWith(i.to+'/')));
const pageTitle=computed(()=>activeItem.value?.title||(route.path.startsWith('/editor')?'Space editor':'Parking Vis'));
const pageNumber=computed(()=>activeItem.value?.index||'PV');
const isPublicPage=computed(()=>route.path==='/'||route.path==='/setup');
const isHistoryPage=computed(()=>route.path==='/history');
const roleLabel=computed(()=>isAdmin.value?'Administrator':'Operator');
function hasPermission(p){if(isAdmin.value)return true;try{return JSON.parse(localStorage.getItem('permissions')||'[]').includes(p);}catch{return false;}}
function logout(){for(const key of ['token','permissions','username','is_admin'])localStorage.removeItem(key);delete axios.defaults.headers.common.Authorization;router.push('/');}
async function syncUser(){if(isPublicPage.value||!localStorage.getItem('token'))return;try{const {data}=await axios.get('/api/users/me');username.value=data.username;isAdmin.value=data.is_admin;localStorage.setItem('username',data.username);localStorage.setItem('is_admin',String(data.is_admin));localStorage.setItem('permissions',JSON.stringify(data.permissions));}catch(e){if(e.response?.status===401)logout();}}
function tick(){const now=new Date();currentTime.value=now.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});currentDate.value=now.toLocaleDateString([], {month:'short',day:'2-digit',year:'numeric'}).toUpperCase();}
watch(mobile,value=>drawer.value=!value);
watch(()=>route.path,()=>{if(mobile.value)drawer.value=false;syncUser();});
onMounted(()=>{tick();timer=setInterval(tick,1000);syncUser();});
onUnmounted(()=>clearInterval(timer));
</script>
