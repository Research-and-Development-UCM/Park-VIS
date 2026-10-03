<template>
  <section class="layout-editor" :class="{ expanded }">
    <header class="editor-heading">
      <div><span class="eyebrow">PARKING LAYOUT</span><h1>Make the lot your own.</h1><p>Arrange your display without changing camera detection.</p></div>
      <div class="heading-actions"><router-link :to="dashboardLink">View this lot live</router-link><button class="primary" :disabled="busy || !layout" @click="save">{{ busy ? 'Saving…' : dirty ? 'Save layout •' : 'Save layout' }}</button></div>
    </header>
    <div v-if="message" class="editor-message" :class="{ error: isError }" role="status">{{ message }}</div>
    <label class="lot-selector">Parking lot<select :value="selectedLot" :disabled="busy || lotsLoading" @change="chooseLot($event)"><option value="" disabled>Select a parking lot…</option><option v-for="lot in lotOptions" :key="lot.id" :value="lot.id">{{ lot.name }}</option></select></label>
    <p class="helper">Camera groups represent parking lots with multiple cameras. Create or edit groups in Cameras & spaces.</p>
    <div v-if="!layout" class="editor-loading">{{ lotsLoading || busy ? 'Loading your spaces…' : loadFailed ? 'Layout could not load. Select the lot to try again.' : 'Choose a parking lot above to start arranging its spaces.' }}</div>
    <template v-else>
      <div class="editor-toolbar">
        <div><button @click="add('stall')">+ Space</button><button :class="{active:drawing}" @click="toggleDrawing">{{ drawing ? 'Finish drawing' : 'Draw spaces' }}</button><button @click="add('road')">+ Road</button><button @click="add('label')">+ Label</button><button @click="add('entry')">+ Entrance</button></div>
        <div><button :disabled="!selected" @click="copyObject">Copy</button><button :disabled="!copiedObject" @click="pasteObject">Paste</button><button :disabled="!selected" @click="duplicate">Duplicate</button><button :disabled="!undoStack.length" @click="undo">Undo</button><button @click="controller?.fit()">Fit map</button><button @click="toggleExpanded">{{ expanded ? 'Exit expanded view' : 'Expand workspace' }}</button><button v-if="layout.background" @click="toggleTracing">{{ tracing ? 'Standard view' : 'Trace aerial' }}</button><label class="file-button">Background<input type="file" accept="image/png,image/jpeg,image/webp" @change="uploadBackground" /></label></div>
      </div>
      <div class="editor-workspace">
        <div class="map-panel"><div ref="mapElement" class="parking-map"></div><div class="map-hint">{{ drawing ? 'Drag across a parking space to draw its box · Escape to finish' : 'Drag slots to move · Select a slot for resize and rotation handles · Scroll to zoom' }}</div></div>
        <aside class="editor-inspector">
          <div class="inspector-tabs"><button :class="{ active: tab === 'object' }" @click="tab = 'object'">Object</button><button :class="{ active: tab === 'layout' }" @click="tab = 'layout'">Layout & export</button></div>
          <template v-if="tab === 'object'">
            <template v-if="selected">
              <button @click="change('locked', !selected.locked)">{{ selected.locked ? 'Unlock item' : 'Lock item' }}</button><p v-if="selected.locked" class="helper">Position, size, rotation, and removal are locked.</p><h2>{{ selected.kind === 'stall' ? 'Parking space' : selected.kind }}</h2>
              <label>Name<input :value="selected.name" maxlength="80" @change="change('name', $event.target.value)" /></label>
              <label v-if="selected.kind === 'stall'">Live camera space<select :value="selected.space_id ?? ''" @change="change('space_id', $event.target.value ? Number($event.target.value) : null)"><option value="">Unlinked · unknown status</option><option v-for="s in linkOptions" :key="s.id" :value="s.id" :disabled="linkedElsewhere(s.id)">{{ s.camera_name }} / {{ s.name }}{{ linkedElsewhere(s.id) ? ' (already linked)' : '' }}</option></select></label>
              <div class="input-pair"><label>X<input type="number" :disabled="selected.locked" :value="selected.x" min="0" :max="layout.width" @change="changeNumber('x', $event, 0, layout.width)" /></label><label>Y<input type="number" :disabled="selected.locked" :value="selected.y" min="0" :max="layout.height" @change="changeNumber('y', $event, 0, layout.height)" /></label></div>
              <div class="input-pair"><label>Width<input type="number" :disabled="selected.locked" :value="selected.width" min="0.25" step="0.25" max="20000" @change="changeNumber('width', $event, 0.25, 20000)" /></label><label>Height<input type="number" :disabled="selected.locked" :value="selected.height" min="0.25" step="0.25" max="20000" @change="changeNumber('height', $event, 0.25, 20000)" /></label></div>
              <label>Rotation · degrees<input type="number" :disabled="selected.locked" :value="selected.angle" min="-360" max="360" @change="changeNumber('angle', $event, -360, 360)" /></label>
              <div class="inspector-actions"><button @click="duplicate">Duplicate</button><button class="danger" :disabled="selected.locked" @click="remove">Remove</button></div>
              <h3 class="mt-4">Repeat spaces</h3>
              <div class="input-pair"><label>Copies<input v-model.number="repeatCount" type="number" min="1" max="100" /></label><label>Gap<input v-model.number="repeatGap" type="number" min="0" max="1000" /></label></div>
              <div class="inspector-actions"><button @click="repeatObjects('x')">Copy row →</button><button @click="repeatObjects('y')">Copy column ↓</button></div>
              <p class="helper">Ctrl/Cmd+C / V: copy / paste · Ctrl/Cmd+D: duplicate · Arrows: move · Alt+Arrows: resize · Shift: 10-unit steps · Ctrl/Cmd+Z: undo · Delete: remove. Shortcuts pause while typing in a field.</p>
              <p class="helper">Removing a display object leaves the camera space and its detection outline intact.</p>
            </template>
            <div v-else class="selection-empty"><span>↖</span><h2>Select an object</h2><p>Click a space, road, or label to change its size, rotation, and live link.</p></div>
            <hr /><section class="space-generator"><h3>Build parking row</h3><p class="helper">Count includes the first space. Gap is the clear distance between slots. Directions follow the slot rotation.</p>
