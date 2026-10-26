import {build} from 'esbuild';
import {mkdir, readFile, writeFile, copyFile, access, readdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {reportEndpoint} from '../src/bench_web/web/browser/report-endpoint.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const out = path.resolve(root, process.env.XRBENCH_WEB_OUTPUT || 'dist');
const reportUrl = process.env.XRBENCH_REPORT_ENDPOINT || './api/reports';
const reportTarget = reportEndpoint(reportUrl, 'https://xrbench.invalid/');
if (reportUrl !== './api/reports' && (!reportUrl.startsWith('https://') || reportTarget.origin === 'https://xrbench.invalid'))
  throw Error('XRBENCH_REPORT_ENDPOINT must be an absolute HTTPS receiver URL.');
const receiverOrigin = reportUrl === './api/reports' ? '' : ' ' + reportTarget.origin;
// Keep arbitrary output paths from overwriting source or local state.
if (!out.startsWith(path.join(root, 'dist') + path.sep) && out !== path.join(root, 'dist') && !out.startsWith(path.join(root, 'artifacts') + path.sep)) throw Error('Build output must be dist/ or a directory inside artifacts/');
await mkdir(path.join(out, 'bench'), {recursive: true});
const venv = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
let python = process.env.XRBENCH_PYTHON;
if (!python) {try {await access(venv); python = venv;} catch {python = 'python';}}
const publish = spawnSync(python, ['-m', 'engine.web_distribution', '--output', out,
  ...(process.env.XRBENCH_RUNNER_BUILD ? ['--build', process.env.XRBENCH_RUNNER_BUILD] : [])], {cwd: root, stdio: 'inherit'});
if (publish.status !== 0) throw Error('The release pipeline must provide a signed, matched native runner before publishing.');
const bundled = await build({entryPoints: [path.join(root, 'src/bench_web/web/app.js')], outfile: path.join(out, 'bench/app.js'), metafile: true,
  bundle: true, format: 'esm', platform: 'browser', target: ['chrome120', 'edge120'], minify: true, legalComments: 'eof',
  plugins: [{name: 'dashboard-paths', setup(b) {
    b.onResolve({filter: /^\/(bench|stats|shared)\//}, ({path: name}) => {
      const [, prefix, ...parts] = name.split('/');
      const base = {bench: 'src/bench_web/web', stats: 'src/stats_for_nerds/web', shared: 'engine/web'}[prefix];
      return {path: path.join(root, base, ...parts)};
    });
  }}]});
let html = await readFile(path.join(root, 'src/bench_web/web/index.html'), 'utf8');
html = html.replace('<html lang="en">', '<html lang="en" data-transport="webusb">')
  .replace('<head>', '<head>\n  <meta name="xrbench-report-endpoint" content="' + reportUrl.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;') + '">')
  .replaceAll('"/bench/', '"./bench/').replace('href="/"', 'href="./"')
  .replace(/<details id="setup-help"[\s\S]*?<\/details>/, '')
  .replace('<a id="stats-link"', '<a hidden id="stats-link"')
  .replace('Checking the local benchmark service…', 'Connect your headset by USB, then press Run.')
  .replace('Recognition, compatibility, and hardware validation are separate.', 'Desktop Chrome or Edge and a USB data cable are required. Recognition, compatibility, and hardware validation are separate.')
  .replace('Android devices require USB debugging.', 'Headsets require developer mode and USB debugging permission. Some computer/device combinations require a USB driver.');
await writeFile(path.join(out, 'index.html'), html);
await copyFile(path.join(root, 'src/bench_web/web/style.css'), path.join(out, 'bench/style.css'));
await copyFile(path.join(root, 'THIRD_PARTY_NOTICES.md'), path.join(out, 'THIRD_PARTY_NOTICES.md'));
const packages = new Set();
for (const input of Object.keys(bundled.metafile.inputs)) {
  const match = input.replaceAll('\\', '/').match(/node_modules\/((?:@[^/]+\/)?[^/]+)/);
  if (match) packages.add(match[1]);
}
for (const name of packages) {
  const source = path.join(root, 'node_modules', name), destination = path.join(out, 'licenses', name);
  await mkdir(destination, {recursive: true});
  for (const file of await readdir(source)) if (/^(license|copying|notice)/i.test(file)) await copyFile(path.join(source, file), path.join(destination, file));
  await copyFile(path.join(source, 'package.json'), path.join(destination, 'package.json'));
}
// Supported by static hosts with _headers; equivalent policy is documented for other hosts.
await writeFile(path.join(out, '_headers'), `/*
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'${receiverOrigin}; object-src 'none'; base-uri 'self'; frame-ancestors 'none'
  Permissions-Policy: usb=(self)
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
/runner/manifest.json
  Cache-Control: no-cache
`);
console.log('Static website ready at ' + out);
