const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../public/parking-view.html'),'utf8');
const source=html.slice(html.indexOf('const makeMap=')+14,html.indexOf('; let layout='));
const createMap=Function('return '+source)();
function setup(item,callbacks={}) {
 const markers=[],events={},documentEvents={};
 global.document={addEventListener:(n,f)=>documentEvents[n]=f,removeEventListener:n=>delete documentEvents[n]};
 const map={getZoom:()=>0,invalidateSize(){},fitBounds(){},remove(){},removeLayer(){},on:(n,f)=>events[n]=f,dragging:{disable(){},enable(){}},mouseEventToLatLng:e=>e.latlng};
 const layer={clearLayers:()=>markers.splice(0),addTo(){return this;}};
 const L={CRS:{Simple:{}},map:()=>map,layerGroup:()=>layer,divIcon:o=>o,imageOverlay:()=>({addTo(){}}),rectangle:()=>({addTo(){return this},setBounds(){}}),marker:(pos,options)=>{
   const handlers={};const m={options,pos,handlers,addTo(){markers.push(this);return this},on:(n,f)=>handlers[n]=f,getLatLng:()=>({lat:m.pos[0],lng:m.pos[1]}),setLatLng:p=>m.pos=p,setIcon:i=>m.options.icon=i};return m;
 }};
 const layout={width:100000,height:50000,background:'',items:[item]};
 const viewer=createMap(L,{classList:{toggle(){}}},layout,{},callbacks);return {markers,events,documentEvents,viewer,layout};
}
const item=()=>({id:'a',kind:'stall',name:'P1',x:100,y:100,width:4,height:6,angle:0,space_id:null});
test('locked slots cannot drag or expose transform handles',()=>{
 const i={...item(),locked:true};const s=setup(i,{move(){},transform(){}});s.viewer.update(s.layout,{},'a');assert.equal(s.markers.length,1);assert.equal(s.markers[0].options.draggable,false);
});
test('cursor resize saves fractional dimensions and rotation saves its angle',()=>{
 const edits=[];const s=setup(item(),{move(){},transform:(i,v)=>edits.push(v)});s.viewer.update(s.layout,{},'a');assert.equal(s.markers.length,6);
 let h=s.markers[1];h.handlers.dragstart();h.pos=[49901.5,101.25];h.handlers.drag();h.handlers.dragend();assert.equal(edits[0].width,2.5);assert.equal(edits[0].height,3);
 h=s.markers[5];h.handlers.dragstart();h.pos=[49900,110];h.handlers.drag();h.handlers.dragend();assert.equal(edits[1].angle,90);
});
test('drawing creates a centered small rectangle and cleans up document events',()=>{
 const draws=[];const s=setup(item(),{draw:v=>draws.push(v)});s.viewer.setDrawing(true);assert.equal(s.markers[0].options.draggable,false);
 s.events.mousedown({latlng:{lat:49900,lng:100},originalEvent:{button:0,target:{closest:()=>null},preventDefault(){}}});
 s.documentEvents.mousemove({latlng:{lat:49898,lng:103}});s.documentEvents.mouseup({latlng:{lat:49898,lng:103}});
 assert.deepEqual(draws[0],{x:101.5,y:101,width:3,height:2});assert.equal(Object.keys(s.documentEvents).length,0);
});
test('tiny clicks do not create a space, and destroy cancels drawing',()=>{
 const draws=[];const s=setup(item(),{draw:v=>draws.push(v)});s.viewer.setDrawing(true);
 const down=()=>s.events.mousedown({latlng:{lat:49900,lng:100},originalEvent:{button:0,target:{closest:()=>null},preventDefault(){}}});down();s.documentEvents.mouseup({latlng:{lat:49900,lng:100}});assert.equal(draws.length,0);down();s.viewer.destroy();assert.equal(Object.keys(s.documentEvents).length,0);
});
