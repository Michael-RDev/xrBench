import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, writeFile, rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {installPortfolioSection} from '../../scripts/install-portfolio-section.mjs';
import {hostingConfig, hostedHeaders} from '../../scripts/hosting-config.mjs';
import {nativeManifest} from '../../src/bench_web/tests/native-fixtures.mjs';

const project = 'nqsieptekarxxmqrgmdy';
async function fixture(delivery = 'download') {
  const root = await mkdtemp(path.join(os.tmpdir(), 'xrbench-portfolio-install-'));
  const source = path.join(root, 'package'), portfolio = path.join(root, 'portfolio');
  await mkdir(path.join(source, 'bench'), {recursive: true});
  await mkdir(path.join(source, 'runner')); await mkdir(path.join(portfolio, 'public'), {recursive: true});
  const config = hostingConfig({XRBENCH_DEPLOYMENT: 'hosted', XRBENCH_SUPABASE_URL: `https://${project}.supabase.co`,
    XRBENCH_REPORT_DELIVERY: delivery,
    XRBENCH_SITE_ORIGIN: 'https://portfolio.example', XRBENCH_SITE_PATH: '/xrbench/'});
  const headers = hostedHeaders(config), apk = Buffer.from('synthetic APK hash fixture');
  const manifest = nativeManifest();
  manifest.apk_sha256 = createHash('sha256').update(apk).digest('hex');
  manifest.apk = manifest.apk_sha256 + '.apk'; manifest.apk_bytes = apk.byteLength;
  manifest.configurations.gpu.apk_sha256 = manifest.apk_sha256;
  await writeFile(path.join(source, 'runner', manifest.apk), apk);
  await writeFile(path.join(source, 'runner/manifest.json'), JSON.stringify(manifest));
  await writeFile(path.join(source, 'index.html'), '<!doctype html><html data-transport="webusb"><head><base href="/xrbench/"><meta name="xrbench-report-delivery" content="' + delivery + '">' +
    (config.endpoint ? '<meta name="xrbench-report-endpoint" content="' + config.endpoint + '">' : '') + '</head></html>');
  await writeFile(path.join(source, 'bench/app.js'), '/* synthetic packaging fixture */');
  await writeFile(path.join(source, 'bench/style.css'), 'body {}');
  await writeFile(path.join(source, '_headers'), '/*\n' + Object.entries(headers).map(([k,v]) => `  ${k}: ${v}\n`).join(''));
  await writeFile(path.join(source, 'hosting-manifest.json'), JSON.stringify({schema_version: 1, deployment: 'hosted',
    site_origin: config.siteOrigin, site_path: config.sitePath, supabase_url: config.projectUrl,
    report_delivery: delivery, report_endpoint: config.endpoint, security_headers: headers}));
  return {root, source, portfolio, options: {directory: source, portfolio}};
}

test('installer checks without writing, then installs and safely updates only its managed section', async () => {
  const f = await fixture();
  try {
    await writeFile(path.join(f.portfolio, 'public/portfolio.txt'), 'untouched');
    assert.equal((await installPortfolioSection({...f.options, check: true})).installed, false);
    await assert.rejects(readFile(path.join(f.portfolio, '.xrbench-section.json')));
    assert.equal((await installPortfolioSection(f.options)).installed, true);
    assert.equal(await readFile(path.join(f.portfolio, 'public/portfolio.txt'), 'utf8'), 'untouched');
    await writeFile(path.join(f.source, 'bench/app.js'), '/* updated synthetic fixture */');
    await installPortfolioSection(f.options);
    assert.equal(await readFile(path.join(f.portfolio, 'public/xrbench/bench/app.js'), 'utf8'), '/* updated synthetic fixture */');
  } finally {await rm(f.root, {recursive: true, force: true});}
});
test('installer preserves unmanaged directories and refuses locally edited managed assets', async () => {
  const f = await fixture();
  try {
    await mkdir(path.join(f.portfolio, 'public/xrbench'));
    await writeFile(path.join(f.portfolio, 'public/xrbench/personal.txt'), 'preserve');
    await assert.rejects(installPortfolioSection(f.options), /unmanaged/);
    assert.equal(await readFile(path.join(f.portfolio, 'public/xrbench/personal.txt'), 'utf8'), 'preserve');
    await rm(path.join(f.portfolio, 'public/xrbench'), {recursive: true});
    await installPortfolioSection(f.options);
    await writeFile(path.join(f.portfolio, 'public/xrbench/bench/app.js'), 'local edit');
    await assert.rejects(installPortfolioSection(f.options), /changed locally/);
    assert.equal(await readFile(path.join(f.portfolio, 'public/xrbench/bench/app.js'), 'utf8'), 'local edit');
  } finally {await rm(f.root, {recursive: true, force: true});}
});

test('hosted automatic packages validate and install with only the protected same-origin endpoint', async () => {
  const f = await fixture('automatic');
  try {
    assert.equal((await installPortfolioSection({...f.options, check: true})).checked, true);
    await installPortfolioSection(f.options);
    const html = await readFile(path.join(f.portfolio, 'public/xrbench/index.html'), 'utf8');
    assert.match(html, /xrbench-report-delivery" content="automatic/);
    assert.match(html, /xrbench-report-endpoint" content="\/api\/reports/);
    assert.doesNotMatch(html, /xrbench-auth-|type="email"/);
  } finally {await rm(f.root, {recursive: true, force: true});}
});

test('package verification catches a deployment base accidentally rewritten by a homepage-link transform', async () => {
  const f = await fixture('automatic');
  try {
    const file=path.join(f.source,'index.html'),html=await readFile(file,'utf8');
    await writeFile(file,html.replace('<base href="/xrbench/">','<base href="./">'));
    await assert.rejects(installPortfolioSection({...f.options,check:true}), /base path differs/);
  } finally {await rm(f.root, {recursive: true, force: true});}
});
