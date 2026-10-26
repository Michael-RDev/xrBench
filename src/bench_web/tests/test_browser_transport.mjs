import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WebUsbTransport, connectionMessage, connectionError} from '../web/browser/transport.mjs';
import {AdbDaemonWebUsbDevice} from '@yume-chan/adb-daemon-webusb';
import {sha256} from '../web/browser/evidence.mjs';
import {manifest} from './browser-fixtures.mjs';

test('permission chooser is invoked synchronously and multiple headsets never prefer Quest 3', async () => {
  const previous = globalThis.isSecureContext; globalThis.isSecureContext = true;
  try {
    let requests = 0;
    const t = new WebUsbTransport({manager: {requestDevice: async () => {requests++; return {serial: 'two'};}}});
    t.devices = [{serial: 'one', name: 'Quest 3'}, {serial: 'two', name: 'Quest 2'}];
    const choice = t.choose(); assert.equal(requests, 1); assert.equal((await choice).serial, 'two');
    assert.equal((await t.choose('one')).serial, 'one'); assert.equal(requests, 1);
  } finally {globalThis.isSecureContext = previous;}
});
test('denied and missing permissions and busy USB interfaces have distinct recovery guidance', () => {
  assert.match(connectionMessage(new DOMException('', 'NotAllowedError')), /permission was denied/);
  assert.match(connectionMessage(new DOMException('', 'NotFoundError')), /No headset selected/);
  assert.match(connectionMessage(Error('Failed to claim interface')), /Close other headset tools/);
});

test('the actual USB library busy error retains its category and closes the opened device', async () => {
  const cause = new AdbDaemonWebUsbDevice.DeviceBusyError(new DOMException('claim failed', 'NetworkError'));
  let closes = 0;
  const raw = {opened: true, close: async () => {closes++; raw.opened = false;}};
  const t = new WebUsbTransport();
  await assert.rejects(t.connect({serial: 'busy', raw, connect: async () => {throw cause;}}), error => {
    assert.equal(error.code, 'usb_busy'); assert.match(error.message, /Retry connection/);
    assert.equal(connectionError(Error('outer wrapper', {cause: error})).code, 'usb_busy'); return true;
  });
  assert.equal(closes, 1); assert.equal(t.device, null); assert.equal(t.adb, null);
  for (const message of ['The device is already in use by another program', 'The device is already in used by another program'])
    assert.equal(connectionError(Error(message)).code, 'usb_busy');
});

test('process absence is distinguished from a failed liveness command', async () => {
  const t = new WebUsbTransport(), m = manifest();
  for (const [code, stdout, stderr, expected] of [[0, '123 456\n', '', true], [1, '', '', false]]) {
    t.shell = async args => {assert.deepEqual(args, ['pidof', m.package]); return {code, stdout, stderr};};
    assert.equal(await t.isRunning(m), expected);
  }
  t.shell = async () => ({code: 127, stdout: '', stderr: 'pidof: unavailable'});
  await assert.rejects(() => t.isRunning(m), /Could not check/);
});
test('matching installed APK is reused without a download or install', async () => {
  const m = manifest(), t = new WebUsbTransport({fetcher: () => assert.fail('must not download')});
  t.installedHash = async () => m.apk_sha256;
  t.shell = () => assert.fail('must not install');
  await t.install(m, 'https://fixture.test/runner/manifest.json', new AbortController().signal, () => {});
});

test('runner download retains the browser fetch receiver', async t => {
  const bytes = new Uint8Array([1, 2, 3]), m = manifest();
  m.apk_sha256 = await sha256(bytes); m.apk = m.apk_sha256 + '.apk'; m.apk_bytes = bytes.length;
  t.mock.method(globalThis, 'fetch', async function() {
    assert.equal(this, globalThis);
    return new Response(bytes);
  });
  const transport = new WebUsbTransport(); let installed = false;
  transport.installedHash = async () => installed ? m.apk_sha256 : null;
  transport.shell = async () => {installed = true; return {code: 0, stdout: 'Success', stderr: ''};};
  await transport.install(m, 'https://fixture.test/runner/manifest.json', new AbortController().signal, () => {});
  assert.equal(installed, true);
});

