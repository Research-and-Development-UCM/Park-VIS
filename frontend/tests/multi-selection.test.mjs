import { test } from 'node:test';
import assert from 'node:assert/strict';
import {selectItemIds,groupMovement,itemsInsideBox,pasteSelection} from '../src/utils/parkingSelection.mjs';
test('Shift selection adds and removes IDs, ordinary click selects one',()=>{
  const first=selectItemIds([], 'a');const second=selectItemIds(first,'b',true);assert.deepEqual(second,['a','b']);assert.deepEqual(first,['a']);assert.deepEqual(selectItemIds(second,'a',true),['b']);assert.deepEqual(selectItemIds(second,'c'),['c']);assert.deepEqual(selectItemIds(['a'],'a',true),[]);
});
test('group movement preserves spacing at canvas edges and leaves locked items unchanged',()=>{
  const items=[{x:10,y:10},{x:25,y:20},{x:1,y:1,locked:true}];const moves=groupMovement(items,100,-50,30,30);assert.deepEqual(moves.map(m=>[m.x,m.y]),[[15,0],[30,10]]);assert.equal(moves.length,2);assert.deepEqual(items[2],{x:1,y:1,locked:true});assert.equal(items[0].x,10);
});
test('an entirely locked group does not move',()=>{assert.deepEqual(groupMovement([{x:1,y:1,locked:true}],5,5,100,100),[]);});

test('box selection includes fully enclosed rotated items and excludes partial overlaps',()=>{
  const items=[{id:'a',x:10,y:10,width:4,height:6,angle:0},{id:'b',x:19,y:10,width:4,height:4,angle:0},{id:'c',x:10,y:10,width:6,height:4,angle:90}];assert.deepEqual(itemsInsideBox(items,{left:5,right:15,top:5,bottom:15}).map(i=>i.id),['a','c']);
});
test('paste preserves each name, dimensions and spacing with new IDs and unlinked copies',()=>{
  const source=[{id:'a',name:'A12',x:10,y:10,width:3,height:6,angle:90,space_id:1,locked:true},{id:'b',name:'West 5',x:20,y:25,width:4,height:7,angle:0,space_id:2,locked:false}];let n=0;const copies=pasteSelection(source,40,40,()=>String(++n));assert.deepEqual(copies.map(i=>i.name),['A12','West 5']);assert.deepEqual(copies.map(i=>[i.x,i.y]),[[30,25],[40,40]]);assert.equal(copies[0].angle,90);assert.equal(copies[0].width,3);assert.ok(copies.every(i=>i.space_id===null&&!i.locked));assert.equal(source[0].space_id,1);
});