<div class="input-pair"><label>Space count<input v-model.number="row.count" type="number" min="1" max="1000" /></label><label>Gap<input v-model.number="row.gap" type="number" min="0" max="1000" /></label></div>
<div class="input-pair"><label>Slot width<input v-model.number="row.width" type="number" min="0.25" step="0.25" max="20000" /></label><label>Slot height<input v-model.number="row.height" type="number" min="0.25" step="0.25" max="20000" /></label></div>
<label>Extend direction<select v-model="row.direction"><option value="right">Right (+ horizontal) →</option><option value="left">Left (− horizontal) ←</option><option value="down">Down (+ vertical) ↓</option><option value="up">Up (− vertical) ↑</option></select></label>
<div class="input-pair"><label>Start X<input v-model.number="row.x" type="number" min="0" :max="layout.width" /></label><label>Start Y<input v-model.number="row.y" type="number" min="0" :max="layout.height" /></label></div>
<label>Slot rotation · degrees<input v-model.number="row.angle" type="number" min="-360" max="360" /></label><label>Name prefix<input v-model="row.prefix" maxlength="60" /></label><label>Starting number<input v-model.number="row.start" type="number" min="1" max="1000000" /></label>
<div class="inspector-actions"><button :disabled="!selected" @click="useSelectedForRow">Use selected position & size</button><button @click="buildRow">Add parking row</button></div></section><hr /><h3>Objects <span>{{ layout.items.length }}</span></h3>
            <div class="object-list"><button v-for="item in layout.items" :key="item.id" :class="{ active: selectedId === item.id }" @click="select(item)"><span>{{ item.kind === 'stall' ? '▣' : item.kind === 'road' ? '━' : item.kind === 'entry' ? '↑' : 'T' }}</span>{{ item.name || 'Untitled' }}</button></div>
          </template>
          <template v-else>
            <h2>Your layout</h2><label>Lot name<input v-model="layout.name" maxlength="80" /></label>
            <div class="input-pair"><label>Canvas width<input type="number" :value="layout.width" min="200" max="100000" @change="resize('width', $event)" /></label><label>Canvas height<input type="number" :value="layout.height" min="200" max="100000" @change="resize('height', $event)" /></label></div>
            <p class="helper">The aerial stays fixed to the canvas. Upload a high-resolution image, then match the canvas to it to preserve its proportions.</p><button v-if="layout.background" @click="matchImageCanvas">Match canvas to image</button><button v-if="layout.background" @click="checkpoint(); layout.background = ''">Remove background</button>
            <hr /><h3>Move it anywhere</h3><p class="helper">JSON preserves your editable setup. HTML is a self-contained, read-only map with no admin credentials.</p>
            <div class="export-buttons"><button @click="downloadJson">Export layout JSON</button><label class="file-button">Import layout JSON<input type="file" accept=".json,application/json" @change="importJson" /></label><button @click="downloadSnapshot">Export offline HTML</button></div>
            <hr /><h3>Live website viewer</h3><label>Backend / proxy URL<input v-model="backendUrl" placeholder="https://parking.example.com" /></label><p class="helper">For an external website, enter your publicly reachable HTTPS backend URL. Localhost only works on this computer.</p>
            <button class="primary full-width" :disabled="busy" @click="exportLive">Save & export live HTML</button>
            <template v-if="viewKey"><label>Embed code<textarea readonly :value="embedCode" rows="4"></textarea></label><p class="helper">The link grants read-only access to this layout and its occupancy. Host the exported HTML yourself, or embed the viewer URL above.</p><button class="danger" @click="revoke">Disable existing live viewers</button></template>
          </template>
        </aside>
      </div>
      <footer class="editor-footer"><span><i class="dot free"></i>Available <i class="dot busy"></i>Occupied <i class="dot unknown"></i>No recent data</span><span>{{ linkedCount }} linked spaces · {{ dirty ? 'Unsaved changes' : 'Saved' }}</span></footer>
    </template>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue';
