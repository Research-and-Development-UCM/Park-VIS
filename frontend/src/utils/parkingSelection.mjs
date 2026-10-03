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
