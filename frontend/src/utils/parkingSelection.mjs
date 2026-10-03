export function selectItemIds(ids, id, extend = false) {
  if (!extend) return [id];
  return ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id];
}
export function groupMovement(items, dx, dy, width, height) {
  const movable = items.filter(item => !item.locked);
  if (!movable.length) return [];
  const x = Math.max(-Math.min(...movable.map(i=>i.x)), Math.min(width-Math.max(...movable.map(i=>i.x)),dx));
  const y = Math.max(-Math.min(...movable.map(i=>i.y)), Math.min(height-Math.max(...movable.map(i=>i.y)),dy));
  return movable.map(item=>({item,x:item.x+x,y:item.y+y}));
}

export function itemsInsideBox(items, bounds) {
  return items.filter(item=>{
    const angle=item.angle*Math.PI/180;
    const halfWidth=(Math.abs(Math.cos(angle))*item.width+Math.abs(Math.sin(angle))*item.height)/2;
    const halfHeight=(Math.abs(Math.sin(angle))*item.width+Math.abs(Math.cos(angle))*item.height)/2;
    return item.x-halfWidth>=bounds.left && item.x+halfWidth<=bounds.right && item.y-halfHeight>=bounds.top && item.y+halfHeight<=bounds.bottom;
  });
}
export function pasteSelection(items,width,height,idFactory=()=>crypto.randomUUID()) {
  const sources=items.map(item=>({...item,locked:false}));
  return groupMovement(sources,20,20,width,height).map(({item,x,y})=>({...item,id:idFactory(),x,y,space_id:null,locked:false}));
}