import { onBeforeRouteLeave, useRoute } from 'vue-router';
import axios from 'axios';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { createParkingMap, defaultLayout, statesFromCameras, installParkingStyle, exportViewer, downloadFile } from '../utils/parkingMap';

const layout = ref(null), cameras = ref([]), states = ref({}), selectedId = ref(null), tab = ref('object');
const mapElement = ref(null), message = ref(''), isError = ref(false), busy = ref(false), loadFailed = ref(false);
const baseline = ref(''), undoStack = ref([]), viewKey = ref(''), backendUrl = ref(window.location.origin);
const copiedObject = ref(null), repeatCount = ref(5), repeatGap = ref(10);
const expanded = ref(false), tracing = ref(true), drawing = ref(false);
const row = ref({count:10,gap:4,width:24,height:48,direction:'right',x:100,y:100,angle:0,prefix:'P',start:1});
const route = useRoute(), groups = ref([]), selectedLot = ref(''), lotsLoading = ref(true);
const lotOptions = computed(() => [...groups.value.map(g=>({id:`group:${g.id}`,name:`${g.name} · parking lot`})),...cameras.value.map(c=>({id:`camera:${c.id}`,name:`${c.name} · single camera`})),{id:'overview',name:'All cameras · existing combined layout'}]);
const lotParams = () => selectedLot.value === 'overview' ? {} : {lot:selectedLot.value};
const lotCameras = computed(() => {
  if (selectedLot.value.startsWith('group:')) {const group=groups.value.find(g=>g.id===Number(selectedLot.value.split(':')[1]));return cameras.value.filter(c=>group?.camera_ids?.includes(c.id));}
  if (selectedLot.value.startsWith('camera:')) return cameras.value.filter(c=>c.id===Number(selectedLot.value.split(':')[1]));
  return selectedLot.value === 'overview' ? cameras.value : [];
});
const dashboardLink = computed(()=>({path:'/dashboard',query:selectedLot.value && selectedLot.value !== 'overview' ? {lot:selectedLot.value}: {}}));
let controller = null, timer = null, refreshInFlight = false, observer = null, disposed = false;
const selected = computed(() => layout.value?.items.find(i => i.id === selectedId.value));
const dirty = computed(() => layout.value && JSON.stringify(layout.value) !== baseline.value);
const linkedCount = computed(() => layout.value?.items.filter(i => i.kind === 'stall' && i.space_id).length || 0);
const linkOptions = computed(() => lotCameras.value.flatMap(c => c.spaces.map(s => ({...s,camera_name:c.name}))));
const embedCode = computed(() => `<iframe src="${backendUrl.value.replace(/\/$/,'')}/api/parking-layout/embed/${viewKey.value}" title="Parking availability" width="100%" height="650" style="border:0" loading="lazy"></iframe>`);
async function toggleExpanded() { expanded.value = !expanded.value; await nextTick(); controller?.map.invalidateSize(); }
function toggleDrawing() { drawing.value = !drawing.value; controller?.setDrawing(drawing.value); }
function transformItem(item, values) { if(item.locked)return; checkpoint(); Object.assign(item,values); }
function drawSpace(values) {
  if(layout.value.items.length >= 1000)return notify('A layout can contain up to 1,000 objects.',true);
  checkpoint();const item = {id:crypto.randomUUID(),kind:'stall',name:row.value.prefix+row.value.start++, ...values,angle:0,locked:false,space_id:null};
  layout.value.items.push(item);select(item);
}
function toggleTracing() { tracing.value = !tracing.value; controller?.setTracing(tracing.value); }
function notify(text, error = false) { message.value = text; isError.value = error; }
function errorText(e) { const detail = e.response?.data?.detail; return typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(d => d.msg).join('; ') : e.message || 'Operation failed'; }
function checkpoint() { undoStack.value.push(JSON.stringify(layout.value)); if (undoStack.value.length > 40) undoStack.value.shift(); }
function undo() { if (undoStack.value.length) { layout.value = JSON.parse(undoStack.value.pop()); if (!selected.value) selectedId.value = null; } }
function select(item) { selectedId.value = item.id; tab.value = 'object'; }
function linkedElsewhere(id) { return layout.value.items.some(i => i.id !== selectedId.value && i.space_id === id); }
function change(key, value) { if (!selected.value || (selected.value.locked && ['x','y','width','height','angle'].includes(key))) return; checkpoint(); selected.value[key] = value; }
function changeNumber(key, event, min, max) { const value = Number(event.target.value); if (!Number.isFinite(value)) return; change(key, Math.max(min,Math.min(max,value))); }
function resize(key,event) { const value = Number(event.target.value); if (!Number.isFinite(value)) return; const size = Math.round(Math.max(200,Math.min(100000,value))); if (layout.value.items.some(i=>i.locked && i[key === 'width' ? 'x' : 'y'] > size)) return notify('Unlock items outside the new canvas before shrinking it.',true); checkpoint(); layout.value[key] = size; for (const item of layout.value.items) { item.x = Math.min(item.x,layout.value.width); item.y = Math.min(item.y,layout.value.height); } nextTick(() => controller?.fit()); }
function add(kind) {
  if (layout.value.items.length >= 1000) return notify('A layout can contain up to 1,000 objects.',true);
  checkpoint();
  const sizes = {stall:[82,120],road:[400,60],label:[220,35],entry:[90,90]};
  const names = {stall:'New space',road:'Drive aisle',label:'Lot label',entry:'Entrance'};
  const [width,height] = sizes[kind];
  const item = {id:crypto.randomUUID(),kind,name:names[kind],x:layout.value.width/2,y:layout.value.height/2,width,height,angle:0,space_id:null};
  layout.value.items.push(item);select(item);
}
function copyObject() { if (!selected.value) return; copiedObject.value = {...selected.value}; notify('Object copied. Paste creates an unlinked copy.'); }
function insertCopy(source, x, y) { const item = {...source,id:crypto.randomUUID(),x,y,space_id:null,locked:false}; layout.value.items.push(item); return item; }
function pasteObject() {
  if (!copiedObject.value || !layout.value) return;
  if (layout.value.items.length >= 1000) return notify('A layout can contain up to 1,000 objects.',true);
  checkpoint(); const source = copiedObject.value;
  const item = insertCopy(source,Math.min(layout.value.width,source.x+20),Math.min(layout.value.height,source.y+20));
  copiedObject.value = {...item}; select(item); notify('Copy pasted. Link it to a camera space when ready.');
}
function duplicate() { if (!selected.value) return; copiedObject.value = {...selected.value}; pasteObject(); }
function repeatObjects(axis) {
  if (!selected.value) return;
  const count = Number(repeatCount.value), gap = Number(repeatGap.value);
  if (!Number.isInteger(count) || count < 1 || count > 100 || !Number.isFinite(gap) || gap < 0 || gap > 1000) return notify('Choose 1–100 copies and a gap from 0–1,000.',true);
  const source = {...selected.value}, radians = source.angle * Math.PI / 180;
  const spacing = (axis === 'x' ? source.width : source.height) + gap;
  const dx = axis === 'x' ? Math.cos(radians)*spacing : -Math.sin(radians)*spacing;
  const dy = axis === 'x' ? Math.sin(radians)*spacing : Math.cos(radians)*spacing;
  const positions = Array.from({length:count},(_,i)=>({x:source.x+dx*(i+1),y:source.y+dy*(i+1)}));
  if (layout.value.items.length+count > 1000 || positions.some(p=>p.x<0||p.y<0||p.x>layout.value.width||p.y>layout.value.height)) return notify('The copies do not fit. Reduce the count or gap, or enlarge the canvas.',true);
  checkpoint(); let last;
  for (const p of positions) last = insertCopy(source,Math.round(p.x),Math.round(p.y));
  select(last); notify(`${count} unlinked copies added. Undo removes the entire group.`);
}
function useSelectedForRow() {
  if (!selected.value) return;
  for (const key of ['x','y','width','height','angle']) row.value[key] = selected.value[key];
}
function buildRow() {
  const r = row.value;
  if (!Number.isInteger(r.count) || r.count < 1 || r.count > 1000 || !Number.isInteger(r.start) || r.start < 1 || r.start > 1000000 ||
      !['x','y','width','height','angle','gap'].every(k=>Number.isFinite(r[k])) || r.width < 0.25 || r.width > 20000 || r.height < 0.25 || r.height > 20000 || r.gap < 0 || r.gap > 1000 || Math.abs(r.angle) > 360)
    return notify('Enter valid count, dimensions, gap, position, rotation, and starting number.',true);
  if (layout.value.items.length + r.count > 1000) return notify('A layout can contain up to 1,000 objects.',true);
  const horizontal = ['left','right'].includes(r.direction), sign = ['left','up'].includes(r.direction) ? -1 : 1;
  const radians = r.angle * Math.PI / 180, spacing = (horizontal ? r.width : r.height) + r.gap;
  const dx = sign * spacing * (horizontal ? Math.cos(radians) : -Math.sin(radians));
  const dy = sign * spacing * (horizontal ? Math.sin(radians) : Math.cos(radians));
  const halfWidth = (Math.abs(Math.cos(radians))*r.width + Math.abs(Math.sin(radians))*r.height)/2;
  const halfHeight = (Math.abs(Math.sin(radians))*r.width + Math.abs(Math.cos(radians))*r.height)/2;
  const positions = Array.from({length:r.count},(_,i)=>({x:r.x+dx*i,y:r.y+dy*i}));
  if (positions.some(p=>p.x-halfWidth < -1e-8 || p.y-halfHeight < -1e-8 || p.x+halfWidth > layout.value.width+1e-8 || p.y+halfHeight > layout.value.height+1e-8))
    return notify('The row does not fit on the canvas. Adjust its start, count, gap, or canvas size.',true);
  checkpoint();
  const items = positions.map((p,i)=>({id:crypto.randomUUID(),kind:'stall',name:r.prefix+(r.start+i), ...p,width:r.width,height:r.height,angle:r.angle,space_id:null,locked:false}));
  layout.value.items.push(...items);select(items[0]);row.value.start += r.count;
  notify(r.count+' unlinked parking spaces added. Undo removes the entire row.');
}
function remove() { if (!selected.value || selected.value.locked) return; checkpoint();layout.value.items = layout.value.items.filter(i=>i.id!==selectedId.value);selectedId.value=null; }
function keyboard(event) {
  if (event.key === 'Escape' && drawing.value) { event.preventDefault(); toggleDrawing(); return; }
  if (event.key === 'Escape' && expanded.value) { event.preventDefault(); toggleExpanded(); return; }
  if (!layout.value || busy.value || event.target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])') || event.isComposing) return;
  const command = event.ctrlKey || event.metaKey, key = event.key.toLowerCase();
  if (command && !event.altKey) {
    const action = {c:selected.value && copyObject,v:copiedObject.value && pasteObject,d:selected.value && duplicate,z:undoStack.value.length && undo}[key];
    if (action && !event.shiftKey) {event.preventDefault();action();} return;
  }
  if (!selected.value) return;
  if (event.key === 'Delete' || event.key === 'Backspace') {event.preventDefault();remove();return;}
  if (event.key === 'Escape') {selectedId.value=null;return;}
  if (selected.value.locked) return;
  const direction = {ArrowLeft:['x',-1],ArrowRight:['x',1],ArrowUp:['y',-1],ArrowDown:['y',1]}[event.key];
  if (!direction) return;
  event.preventDefault();const [axis,sign] = direction, step = event.shiftKey ? 10 : 1;
  const property = event.altKey ? axis === 'x' ? 'width' : 'height' : axis;
  const min = event.altKey ? 0.25 : 0, max = event.altKey ? 20000 : axis === 'x' ? layout.value.width : layout.value.height;
  const value = Math.max(min,Math.min(max,selected.value[property]+sign*step));
  if (value !== selected.value[property]) {if (!event.repeat) checkpoint();selected.value[property]=value;}
}
async function refresh() {
  if (refreshInFlight) return; refreshInFlight=true;
  try { const {data} = await axios.get('/api/dashboard/full'); if (!disposed) {cameras.value=data;states.value=statesFromCameras(data);} }
  catch { if (!disposed) states.value={}; }
  finally {refreshInFlight=false;}
}
async function save() {
  busy.value=true;
  try {const {data}=await axios.put('/api/parking-layout',layout.value,{params:lotParams()});layout.value=data.layout;baseline.value=JSON.stringify(data.layout);notify('Layout saved. This parking lot now uses this arrangement.');return true;}
  catch(e){notify(errorText(e),true);return false;}finally{busy.value=false;}
}
function downloadJson(){downloadFile('parking-layout.json',JSON.stringify(layout.value,null,2),'application/json');notify('Editable layout exported.');}
function downloadSnapshot(){downloadFile('parking-map.html',exportViewer(layout.value,states.value),'text/html');notify('Offline viewer exported. Open the HTML file in any browser.');}
async function exportLive(){
  try {
    const base = new URL(backendUrl.value); if (!['http:','https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash) throw new Error('Enter a plain HTTP or HTTPS backend URL.');
    backendUrl.value = base.href.replace(/\/$/,'');
    if (!await save()) return;
    const {data}=await axios.post('/api/parking-layout/share',null,{params:lotParams()});viewKey.value=data.view_key;
    const feed=`${backendUrl.value}/api/parking-layout/view/${viewKey.value}`;
    downloadFile('parking-map-live.html',exportViewer(layout.value,{},feed),'text/html');notify('Live viewer exported. Your read-only embed code is ready below.');
  }catch(e){notify(errorText(e),true);}
}
async function revoke(){try{await axios.delete('/api/parking-layout/share',{params:lotParams()});viewKey.value='';notify('Existing live viewer links for this lot have been disabled.');}catch(e){notify(errorText(e),true);}}
async function matchImageCanvas() {
  try {
    const image = await new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=reject;img.src=layout.value.background;});
    const width=Math.max(200,image.naturalWidth),height=Math.max(200,image.naturalHeight);
    if(width>100000||height>100000)return notify('Image dimensions exceed the canvas limit.',true);
    if(layout.value.items.some(i=>i.locked))return notify('Unlock items before scaling the layout to the image.',true);
    const sx=width/layout.value.width,sy=height/layout.value.height;
    if(layout.value.items.some(i=>i.width*sx>20000||i.height*sy>20000||i.width*sx<0.25||i.height*sy<0.25))return notify('Scaling would put some slot sizes outside the supported range.',true);
    checkpoint();for(const item of layout.value.items){item.x*=sx;item.y*=sy;item.width*=sx;item.height*=sy;}
    layout.value.width=width;layout.value.height=height;await nextTick();controller?.fit();notify('Canvas matched to image dimensions. Existing objects scaled with it.');
  }catch{notify('Unable to read image dimensions.',true);}
}
async function uploadBackground(event){
  const file=event.target.files?.[0];event.target.value='';if(!file)return;
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>15_000_000)return notify('Choose a PNG, JPEG, or WebP image smaller than 15 MB.',true);
  try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});checkpoint();layout.value.background=data;notify('Background added. Save the layout to keep it.');}catch{notify('Unable to read background.',true);}
}
async function importJson(event){
  const file=event.target.files?.[0];event.target.value='';if(!file)return;
  try{
    if(file.size>22_000_000)throw new Error('Layout file is too large.');
    const value=JSON.parse(await file.text());
    if(value.version!==1||typeof value.name!=='string'||!value.name.trim()||value.name.length>80||!Number.isInteger(value.width)||!Number.isInteger(value.height)||value.width<200||value.width>100000||value.height<200||value.height>100000||!Array.isArray(value.items)||value.items.length>1000||typeof value.background!=='string')throw new Error('Invalid parking layout file.');
    if(value.background && (!/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value.background)||value.background.length>21_000_000))throw new Error('Invalid background image.');
    const ids=new Set(),links=new Set();
    for(const item of value.items){
      if(typeof item.id!=='string'||!item.id||item.id.length>80||ids.has(item.id)||!['stall','road','label','entry'].includes(item.kind)||typeof item.name!=='string'||item.name.length>80||!['x','y','width','height','angle'].every(k=>typeof item[k]==='number'&&Number.isFinite(item[k]))||item.x<0||item.x>value.width||item.y<0||item.y>value.height||item.width<0.25||item.width>20000||item.height<0.25||item.height>20000||Math.abs(item.angle)>360)throw new Error('Invalid layout object.');
      if(item.locked !== undefined && typeof item.locked !== 'boolean')throw new Error('Invalid lock setting.');
      ids.add(item.id);
      if(item.space_id!==null&&(!Number.isInteger(item.space_id)||item.space_id<1||item.kind!=='stall'||links.has(item.space_id)))throw new Error('Invalid or duplicate camera-space link.');
      if(item.space_id)links.add(item.space_id);
    }
    checkpoint();layout.value=value;selectedId.value=null;await nextTick();controller?.fit();notify('Layout imported. Check camera-space links, then save.');
  }catch(e){notify(e.message || 'Unable to import layout.',true);}
}
watch([layout,states,selectedId],()=>controller?.update(layout.value,states.value,selectedId.value),{deep:true});
function beforeUnload(e){if(dirty.value){e.preventDefault();e.returnValue='';}}
onBeforeRouteLeave(()=>!dirty.value||window.confirm('Leave without saving your layout changes?'));
async function chooseLot(event) {
  const choice=event.target.value;
  if(dirty.value && !window.confirm('Switch parking lots without saving your changes?')) {event.target.value=selectedLot.value;return;}
  await loadLot(choice);
}
async function loadLot(choice) {
  if (!lotOptions.value.some(l=>l.id===choice)) return;
  busy.value=true;selectedLot.value=choice;loadFailed.value=false;
  controller?.destroy();controller=null;observer?.disconnect();observer=null;
  layout.value=null;selectedId.value=null;drawing.value=false;undoStack.value=[];copiedObject.value=null;viewKey.value='';message.value='';
  try {
    const {data}=await axios.get('/api/parking-layout',{params:lotParams()});if(disposed)return;
    layout.value=data.layout||defaultLayout(lotCameras.value);
    if(!data.layout)layout.value.name=lotOptions.value.find(l=>l.id===choice).name.split(' · ')[0];
    baseline.value=data.layout?JSON.stringify(layout.value):'';
    await nextTick();if(disposed)return;
    controller=createParkingMap(L,mapElement.value,layout.value,states.value,{select,transform:transformItem,draw:drawSpace,move(item,x,y){if(item.locked)return;checkpoint();item.x=Math.min(layout.value.width,Math.round(x*100)/100);item.y=Math.min(layout.value.height,Math.round(y*100)/100);}});
    controller.setTracing?.(tracing.value);
    observer=new ResizeObserver(()=>controller?.map.invalidateSize());observer.observe(mapElement.value);
  }catch(e){loadFailed.value=true;notify(errorText(e),true);}finally{busy.value=false;}
}
onMounted(async()=>{
  installParkingStyle();
  window.addEventListener('keydown',keyboard);
  try{
    const [groupList,live]=await Promise.all([axios.get('/api/camera-groups'),axios.get('/api/dashboard/full')]);
    if(disposed)return;
    groups.value=groupList.data||[];cameras.value=live.data;states.value=statesFromCameras(live.data);lotsLoading.value=false;
    if(typeof route.query.lot==='string')await loadLot(route.query.lot);
    timer=setInterval(refresh,10000);window.addEventListener('beforeunload',beforeUnload);
  }catch(e){loadFailed.value=true;notify(errorText(e),true);}finally{lotsLoading.value=false;}
});
onUnmounted(()=>{disposed=true;clearInterval(timer);observer?.disconnect();controller?.destroy();window.removeEventListener('beforeunload',beforeUnload);window.removeEventListener('keydown',keyboard);});
</script>

