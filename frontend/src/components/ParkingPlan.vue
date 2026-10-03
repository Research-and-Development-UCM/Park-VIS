<template>
  <section class="parking-plan">
    <header class="plan-heading"><div><h2>{{ layout?.name || lotName || 'Parking overview' }}</h2><p>{{ saved ? 'Your saved layout, connected to live camera occupancy.' : 'Automatic layout. Arrange spaces in Parking Layout.' }}</p></div><router-link v-if="canEdit" :to="{path:'/layout',query:lot ? {lot} : {}}">Edit this lot →</router-link></header>
    <div class="plan-summary"><strong>{{ counts.available }} available</strong><span>{{ counts.occupied }} occupied</span><span>{{ counts.unknown }} unknown</span><button @click="controller?.fit()">Fit map</button></div>
    <p v-if="error" class="plan-error" role="status">{{ error }}</p>
    <div v-if="!cameras.some(c => c.spaces.length)" class="plan-empty">No spaces mapped yet. Add parking spaces in the camera's Space Editor.</div>
    <div v-else ref="mapElement" class="parking-map plan-map" aria-label="Interactive top-down parking map"></div>
    <p class="plan-footnote">Scroll to zoom · Drag to pan · Select a linked space for history · Gray means no recent occupancy data</p>
  </section>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import axios from 'axios';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { defaultLayout, createParkingMap, installParkingStyle } from '../utils/parkingMap';
const props=defineProps({cameras:{type:Array,required:true},lot:{type:String,default:''},lotName:{type:String,default:''}}),emit=defineEmits(['select']);
const layout=ref(null),saved=ref(false),mapElement=ref(null),error=ref(''),revision=ref(0);
let controller=null,observer=null,timer=null,disposed=false;
const canEdit=computed(()=>{try{return localStorage.getItem('is_admin')==='true'||JSON.parse(localStorage.getItem('permissions')||'[]').includes('manage_cameras');}catch{return false;}});
const states=computed(()=>{const result={};for(const cam of props.cameras)for(const s of cam.spaces)result[s.id]={occupied:s.occupancyKnown?s.occupied:null,updated_at:cam.lastUpdated?.toISOString()};return result;});
const visibleLayout=computed(()=>{
  const value=layout.value||defaultLayout(props.cameras);
  const visibleIds=new Set(props.cameras.flatMap(c=>c.spaces.map(s=>s.id)));
  return {...value,items:value.items.filter(i=>i.kind!=='stall'||!i.space_id||visibleIds.has(i.space_id))};
});
const counts=computed(()=>{revision.value;const result={available:0,occupied:0,unknown:0};for(const item of visibleLayout.value.items)if(item.kind==='stall')result[controller?.status(item)||'unknown']++;return result;});
async function sync(){
  await nextTick();if(disposed)return;
  if(!mapElement.value){controller?.destroy();controller=null;observer?.disconnect();return;}
  if(!controller){
    controller=createParkingMap(L,mapElement.value,visibleLayout.value,states.value,{select(item){if(item.kind!=='stall')return;const space=props.cameras.flatMap(c=>c.spaces).find(s=>s.id===item.space_id);if(space)emit('select',space);}});
    observer=new ResizeObserver(()=>controller?.map.invalidateSize());observer.observe(mapElement.value);
  }else controller.update(visibleLayout.value,states.value);
  revision.value++;
}
watch([visibleLayout,states],sync,{deep:true});
onMounted(async()=>{installParkingStyle();try{const {data}=await axios.get('/api/parking-layout',{params:props.lot?{lot:props.lot}:{}});if(disposed)return;layout.value=data.layout;saved.value=!!data.layout;}catch{error.value='Saved layout could not load. Showing an automatic arrangement.';}await sync();timer=setInterval(sync,10000);});
onUnmounted(()=>{disposed=true;clearInterval(timer);observer?.disconnect();controller?.destroy();});
</script>
<style scoped>
.parking-plan{width:100%;padding:8px 0 28px;color:#20332d}.plan-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap;margin-bottom:20px}.plan-heading h2{font-size:24px;letter-spacing:-.6px}.plan-heading p,.plan-footnote{color:#71816f;font-size:12px;margin-top:5px}.plan-heading a{color:#286749;font-size:13px;text-decoration:none}.plan-summary{display:flex;align-items:center;gap:24px;padding:16px 22px;border:1px solid #dce4d7;border-bottom:0;background:#fff;border-radius:12px 12px 0 0;font-size:13px;flex-wrap:wrap}.plan-summary strong{color:#2a7850}.plan-summary span{color:#7e887a}.plan-summary button{margin-left:auto;color:#476a4c;border:1px solid #dce4d7;border-radius:6px;padding:5px 10px;font-size:12px}.plan-map{height:570px;min-height:300px;border:1px solid #dce4d7;border-radius:0 0 12px 12px}.plan-footnote{margin-top:12px}.plan-empty,.plan-error{padding:24px;color:#76837b;background:#f5f7f3}.plan-error{color:#9e5c43;padding:12px}@media(max-width:600px){.plan-map{height:430px}.plan-summary{gap:12px}}
</style>
