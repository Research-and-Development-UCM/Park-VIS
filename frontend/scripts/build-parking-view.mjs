import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const { build } = createRequire(require.resolve('vite/package.json'))('esbuild');
const bundle = await build({
  entryPoints: [resolve(root, 'src/utils/parkingMap.js')], bundle: true,
  write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'raw-assets', setup(builder) {
    builder.onResolve({ filter: /\?raw$/ }, args => ({ path: require.resolve(args.path.slice(0,-4)), namespace: 'raw' }));
    builder.onLoad({ filter: /.*/, namespace: 'raw' }, async args => ({ contents: await readFile(args.path, 'utf8'), loader: 'text' }));
  } }],
});
const { exportViewer } = await import('data:text/javascript;base64,' + Buffer.from(bundle.outputFiles[0].text).toString('base64'));
await mkdir(resolve(root, 'public'), { recursive: true });
await writeFile(resolve(root, 'public/parking-view.html'), exportViewer({version:1,name:'Parking availability',width:1200,height:800,background:'',items:[]}, {}, '__PUBLIC_VIEW__'));
console.log('Built self-contained parking viewer.');
