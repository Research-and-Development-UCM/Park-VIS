export const formatSize = (bytes) => {
  if (bytes == null || isNaN(bytes) || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

export const parseTimestamp = (ts) => {
  if (!ts) return null;
  if (ts instanceof Date) return ts;
  let s = String(ts).trim();
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(s)) {
    s = s.replace(' ', 'T');
  }
  const hasTz = s.includes('Z') || /[T\s]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?([+-]\d{2}(?::?\d{2})?)$/.test(s);
  return new Date(hasTz ? s : s + 'Z');
};


