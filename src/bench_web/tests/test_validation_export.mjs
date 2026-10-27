import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BrowserController} from '../web/browser/controller.mjs';
import {WebUsbTransport} from '../web/browser/transport.mjs';
import {MemoryStore} from './browser-fixtures.mjs';
import {nativeManifest, operatorReport} from './native-fixtures.mjs';

function fixture(diagnostics) {
  const manifest = nativeManifest(), run_id = 'run-' + 'a'.repeat(32), store = new MemoryStore();
  const configuration = {...manifest.configurations.gpu, run_id}, native = operatorReport(configuration, manifest.catalog);
  const session = {session_id: 'bench-' + 'b'.repeat(32), serial: 'private-serial', request_id: 'private-request',
    status: 'running', current_phase: 0, manifest, identity: {}, phases: [{run_id, configuration,
      attempted: true, status: 'running', run: {created_at: Date.now() / 1000, status: 'running'}}]};
  const transport = {read: async (_, id, file) => file === 'report.json' ? native : {run_id, status: 'complete', completed_cases: 221, sequence: 1},
    isRunning: async () => true, ...(diagnostics ? {diagnostics} : {})};
  const controller = new BrowserController({store, transport, manifestUrl: '', sleep: async () => {}});
  controller.manifest = manifest; controller.sessions = [session];
  return {controller, session, store, native, run_id};
}

test('legacy browser exports remain readable and validation export includes immutable submission bytes', async () => {
  const f = fixture();
  await f.store.saveReport(f.run_id, {native: f.native});
  const body = JSON.stringify(f.native), receipt = {run_id: f.run_id, stored: true, storage: 'postgresql'};
  await f.store.saveDelivery({run_id: f.run_id, status: 'sent', body, receipt});
  const value = await f.controller.exportSession(f.session.session_id);
  assert.equal(value.schema_version, 1); assert.deepEqual(value.reports, [f.native]);
  assert.equal(value.validation.deliveries[0].body, body);
  assert.deepEqual(value.validation.deliveries[0].receipt, receipt);
  assert.deepEqual(value.validation.diagnostics, []);
  assert.deepEqual(value.validation.release, f.session.manifest);
  assert.equal(value.session.serial, undefined); assert.equal(value.session.request_id, undefined);
});

test('loading a newer website never relabels a historical session as collected by that website', async () => {
  const f = fixture();
  f.session.site_sha256 = 'a'.repeat(64);
  f.controller.siteSha256 = 'b'.repeat(64);
  const value = await f.controller.exportSession(f.session.session_id);
  assert.equal(value.validation.site_sha256, 'a'.repeat(64));
  f.controller.event(f.session,'connected');
  assert.equal(f.session.browser_events[0].site_sha256,'b'.repeat(64));
});

test('terminal capture saves diagnostics without changing original reports or exposing them in status snapshots', async () => {
  let calls = 0;
  const f = fixture(async (manifest, run_id) => {calls++; return {run_id, package: manifest.package, lifecycle: {events: []}};});
  await f.controller.tick(f.session, new AbortController().signal);
  assert.equal(calls, 1); assert.equal(f.session.status, 'complete');
  const exported = await f.controller.exportSession(f.session.session_id);
  assert.equal(exported.validation.diagnostics[0].run_id, f.run_id);
  assert.deepEqual(exported.reports[0], f.native);
  assert.equal(f.controller.view(f.session).diagnostics, undefined);
  assert.equal(exported.validation.browser_events[0].event, 'native_terminal');
});

test('diagnostic failures preserve benchmark outcome and are explicit in exported evidence', async () => {
  const f = fixture(async () => {throw Error('OS diagnostics denied');});
  await f.controller.tick(f.session, new AbortController().signal);
  assert.equal(f.session.status, 'complete');
  assert.match((await f.controller.exportSession(f.session.session_id)).validation.diagnostics[0].error, /denied/);
});

test('device diagnostics use only read-only commands through the existing browser transport', async () => {
  const t = new WebUsbTransport(), manifest = nativeManifest(), run_id = 'run-' + 'c'.repeat(32), commands = [];
  t.readJson = async (_, path) => {
    assert.equal(path, `files/runs/${run_id}/lifecycle.json`);
    return {run_id, events: [{event: 'service_destroyed'}]};
  };
  t.shell = async args => {
    commands.push(args);
    if (args[0] === 'date') return {code:0, stdout:String(Date.now()), stderr:''};
    if (args[1] === 'thermalservice') throw Error('Not available on this OS');
    return {code: 0, stdout: 'snapshot', stderr: ''};
  };
  const diagnostic = await t.diagnostics(manifest, run_id);
  const snapshots = commands.filter(args => args[0] === 'dumpsys');
  assert.equal(snapshots.length, 4);
  assert.ok(commands.every(args => args[0] === 'dumpsys' ||
    JSON.stringify(args) === JSON.stringify(['date', '+%s%3N'])));
  assert.deepEqual(snapshots[0], ['dumpsys', 'activity', 'services', manifest.package]);
  assert.equal(diagnostic.apk_sha256, manifest.apk_sha256);
  assert.ok(diagnostic.clock.device_at);
  assert.match(diagnostic.commands.thermal.error, /OS/);
  await assert.rejects(t.diagnostics(manifest, '../escape'), /identity/);
});

test('installation evidence reads original version, identity, and report hashes through the USB owner', async () => {
  const t = new WebUsbTransport(), manifest = nativeManifest(), calls = [];
  t.device = {serial:'private-headset'};
  t.installedHash = async () => manifest.apk_sha256;
  t.shell = async args => {
    calls.push(args);
    if (args[0] === 'dumpsys') return {code:0,stdout:'Package ['+manifest.package+']\nversionCode='+manifest.version_code,stderr:''};
    if (args.at(-1) === 'shared_prefs/device-profile.xml')
      return {code:0,stdout:'<map><string name="id">11111111-2222-3333-4444-555555555555</string></map>',stderr:''};
    return {code:0,stdout:'a'.repeat(64)+'  files/runs/run-'+ 'b'.repeat(32)+'/report.json\n',stderr:''};
  };
  const snapshot = await t.installationSnapshot(manifest);
  assert.equal(snapshot.version_code,manifest.version_code);
  assert.equal(snapshot.installation_id,'11111111-2222-3333-4444-555555555555');
  assert.equal(snapshot.reports.length,1);
  assert.ok(calls.every(args => ['dumpsys','run-as'].includes(args[0])));
});
