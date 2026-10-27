import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hostingConfig, hostedHeaders} from '../../scripts/hosting-config.mjs';

const configured = {
  XRBENCH_DEPLOYMENT: 'hosted', XRBENCH_SUPABASE_URL: 'https://fixture.supabase.co',
  XRBENCH_SITE_ORIGIN: 'https://portfolio.example', XRBENCH_SITE_PATH: '/xrbench/',
};
test('hosted builds require the intended project and website and use a protected same-origin receiver', () => {
  for (const key of ['XRBENCH_SUPABASE_URL', 'XRBENCH_SITE_ORIGIN']) {
    const env = {...configured}; delete env[key];
    assert.throws(() => hostingConfig(env));
  }
  const config = hostingConfig(configured);
  assert.equal(config.endpoint, '/api/reports');
  assert.equal(config.delivery, 'automatic');
  assert.equal(config.sitePath, '/xrbench/');
  assert.match(hostedHeaders(config)['Content-Security-Policy'], /connect-src 'self';/);
});
test('build configuration refuses unsafe targets and cross-origin hosted upload destinations', () => {
  for (const value of ['http://fixture.supabase.co', 'https://user:secret@fixture.supabase.co',
    'https://fixture.supabase.co/path', 'https://fixture.supabase.co/?key=secret', 'https://localhost'])
    assert.throws(() => hostingConfig({...configured, XRBENCH_SUPABASE_URL: value}));
  assert.throws(() => hostingConfig({...configured, XRBENCH_REPORT_DELIVERY: 'local'}));
  for (const endpoint of ['https://other.example/upload', './api/reports', '/xrbench/api/reports', 'https://portfolio.example/api/reports'])
    assert.throws(() => hostingConfig({...configured, XRBENCH_REPORT_ENDPOINT: endpoint}), /same-origin/);
  assert.equal(hostingConfig({...configured, XRBENCH_REPORT_ENDPOINT: '/api/reports'}).endpoint, '/api/reports');
  const download = hostingConfig({...configured, XRBENCH_REPORT_DELIVERY: 'download', XRBENCH_REPORT_ENDPOINT: 'https://unused.example/upload'});
  assert.equal(download.endpoint, null); assert.equal(download.delivery, 'download');
  assert.throws(() => hostingConfig({...configured, XRBENCH_SITE_PATH: '/xrbench/../'}));
});
test('local releases retain their existing receiver configuration without hosted identity', () => {
  assert.deepEqual(hostingConfig({}), {mode: 'local', delivery: 'local', projectUrl: null,
    endpoint: './api/reports', siteOrigin: null, sitePath: '/'});
  assert.equal(hostingConfig({XRBENCH_REPORT_DELIVERY: 'automatic'}).endpoint, '/api/reports');
});