test('an older runner updates in place and capabilities must match the distributed runtime', async () => {
  const bytes = new Uint8Array([1, 2, 3]), m = manifest();
  m.apk_sha256 = await sha256(bytes); m.apk = m.apk_sha256 + '.apk';
  const t = new WebUsbTransport({fetcher: async () => new Response(bytes)}); let installed = false, probe;
  t.installedHash = async () => installed ? m.apk_sha256 : 'c'.repeat(64);
  t.shell = async args => {
    assert.deepEqual(args.slice(0, 4), ['pm', 'install', '-r', '-t']);
    installed = true; return {code: 0, stdout: 'Success', stderr: ''};
  };
  await t.install(m, 'https://fixture.test/runner/manifest.json', new AbortController().signal, () => {});
  t.launchActivity = async (_, key, id) => {assert.equal(key, 'probe_id'); probe = id;};
  const profile = {profile_version: 1, runner_revision: m.runner_revision, runtime_revision: m.runtime_revision,
    backends: {executorch_cpu: {compatible: true}, executorch_vulkan: {compatible: false, reason: 'Unavailable'}}};
  t.readJson = async () => ({...profile, probe_id: probe});
  assert.equal((await t.probe(m)).backends.executorch_cpu.compatible, true);
  profile.runtime_revision = 'wrong';
  await assert.rejects(() => t.probe(m), /capability identity mismatch/);
});
test('APK download is checked before streaming installation and corrupt bytes never reach the device', async () => {
  for (const corrupt of [false, true]) {
    const bytes = new Uint8Array([1, 2, 3]), m = manifest(); m.apk_sha256 = await sha256(bytes); m.apk = m.apk_sha256 + '.apk';
    const calls = [], t = new WebUsbTransport({fetcher: async () => new Response(corrupt ? new Uint8Array([3, 2, 1]) : bytes)});
    let installed = false; t.installedHash = async () => installed ? m.apk_sha256 : null;
    t.shell = async (args, options) => {calls.push(args); assert.deepEqual(options.data, bytes); installed = true; return {code: 0, stdout: 'Success\n', stderr: ''};};
    if (corrupt) {await assert.rejects(() => t.install(m, 'https://fixture.test/runner/manifest.json', new AbortController().signal, () => {}), /checksum failed/); assert.equal(calls.length, 0);}
    else {await t.install(m, 'https://fixture.test/runner/manifest.json', new AbortController().signal, () => {}); assert.deepEqual(calls, [['pm', 'install', '-r', '-t', '-d', '-S', '3']]);}
  }
});
test('signature conflicts never uninstall an existing app', async () => {
  const bytes = new Uint8Array([1, 2, 3]), m = manifest(); m.apk_sha256 = await sha256(bytes); m.apk = m.apk_sha256 + '.apk';
  const calls = [], t = new WebUsbTransport({fetcher: async () => new Response(bytes)});
  t.installedHash = async () => null;
  t.shell = async args => {calls.push(args); return {code: 1, stderr: 'INSTALL_FAILED_UPDATE_INCOMPATIBLE', stdout: ''};};
  await assert.rejects(() => t.install(m, 'https://fixture.test/runner/manifest.json', new AbortController().signal, () => {}), /was not removed/);
  assert.equal(calls.length, 1); assert.ok(calls.every(c => !c.includes('uninstall')));
});
test('shell v2 quotes arguments, streams stdin, and drains stdout and stderr concurrently', async () => {
  const t = new WebUsbTransport(), written = [], data = new Uint8Array(70000); let command, closed = false;
  const output = text => new ReadableStream({start(c) {c.enqueue(new TextEncoder().encode(text)); c.close();}});
  t.adb = {subprocess: {shellProtocol: {spawn: async args => {
    command = args;
    return {stdin: new WritableStream({write(v) {written.push(v.length);}, close() {closed = true;}}), stdout: output('ok'), stderr: output('note'), exited: Promise.resolve(0)};
  }}}};
  const r = await t.shell(['run-as', 'org.xrgpu.benchmarks.web', 'sh', '-c', "cat > 'some file'"], {data});
  assert.deepEqual(written, [65536, 4464]); assert.ok(closed); assert.equal(r.stdout, 'ok'); assert.equal(r.stderr, 'note');
  assert.equal(command.at(-1), "'cat > '\\''some file'\\'''" );
});
test('native launch writes atomically and uses the separate package and full activity name', async () => {
  const t = new WebUsbTransport(), m = manifest(), calls = [];
  t.shell = async (args, opts) => {calls.push({args, opts}); return {stdout: '', code: 0};};
  const config = {...m.configurations.cpu, run_id: 'run-' + 'a'.repeat(32)};
  await t.launch(m, config);
  assert.match(calls[0].args.at(-1), /test ! -e .*report.json.*\.part.*mv /);
  assert.deepEqual(JSON.parse(new TextDecoder().decode(calls[0].opts.data)), config);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].args.slice(0,4), ['am','start','-n','org.xrgpu.benchmarks.web/org.xrgpu.benchmarks.MainActivity']);
});

test('quick shell commands and size-delimited installs can exit before stdin closes', async () => {
  for (const data of [undefined, new Uint8Array([1])]) {
    const t = new WebUsbTransport(); let writes = 0;
    t.adb = {subprocess: {shellProtocol: {spawn: async () => ({
      stdin: new WritableStream({write() {writes++;}, close() {throw Error('Socket closed');}}),
      stdout: new ReadableStream({start(c) {c.enqueue(new TextEncoder().encode('Success')); c.close();}}),
      stderr: new ReadableStream({start(c) {c.close();}}), exited: Promise.resolve(0),
    })}}};
    const result = await t.shell(['pm', 'path', 'org.xrgpu.benchmarks.web'], {data});
    assert.equal(result.code, 0); assert.equal(result.stdout, 'Success');
    assert.equal(writes, data ? 1 : 0);
  }
});

test('early install rejection preserves Android diagnostics when stdin fails', async () => {
  const t = new WebUsbTransport();
  const output = text => new ReadableStream({start(c) {c.enqueue(new TextEncoder().encode(text)); c.close();}});
  t.adb = {subprocess: {shellProtocol: {spawn: async () => ({
    stdin: new WritableStream({write() {throw Error('Socket closed');}}),
    stdout: output(''), stderr: output('INSTALL_FAILED_UPDATE_INCOMPATIBLE'), exited: Promise.resolve(1),
  })}}};
  const result = await t.shell(['pm', 'install'], {data: new Uint8Array([1]), allowFailure: true});
  assert.equal(result.code, 1); assert.equal(result.stderr, 'INSTALL_FAILED_UPDATE_INCOMPATIBLE');
});
