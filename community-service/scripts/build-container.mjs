import { build } from 'esbuild';
import { cp, mkdir, readdir, rm } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const destination = new URL('dist/runtime/', root);
await rm(new URL('dist/', root), { recursive: true, force: true });
await mkdir(destination, { recursive: true });
await build({ entryPoints: {
  service: new URL('src/runtime/service.ts', root).pathname,
  seed: new URL('src/seeds/service.ts', root).pathname,
}, outdir: destination.pathname, outExtension: { '.js': '.mjs' }, bundle: true,
  platform: 'node', format: 'esm', target: 'node24', mainFields: ['module', 'main'], sourcemap: false });
for (const name of await readdir(new URL('src/runtime/', root))) {
  if (name.endsWith('.mjs')) await cp(new URL(`src/runtime/${name}`, root), new URL(name, destination));
}
await cp(new URL('../web/dist-operator/', root), new URL('assets/', destination), { recursive: true });
