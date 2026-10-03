import leafletScript from 'leaflet/dist/leaflet.js?raw';
import leafletStyle from 'leaflet/dist/leaflet.css?raw';

export function installParkingStyle() {
  if (document.querySelector('style[data-parking-map]')) return;
  const style = document.createElement('style');
  style.dataset.parkingMap = 'true'; style.textContent = parkingMapStyle;
  document.head.appendChild(style);
}

export function defaultLayout(cameras) {
  const items = [];
  let row = 0;
  for (const cam of cameras) {
    items.push({ id: crypto.randomUUID(), kind: 'label', name: cam.name, x: 580, y: 55 + row * 230, width: 300, height: 30, angle: 0, space_id: null });
    const spaces = [...cam.spaces].sort((a,b) => a.name.localeCompare(b.name, undefined, {numeric:true}));
    for (let start = 0; start < spaces.length; start += 10) {
      spaces.slice(start, start + 10).forEach((s,index) => items.push({
        id: crypto.randomUUID(), kind: 'stall', name: s.name, space_id: s.id,
        x: 95 + index * 110, y: 135 + row * 230, width: 82, height: 120, angle: 0,
      }));
      items.push({ id: crypto.randomUUID(), kind: 'road', name: 'Drive aisle', x: 590, y: 230 + row * 230, width: 1080, height: 48, angle: 0, space_id: null });
      row++;
    }
    if (!spaces.length) row++;
  }
  return { version: 1, name: 'My parking lot', width: 1200, height: Math.max(800, row * 230 + 90), background: '', items };
}

export function statesFromCameras(cameras) {
  const states = {};
  for (const cam of cameras) for (const space of cam.spaces) {
    const info = cam.occupancy?.[space.id];
    states[space.id] = { occupied: info?.occupied ?? null, updated_at: cam.metadata?.timestamp ?? null };
  }
  return states;
}