<style>
/* Styles also shared with the self-contained HTML viewer. */
@import 'leaflet/dist/leaflet.css';
</style>
<style scoped>
.lot-selector{display:flex;align-items:center;gap:16px;font-size:14px;font-weight:700;margin:16px 0 8px}.lot-selector select{padding:10px 14px;min-width:240px;max-width:100%;border:1px solid #b9b09b;background:#f5efe1;color:#39372b;font:inherit}
.layout-editor{padding:28px;max-width:1800px;margin:auto;color:#263d31}.editor-heading{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:24px;flex-wrap:wrap}.eyebrow{font-size:10px;letter-spacing:2px;color:#718474;font-weight:700}.editor-heading h1{font-size:30px;letter-spacing:-1px;margin:4px 0}.editor-heading p{font-size:14px;color:#758276}.heading-actions{display:flex;align-items:center;gap:18px}.heading-actions a{font-size:13px;color:#3c6d55}.layout-editor button,.file-button{border:1px solid #d5ded5;background:#fff;border-radius:8px;padding:9px 13px;font:inherit;font-size:12px;color:#385143;cursor:pointer;display:inline-block}.layout-editor button:hover,.file-button:hover{background:#edf3eb}.layout-editor button:disabled{opacity:.45;cursor:default}.layout-editor button.primary{background:#255a41;color:#fff;border-color:#255a41}.editor-toolbar{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:14px;background:#fff;border:1px solid #dce3d9;border-radius:12px 12px 0 0}.editor-toolbar>div{display:flex;gap:7px;flex-wrap:wrap}.editor-workspace{display:grid;grid-template-columns:minmax(0,1fr) 290px;border:1px solid #dce3d9;border-top:0;border-radius:0 0 12px 12px;overflow:hidden}.map-panel{height:650px;position:relative;min-width:0}.map-panel .parking-map{height:100%;min-height:0}.map-hint{position:absolute;bottom:16px;left:16px;z-index:500;background:#fffffff0;padding:9px 12px;border-radius:7px;color:#657765;font-size:11px;pointer-events:none}.editor-inspector{background:#fff;padding:18px;border-left:1px solid #dce3d9;height:650px;overflow-y:auto}.inspector-tabs{display:flex;gap:5px;margin-bottom:20px}.inspector-tabs button{flex:1;padding:8px;font-size:11px}.layout-editor .active{background:#e1eddf;border-color:#9abd9b}.editor-inspector h2{font-size:17px;text-transform:capitalize;margin-bottom:18px}.editor-inspector h3{font-size:13px;margin-bottom:12px}.editor-inspector h3 span{float:right;color:#839185}.editor-inspector label{display:block;font-size:11px;color:#728171;margin:12px 0}.editor-inspector input,.editor-inspector select,.editor-inspector textarea{display:block;margin-top:5px;width:100%;padding:9px;border:1px solid #d8e0d6;border-radius:7px;color:#304936;font:inherit;font-size:12px;background:#fafcf8}.editor-inspector textarea{resize:vertical;font-size:10px}.input-pair{display:grid;grid-template-columns:1fr 1fr;gap:10px}.input-pair label{min-width:0}.inspector-actions{display:flex;gap:8px;margin-top:18px}.layout-editor button.danger{color:#a64432}.helper{font-size:11px;line-height:1.6;color:#7d897b;margin:12px 0}.editor-inspector hr{border:0;border-top:1px solid #e6ebe2;margin:22px 0}.selection-empty{padding:24px 4px}.selection-empty>span{font-size:30px;color:#a7b8a1}.selection-empty h2{margin:8px 0}.selection-empty p{font-size:12px;color:#7b8a76;line-height:1.6}.object-list{display:flex;flex-direction:column;gap:4px;max-height:250px;overflow:auto}.object-list button{text-align:left;border-color:transparent;flex-shrink:0}.object-list button span{margin-right:10px;color:#7c9074}.file-button input{display:none}.export-buttons{display:flex;flex-direction:column;gap:8px}.full-width{width:100%}.editor-message{padding:12px 16px;background:#e5f2e3;border-radius:8px;margin-bottom:15px;font-size:13px}.editor-message.error{background:#f8e6df;color:#9c4433}.editor-footer{display:flex;justify-content:space-between;gap:10px;font-size:11px;color:#7c8a77;margin-top:14px;flex-wrap:wrap}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin:0 5px 0 12px}.dot:first-child{margin-left:0}.dot.free{background:#70ad84}.dot.busy{background:#d18c78}.dot.unknown{background:#a4afb1}.editor-loading{padding:80px;text-align:center;color:#7c8a77}
.layout-editor.expanded{position:fixed;inset:0;z-index:2000;max-width:none;overflow:auto;background:#f5f3ed;padding:12px;display:flex;flex-direction:column}.expanded .editor-heading,.expanded .lot-selector,.expanded>.helper{display:none}.expanded .editor-workspace{flex:1;min-height:0}.expanded .map-panel,.expanded .editor-inspector{height:100%;min-height:0}.expanded .editor-toolbar{flex-shrink:0}.expanded .editor-message{margin-bottom:6px}.expanded .editor-footer{padding:8px}.expanded .editor-inspector{max-height:none}
@media(max-width:900px){.layout-editor{padding:16px}.editor-workspace{grid-template-columns:1fr}.expanded .editor-workspace{grid-template-columns:minmax(0,1fr) 240px}.editor-inspector{height:auto;max-height:600px;border-left:0;border-top:1px solid #dce3d9}.map-panel{height:480px}.editor-heading h1{font-size:25px}.map-hint{font-size:9px;right:12px}}
</style>
