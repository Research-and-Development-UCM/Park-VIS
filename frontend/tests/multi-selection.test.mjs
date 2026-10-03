import { test } from 'node:test';
import assert from 'node:assert/strict';
import {selectItemIds,groupMovement} from '../src/utils/parkingSelection.mjs';
test('Shift selection adds and removes IDs, ordinary click selects one',()=>{
  const first=selectItemIds([], 'a');const second=selectItemIds(first,'b',true);assert.deepEqual(second,['a','b']);assert.deepEqual(first,['a']);assert.deepEqual(selectItemIds(second,'a',true),['b']);assert.deepEqual(selectItemIds(second,'c'),['c']);assert.deepEqual(selectItemIds(['a'],'a',true),[]);
});
test('group movement preserves spacing at canvas edges and leaves locked items unchanged',()=>{
  const items=[{x:10,y:10},{x:25,y:20},{x:1,y:1,locked:true}];const moves=groupMovement(items,100,-50,30,30);assert.deepEqual(moves.map(m=>[m.x,m.y]),[[15,0],[30,10]]);assert.equal(moves.length,2);assert.deepEqual(items[2],{x:1,y:1,locked:true});assert.equal(items[0].x,10);
});
test('an entirely locked group does not move',()=>{assert.deepEqual(groupMovement([{x:1,y:1,locked:true}],5,5,100,100),[]);});