// This function is also included in the standalone HTML export.
export function createParkingMap(L, element, initialLayout, initialStates = {}, callbacks = {}) {
  const map = L.map(element, { crs: L.CRS.Simple, minZoom: -12, maxZoom: 5, attributionControl: false, boxZoom: !callbacks.move, keyboard: !callbacks.move });
  let layout = initialLayout, states = initialStates, selected = [], tracing = false, drawing = false, editing = false, backgroundVisible = true;
  const layers = L.layerGroup().addTo(map);
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function status(item) {
    const info = states[item.space_id];
    if (!info || typeof info.occupied !== 'boolean' || !info.updated_at) return 'unknown';
    const raw = String(info.updated_at);
    const stamp = Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(raw) ? raw : raw + 'Z');
    if (!Number.isFinite(stamp) || Date.now() - stamp > 300000) return 'unknown';
    return info.occupied ? 'occupied' : 'available';
  }
  function icon(item) {
    const scale = Math.pow(2, map.getZoom());
    const width = item.width * scale, height = item.height * scale;
    const state = status(item);
    const car = '<svg viewBox="0 0 32 56" aria-hidden="true"><rect x="3" y="2" width="26" height="52" rx="8" fill="currentColor"/><path d="M7 15h18l-2-7H9zM7 39h18l-2 9H9z" fill="#fff" opacity=".6"/></svg>';
    const html = `<div class="parking-object ${item.kind} ${state} ${selected.includes(item.id) ? 'selected' : ''} ${tracing && backgroundVisible && layout.background ? 'tracing' : ''}" style="width:${width}px;height:${height}px;transform:rotate(${item.angle}deg);font-size:${Math.max(7,Math.min(12 * scale,width/4,height/3))}px" role="button" aria-label="${escape(item.name)}${item.kind === 'stall' ? ': ' + state : ''}"><span class="object-name">${escape(item.name)}</span>${item.kind === 'stall' ? (state === 'occupied' ? car : `<b class="parking-symbol">${state === 'unknown' ? '?' : 'P'}</b>`) + `<small>${state === 'unknown' ? 'No recent data' : state}</small>` : item.kind === 'entry' ? '<b class="entry-arrow">↑</b>' : ''}</div>`;
    return L.divIcon({ className: 'parking-object-anchor', html, iconSize: [width,height], iconAnchor: [width/2,height/2] });
  }
  function render() {
    if (editing) return;
    layers.clearLayers();
    if (layout.background && backgroundVisible) L.imageOverlay(layout.background, [[0,0],[layout.height,layout.width]], {opacity:tracing ? 1 : .65}).addTo(layers);
    for (const item of layout.items) {
      const marker = L.marker([layout.height - item.y, item.x], {
        icon: icon(item), draggable: Boolean(callbacks.move) && !item.locked && !drawing, keyboard: true,
        title: item.name, zIndexOffset: item.kind === 'road' ? -10000 : 1000,
      }).addTo(layers);
      marker.on('dragstart', () => { editing = true; });
      marker.on('click', event => {
        if (callbacks.select) callbacks.select(item,event);
        else marker.bindPopup(`${escape(item.name)}${item.kind === 'stall' ? ': ' + status(item) : ''}`).openPopup();
      });
      marker.on('dragend', () => {
        editing = false;
        if (item.locked) return;
        const pos = marker.getLatLng();
        callbacks.move?.(item, Math.max(0,Math.min(layout.width,pos.lng)), Math.max(0,Math.min(layout.height,layout.height-pos.lat)));
        render();
      });
      if (selected.length === 1 && selected.includes(item.id) && !item.locked && !drawing && callbacks.transform) addHandles(item, marker);
    }
  }

  function addHandles(item, marker) {
    const source = {...item}, radians = source.angle * Math.PI / 180;
    const cos = Math.cos(radians), sin = Math.sin(radians);
    const toPoint = (x,y) => [layout.height-(source.y+x*sin+y*cos),source.x+x*cos-y*sin];
    const handles = [];
    function reposition(draft) {
      for (const h of handles) {
        const y = h.rotate ? -draft.height/2-24/Math.pow(2,map.getZoom()) : h.sy*draft.height/2;
        const x = h.rotate ? 0 : h.sx*draft.width/2;
        const angle = draft.angle*Math.PI/180;
        h.marker.setLatLng([layout.height-(draft.y+x*Math.sin(angle)+y*Math.cos(angle)),draft.x+x*Math.cos(angle)-y*Math.sin(angle)]);
      }
    }
    for (const [sx,sy,rotate] of [[-1,-1,false],[1,-1,false],[1,1,false],[-1,1,false],[0,-1,true]]) {
      const point=toPoint(sx*source.width/2,rotate ? -source.height/2-24/Math.pow(2,map.getZoom()) : sy*source.height/2);
      const handle=L.marker(point,{draggable:true,keyboard:false,zIndexOffset:20000,icon:L.divIcon({className:'parking-edit-handle',html:rotate?'↻':'',iconSize:[16,16],iconAnchor:[8,8]}),title:rotate?'Drag to rotate':'Drag to resize'}).addTo(layers);
      let draft={...source};
      handles.push({marker:handle,sx,sy,rotate});
      handle.on('dragstart',()=>{editing=true;});
      handle.on('drag',()=>{
        const pos=handle.getLatLng(), dx=pos.lng-source.x,dy=layout.height-pos.lat-source.y;
        if(rotate) draft.angle=Math.atan2(dy,dx)*180/Math.PI+90;
        else {
          draft.width=Math.max(0.25,Math.min(20000,2*Math.abs(dx*cos+dy*sin)));
          draft.height=Math.max(0.25,Math.min(20000,2*Math.abs(-dx*sin+dy*cos)));
        }
        marker.setIcon(icon(draft));reposition(draft);
      });
      handle.on('dragend',()=>{editing=false;callbacks.transform(item,{width:draft.width,height:draft.height,angle:draft.angle});render();});
    }
  }
  let drawStart=null,drawPreview=null;
  const canvasPoint = latlng => ({x:Math.max(0,Math.min(layout.width,latlng.lng)),y:Math.max(0,Math.min(layout.height,layout.height-latlng.lat))});
  function drawMove(event) {
    if(!drawStart)return;
    const end=canvasPoint(map.mouseEventToLatLng(event));
    drawPreview.setBounds([[layout.height-drawStart.y,drawStart.x],[layout.height-end.y,end.x]]);
  }
  function cancelDraw() {
    document.removeEventListener('mousemove',drawMove);document.removeEventListener('mouseup',drawEnd);
    if(drawPreview)map.removeLayer(drawPreview);drawPreview=null;drawStart=null;editing=false;
  }
  function drawEnd(event) {
    if(!drawStart)return;
    const start=drawStart,end=canvasPoint(map.mouseEventToLatLng(event));cancelDraw();
    const width=Math.abs(end.x-start.x),height=Math.abs(end.y-start.y);
    if(width>=0.25&&height>=0.25&&width<=20000&&height<=20000)callbacks.draw?.({x:(start.x+end.x)/2,y:(start.y+end.y)/2,width,height});
    render();
  }
  map.on('mousedown',event=>{
    if(!drawing || event.originalEvent.button!==0 || event.originalEvent.target.closest('.leaflet-marker-icon,.leaflet-control'))return;
    drawStart=canvasPoint(event.latlng);editing=true;
    drawPreview=L.rectangle([event.latlng,event.latlng],{color:'#ffe66b',weight:2,fillOpacity:.08,interactive:false}).addTo(map);
    document.addEventListener('mousemove',drawMove);document.addEventListener('mouseup',drawEnd);
    event.originalEvent.preventDefault();
  });

  map.on('zoomend', render);
  function fit() { map.invalidateSize(); map.fitBounds([[0,0],[layout.height,layout.width]], {padding:[30,30]}); }
  fit(); render();
  return {
    map, fit, setBackgroundVisible(value) { backgroundVisible = Boolean(value); render(); }, setTracing(value) { tracing = Boolean(value); render(); }, setDrawing(value) { cancelDraw();drawing=Boolean(value)&&Boolean(callbacks.draw);if(drawing)map.dragging.disable();else map.dragging.enable();element.classList.toggle('drawing-spaces',drawing);render(); }, destroy: () => {cancelDraw();map.remove();}, status,
    update(nextLayout, nextStates = states, selectedId = null) { layout = nextLayout; states = nextStates; selected = Array.isArray(selectedId) ? selectedId : selectedId ? [selectedId] : []; render(); },
  };
}

