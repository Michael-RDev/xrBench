import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hostingConfig, hostedHeaders} from '../../scripts/hosting-config.mjs';
import {verifyLiveHosting} from '../../scripts/verify-hosting.mjs';

const config = hostingConfig({XRBENCH_DEPLOYMENT: 'hosted',
  XRBENCH_SUPABASE_URL: 'https://fixture.supabase.co',
  XRBENCH_REPORT_DELIVERY: 'download',
  XRBENCH_SITE_ORIGIN: 'https://portfolio.example', XRBENCH_SITE_PATH: '/xrbench/'});
const html = '<meta name="xrbench-report-delivery" content="download">';

test('live readiness permits the host canonical section redirect and requires real security gates', async () => {
  const calls = [];
  const fetcher = async (target, options) => {
    const url = String(target); calls.push({url, options});
    if (url === config.siteOrigin + '/xrbench/')
      return new Response(null, {status: 308, headers: {location: '/xrbench'}});
    if (url === config.siteOrigin + '/xrbench') return new Response(html, {headers: hostedHeaders(config)});
    assert.fail('A download-only readiness check must not contact authentication or upload services.');
  };
  assert.equal((await verifyLiveHosting({config}, {fetcher})).length, 1);
  assert.equal(calls.length, 2);
});

test('readiness refuses a redirect to another origin or unrelated portfolio page', async () => {
  for (const location of ['https://other.example/xrbench/', '/about', '/xrbench?access_token=private']) {
    let calls = 0;
    await assert.rejects(verifyLiveHosting({config}, {fetcher: async () => {
      calls++; return new Response(null, {status: 302, headers: {location}});
    }}), /outside its intended section/);
    assert.equal(calls, 1);
  }
});

test('readiness rejects a visually correct page with missing policy headers', async () => {
  await assert.rejects(verifyLiveHosting({config}, {fetcher: async () => new Response(html)}),
    /has not applied Content-Security-Policy/);
});

test('automatic live readiness checks the same-origin protected route without POSTing or spending upload quota', async () => {
  const automatic = {...config, delivery: 'automatic', endpoint: '/api/reports'};
  const html = '<meta name="xrbench-report-delivery" content="automatic"><meta name="xrbench-report-endpoint" content="/api/reports">';
  for (const status of [401, 403, 405]) {
    const calls = [];
    const result = await verifyLiveHosting({config: automatic}, {fetcher: async (target, options) => {
      calls.push({target: String(target), options});
      return String(target) === automatic.siteOrigin + '/api/reports' ? new Response(null, {status}) :
        new Response(html, {headers: hostedHeaders(automatic)});
    }});
    assert.equal(result.length, 2); assert.equal(calls.length, 2);
    assert.equal(calls[1].target, automatic.siteOrigin + '/api/reports');
    assert.equal(calls[1].options.method, 'GET'); assert.equal(calls[1].options.redirect, 'manual');
  }
});

test('automatic readiness rejects missing, failed or HTML fallback receiver routes', async () => {
  const automatic = {...config, delivery: 'automatic', endpoint: '/api/reports'};
  const html = '<meta name="xrbench-report-delivery" content="automatic"><meta name="xrbench-report-endpoint" content="/api/reports">';
  for (const status of [200, 302, 404, 500, 503])
    await assert.rejects(verifyLiveHosting({config: automatic}, {fetcher: async target =>
      String(target).endsWith('/api/reports') ? new Response(null, {status}) :
        new Response(html, {headers: hostedHeaders(automatic)})}), /unavailable or incorrectly routed/);
});
