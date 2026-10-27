import {build} from 'esbuild';
import {mkdir, readFile, writeFile, copyFile, access, readdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {reportEndpoint} from '../src/bench_web/web/browser/report-endpoint.mjs';
import {hostingConfig, hostedHeaders} from './hosting-config.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const out = path.resolve(root, process.env.XRBENCH_WEB_OUTPUT || 'dist');
const hosting = hostingConfig();
const reportUrl = hosting.endpoint;
const reportTarget = reportUrl ? reportEndpoint(reportUrl, 'https://xrbench.invalid/') : null;
if (reportUrl && !['./api/reports', '/api/reports'].includes(reportUrl) && (!reportUrl.startsWith('https://') || reportTarget.origin === 'https://xrbench.invalid'))
  throw Error('XRBENCH_REPORT_ENDPOINT must be an absolute HTTPS receiver URL.');
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
const escapeAttribute = value => value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;');
const deploymentMetadata = (hosting.mode === 'hosted' ? `\n  <base href="${escapeAttribute(hosting.sitePath)}">` : '') +
  `\n  <meta name="xrbench-report-delivery" content="${hosting.delivery}">` +
  (reportUrl ? `\n  <meta name="xrbench-report-endpoint" content="${escapeAttribute(reportUrl)}">` : '');
html = html.replace('<html lang="en">', '<html lang="en" data-transport="webusb">')
  .replace('<head>', '<head>' + deploymentMetadata)
  .replaceAll('"/bench/', '"./bench/').replace('<a class="brand" href="/"', '<a class="brand" href="./"')
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
const securityHeaders = hostedHeaders(hosting);
await writeFile(path.join(out, '_headers'), '/*\n' +
  Object.entries(securityHeaders).map(([name, value]) => `  ${name}: ${value}\n`).join('') +
  '/runner/manifest.json\n  Cache-Control: no-cache\n');
await writeFile(path.join(out, 'hosting-manifest.json'), JSON.stringify({
  schema_version: 1, deployment: hosting.mode, site_origin: hosting.siteOrigin, site_path: hosting.sitePath,
  supabase_url: hosting.projectUrl, report_delivery: hosting.delivery,
  report_endpoint: reportUrl, security_headers: securityHeaders,
}, null, 2) + '\n');
console.log('Static website ready at ' + out);
// Bind campaign exports to the actual browser implementation and delivery
// configuration, independently of the runner APK. Exclude only this injected
// identity marker from the HTML hash to avoid a self-referential digest.
const siteFiles = {};
for (const name of ['bench/app.js', 'bench/style.css'])
  siteFiles[name] = createHash('sha256').update(await readFile(path.join(out, name))).digest('hex');
for (const [name, attribute] of [['bench/app.js','src'], ['bench/style.css','href']])
  html = html.replace(attribute + '="./' + name + '"',
    attribute + '="./' + name + '" integrity="sha256-' + Buffer.from(siteFiles[name], 'hex').toString('base64') + '"');
siteFiles['index.html'] = createHash('sha256').update(html).digest('hex');
const siteSha256 = createHash('sha256').update(JSON.stringify(siteFiles)).digest('hex');
await writeFile(path.join(out, 'index.html'), html.replace('<head>',
  '<head>\n  <meta name="xrbench-site-sha256" content="' + siteSha256 + '">'));
await writeFile(path.join(out, 'site-manifest.json'), JSON.stringify({schema_version:1, site_sha256:siteSha256, files:siteFiles}, null, 2) + '\n');