export const parkingMapStyle = `
.parking-map{height:100%;min-height:420px;background-color:#eae4d5;background-image:linear-gradient(#c9beaa55 1px,transparent 1px),linear-gradient(90deg,#c9beaa55 1px,transparent 1px);background-size:24px 24px}
.parking-object.stall.tracing{background:transparent;border-color:#ffe66b;color:#fff;text-shadow:0 1px 3px #000}.parking-object.stall.tracing .parking-symbol,.parking-object.stall.tracing small,.parking-object.stall.tracing svg{display:none}
.parking-edit-handle{background:#fff;border:2px solid #276bd3;border-radius:50%;text-align:center;font-size:12px;color:#276bd3;cursor:grab;box-shadow:0 1px 4px #0005}.drawing-spaces{cursor:crosshair}
.parking-object-anchor{background:none;border:0}
.parking-object{box-sizing:border-box;display:flex;flex-direction:column;align-items:center;justify-content:space-between;gap:3px;padding:5%;color:#59613b;text-align:center;transform-origin:center;overflow:hidden;cursor:pointer;font-family:Courier New,monospace}
.parking-object.stall{background:#dce1c4;border:2px solid #8e9a63;border-bottom-width:5px;border-radius:7px 7px 1px 1px}
.parking-object.occupied{background:#edd9c6;border-color:#b87e58;color:#a05c36}
.parking-object.unknown{background:#e3dfd4;border-color:#b1a993;color:#817b68}
.parking-object.selected{outline:3px solid #276bd3;outline-offset:3px}
.parking-object svg{height:52%;max-width:40%;flex-shrink:1}.object-name{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700}
.parking-object small{text-transform:capitalize;font-size:.8em}.parking-symbol{font-size:2em;opacity:.5;line-height:1}
.parking-object.road{background:#d8d0bb;border-top:2px dashed #f6f0e1;border-bottom:2px dashed #f6f0e1;border-radius:4px;justify-content:center;color:#827758}
.parking-object.label{justify-content:center;color:#514d3c;padding:0}.parking-object.entry{color:#6c7350;justify-content:center}.entry-arrow{font-size:2em}
`;

