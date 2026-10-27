import {build} from 'esbuild';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {validateManifest} from '../src/bench_web/web/browser/evidence.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const output = path.join(root, 'supabase/functions/submit-report');
const files = process.argv.slice(2);
if (!files.length) files.push('dist/runner/manifest.json');
const manifests = [];
for (const file of files) manifests.push(validateManifest(JSON.parse(await readFile(path.resolve(root, file), 'utf8'))));
await mkdir(output, {recursive: true});
await build({entryPoints: [path.join(root, 'server/supabase-adapter.mjs')], outfile: path.join(output, 'receiver.mjs'),
  bundle: true, format: 'esm', platform: 'neutral', target: 'es2022'});
await writeFile(path.join(output, 'releases.json'), JSON.stringify(manifests));
console.log(`Supabase receiver prepared for ${manifests.length} runner release(s).`);
