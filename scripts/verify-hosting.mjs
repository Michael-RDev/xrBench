import assert from 'node:assert/strict';
import {readFile, readdir, lstat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {hostingConfig, hostedHeaders} from './hosting-config.mjs';
import {validateManifest} from '../src/bench_web/web/browser/evidence.mjs';

function metadata(html, name) {
  const matches = [...html.matchAll(new RegExp(`<meta name="${name}" content="([^"]*)">`, 'g'))];
  assert.equal(matches.length, 1, `Expected one ${name} deployment setting.`);
  return matches[0][1].replaceAll('&quot;', '"').replaceAll('&lt;', '<').replaceAll('&amp;', '&');
}

function publicFile(name) {
  return ['index.html', 'hosting-manifest.json', 'site-manifest.json', '_headers', 'THIRD_PARTY_NOTICES.md',
    'bench/app.js', 'bench/style.css', 'runner/manifest.json'].includes(name) ||
    /^runner\/[a-f0-9]{64}\.apk$/.test(name) ||
    /^licenses\/(?:@[^/.][^/]*\/)?[^/.][^/]*\/(?:package\.json|(?:license|copying|notice)[^/]*)$/i.test(name);
}

export async function verifyHostingPackage(directory, {projectRef, origin, sitePath} = {}) {
  const root = path.resolve(directory), checks = [];
  const deployment = JSON.parse(await readFile(path.join(root, 'hosting-manifest.json'), 'utf8'));
  assert.equal(deployment.schema_version, 1, 'Unknown hosting manifest version.');
  assert.equal(deployment.deployment, 'hosted', 'This is a local release; build with XRBENCH_DEPLOYMENT=hosted.');
  const html = await readFile(path.join(root, 'index.html'), 'utf8');
  const config = hostingConfig({XRBENCH_DEPLOYMENT: 'hosted',
    XRBENCH_SUPABASE_URL: deployment.supabase_url,
    XRBENCH_REPORT_DELIVERY: metadata(html, 'xrbench-report-delivery'),
    XRBENCH_REPORT_ENDPOINT: deployment.report_endpoint || undefined,
    XRBENCH_SITE_ORIGIN: deployment.site_origin, XRBENCH_SITE_PATH: deployment.site_path});
  const bases = [...html.matchAll(/<base href="([^"]*)">/g)];
  assert.equal(bases.length, 1, 'Expected one deployment base path.');
  assert.equal(bases[0][1], config.sitePath, 'The HTML base path differs from the hosting configuration.');
  assert.equal(deployment.report_delivery, config.delivery, 'HTML and hosting manifest delivery modes differ.');
  assert.equal(deployment.report_endpoint, config.endpoint, 'The hosted receiver differs from the build contract.');
  assert.doesNotMatch(html, /name="xrbench-auth-/, 'The public page must not contain sign-in or browser service-key configuration.');
  if (config.delivery === 'automatic')
    assert.equal(metadata(html, 'xrbench-report-endpoint'), '/api/reports', 'Automatic uploads must use the protected same-origin receiver.');
  else assert.doesNotMatch(html, /name="xrbench-report-endpoint"/, 'Download-only pages must not configure uploads.');
  if (projectRef) assert.equal(config.projectUrl, `https://${projectRef}.supabase.co`, 'This package targets another Supabase project.');
  if (origin) assert.equal(config.siteOrigin, origin, 'This package targets another website origin.');
  if (sitePath) assert.equal(config.sitePath, sitePath, 'This package targets another website section.');
  checks.push(config.delivery === 'automatic' ?
    'Automatic uploads target the protected same-origin /api/reports route; JSON downloads remain available without contact details.' :
    'Download-only delivery and intended project/origin/path agree; no sign-in or automatic upload is configured.');

  const expected = hostedHeaders(config), headerFile = await readFile(path.join(root, '_headers'), 'utf8');
  assert.deepEqual(deployment.security_headers, expected, 'Hosting manifest security policy differs from the build contract.');
  for (const [name, value] of Object.entries(expected))
    assert.ok(headerFile.includes(`  ${name}: ${value}\n`), `Missing ${name} static-host policy.`);
  assert.match(html, /data-transport="webusb"/, 'Production website must use browser USB.');
  assert.doesNotMatch(html, /Checking the local benchmark service|engine\.build/);
  checks.push('CSP, USB permissions, framing, referrer and HTTPS headers are packaged.');

  async function inspect(directory, prefix = '') {
    for (const name of await readdir(directory)) {
      const relative = prefix + name, full = path.join(directory, name), info = await lstat(full);
      assert.ok(!info.isSymbolicLink(), `Symlink in deployment: ${relative}`);
      assert.ok(!name.startsWith('.'), `Hidden file in deployment: ${relative}`);
      if (info.isDirectory()) await inspect(full, relative + '/');
      else assert.ok(info.isFile() && publicFile(relative), `Unrecognized deployment file: ${relative}`);
    }
  }
  await inspect(root);
  checks.push('Published tree contains only approved assets and no symlinks or hidden files.');
  const manifest = validateManifest(JSON.parse(await readFile(path.join(root, 'runner/manifest.json'), 'utf8')));
  assert.match(manifest.apk, /^[a-f0-9]{64}\.apk$/);
  const apk = await readFile(path.join(root, 'runner', manifest.apk));
  assert.equal(createHash('sha256').update(apk).digest('hex'), manifest.apk_sha256, 'Runner APK bytes do not match the release.');
  assert.equal(manifest.apk, manifest.apk_sha256 + '.apk');
  checks.push('Runner manifest validates and the packaged APK hash matches.');
  return {ready: true, config, checks};
}

export async function verifyLiveHosting(packageResult, {fetcher = globalThis.fetch} = {}) {
  const {config} = packageResult, checks = [];
  const root = new URL(config.sitePath, config.siteOrigin);
  const canonicalPaths = new Set([config.sitePath, config.sitePath.slice(0, -1), config.sitePath + 'index.html']);
  let current = root, response;
  for (let redirects = 0; redirects < 3; redirects++) {
    response = await fetcher(current, {redirect: 'manual', signal: AbortSignal.timeout(15000)});
    if (![301, 302, 307, 308].includes(response.status)) break;
    const location = response.headers.get('location');
    assert.ok(location, 'Website redirect has no destination.');
    const next = new URL(location, current);
    assert.ok(next.origin === config.siteOrigin && canonicalPaths.has(next.pathname) &&
      !next.username && !next.password && !next.search && !next.hash,
    'The website redirects outside its intended section.');
    await response.body?.cancel(); current = next;
  }
  assert.equal(response.status, 200, 'The intended website section is not serving HTTP 200.');
  for (const [name, value] of Object.entries(hostedHeaders(config)))
    assert.equal(response.headers.get(name), value, `The live host has not applied ${name}.`);
  const html = await response.text();
  assert.equal(metadata(html, 'xrbench-report-delivery'), config.delivery);
  assert.doesNotMatch(html, /name="xrbench-auth-/);
  if (config.delivery === 'automatic') {
    assert.equal(metadata(html, 'xrbench-report-endpoint'), '/api/reports');
    checks.push('Live website section enables automatic uploads and JSON downloads with the required security headers.');
    // A GET cannot ingest evidence or reserve upload quota. A protected receiver
    // must reject it rather than return a portfolio HTML fallback or a 404/503.
    const receiver = await fetcher(new URL('/api/reports', config.siteOrigin), {
      method: 'GET', credentials: 'same-origin', redirect: 'manual', signal: AbortSignal.timeout(15000)});
    assert.ok([401, 403, 405].includes(receiver.status),
      'The protected same-origin report receiver is unavailable or incorrectly routed.');
    await receiver.body?.cancel();
    checks.push('Same-origin report URL is protected or rejects non-upload methods; transaction receipts require separate backend verification.');
  } else {
    assert.doesNotMatch(html, /name="xrbench-report-endpoint"/);
    checks.push('Live website section serves download-only results with the required security headers.');
  }
  return checks;
}

async function main() {
  const args = process.argv.slice(2), directory = args.shift() || 'dist';
  const options = {}; let live = false;
  while (args.length) {
    const option = args.shift();
    if (option === '--live') live = true;
    else if (['--project-ref', '--origin', '--path'].includes(option) && args.length)
      options[{'--project-ref': 'projectRef', '--origin': 'origin', '--path': 'sitePath'}[option]] = args.shift();
    else throw Error('Usage: verify-hosting.mjs dist [--project-ref REF] [--origin HTTPS_ORIGIN] [--path /section/] [--live]');
  }
  const result = await verifyHostingPackage(directory, options);
  for (const check of result.checks) console.log('PASS ' + check);
  if (live) for (const check of await verifyLiveHosting(result)) console.log('PASS ' + check);
  console.log(live ? 'Website live checks passed; protected backend storage receipts are audited separately.' :
    'Deployment package checks passed. Live publication and protected backend activation are separate checks.');
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href)
  main().catch(error => {console.error('FAIL ' + error.message); process.exitCode = 1;});