export function exportViewer(layout, states = {}, feedUrl = '') {
  const json = value => JSON.stringify(value).replace(/</g, '\\u003c');
  const script = leafletScript.replace(/<\/script/gi, '<\\/script');
  const style = (leafletStyle + parkingMapStyle).replace(/<\/style/gi, '<\\/style');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>Parking Vis — Availability</title><style>${style}
html,body{height:100%;margin:0;font-family:Courier New,monospace;color:#253c30}body{display:flex;flex-direction:column}header{padding:18px 24px;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;background:#faf6ed}h1{font-size:24px;font-family:Georgia,serif;margin:0}p{font-size:12px;color:#697b70;margin:5px 0 0}#map{flex:1;min-height:250px}#summary{font-size:13px}.legend{font-size:12px;padding:10px 24px;background:#faf6ed;color:#5f7065}
</style></head><body><header><div><h1 id="title"></h1><p id="connection"></p></div><span id="summary"></span></header><div id="map" class="parking-map"></div><div class="legend">Green: available · Red: occupied · Gray: no recent data</div><script>${script}</script><script>
const makeMap=${createParkingMap.toString()}; let layout=${json(layout)},states=${json(states)}; const feed=${feedUrl === '__PUBLIC_VIEW__' ? `(function(){const key=new URLSearchParams(location.search).get('view')||location.pathname.split('/').pop();return /^[A-Za-z0-9_-]{40,80}$/.test(key)?location.origin+'/api/parking-layout/view/'+key:''})()` : json(feedUrl)};
const viewer=makeMap(L,document.getElementById('map'),layout,states);
function show(){document.getElementById('title').textContent=layout.name;const spaces=layout.items.filter(i=>i.kind==='stall');const available=spaces.filter(i=>viewer.status(i)==='available').length;const unknown=spaces.filter(i=>viewer.status(i)==='unknown').length;document.getElementById('summary').textContent=available+' available / '+spaces.length+' spaces'+(unknown?' · '+unknown+' unknown':'');}
document.getElementById('connection').textContent=feed?'Connecting to live occupancy…':'Offline layout · saved occupancy snapshot';show();
let inFlight=false;async function poll(){if(!feed||inFlight)return;inFlight=true;try{const r=await fetch(feed,{credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});if(!r.ok)throw new Error();const data=await r.json();layout=data.layout;states=data.states;viewer.update(layout,states);show();document.getElementById('connection').textContent='Live · updated '+new Date().toLocaleTimeString();}catch{viewer.update(layout,{});show();document.getElementById('connection').textContent='Live connection unavailable · check the backend URL';}finally{inFlight=false}}
poll();setInterval(()=>{if(feed)poll();else{viewer.update(layout,states);show()}},10000);window.addEventListener('resize',()=>viewer.map.invalidateSize());
</script></body></html>`;
}

export function downloadFile(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement('a'); a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
