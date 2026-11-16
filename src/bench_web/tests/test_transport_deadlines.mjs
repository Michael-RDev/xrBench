import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {AdbCommand, AdbAuthType, AdbDaemonTransport, AdbFeature, AdbShellProtocolSubprocessService} from '@yume-chan/adb';
import {Consumable, ReadableStream, WritableStream} from '@yume-chan/stream-extra';
import {WebUsbTransport} from '../web/browser/transport.mjs';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}

for (const failure of ['shell protocol', 'ADB construction']) test(`failed ${failure} closes raw USB even when authenticated cleanup rejects`, async context => {
  let rawCloses = 0, authenticatedCloses = 0;
  const raw = {opened: true, async close() {rawCloses++; raw.opened = false;}};
  const authenticated = {
    clientFeatures: [], banner: {features: []}, disconnected: new Promise(() => {}),
    async close() {authenticatedCloses++; throw Error('Authenticated cleanup failed');},
  };
  if (failure === 'ADB construction') Object.defineProperty(authenticated, 'clientFeatures', {
    get() {throw Error('ADB construction failed');},
  });
  context.mock.method(AdbDaemonTransport, 'authenticate', async () => authenticated);
  const transport = new WebUsbTransport({credentials: {}});
  await assert.rejects(transport.connect({serial: 'fixture', raw, connect: async () => ({})}),
    error => error.code === 'connection_failed' && error.message.includes(failure));
  await nextTurn();
  assert.equal(authenticatedCloses, 1);
  assert.equal(rawCloses, 1);
  assert.equal(raw.opened, false);
  assert.equal(transport.adb, null);
  assert.equal(transport.device, null);
});

for (const stalled of ['authenticated', 'raw']) test(`failed setup retains USB ownership until ${stalled} cleanup settles`, async context => {
  const cleanup = deferred();
  let opens = 0, authenticatedCloses = 0, rawCloses = 0, outcome, supported = false;
  const raw = {opened: false, async close() {
    rawCloses++;
    if (stalled === 'raw') await cleanup.promise;
    raw.opened = false;
  }};
  const device = {serial: 'fixture', raw, async connect() {opens++; raw.opened = true; return {};}};
  context.mock.method(AdbDaemonTransport, 'authenticate', async () => ({
    clientFeatures: supported ? [AdbFeature.ShellV2] : [],
    banner: {features: supported ? [AdbFeature.ShellV2] : []},
    disconnected: new Promise(() => {}),
    async close() {
      authenticatedCloses++;
      if (stalled === 'authenticated') await cleanup.promise;
    },
  }));
  const transport = new WebUsbTransport({credentials: {}});
  const competitor = new WebUsbTransport({credentials: {}});
  const result = transport.connect(device).catch(error => {outcome = error;});
  try {
    await nextTurn();
    assert.equal(outcome?.code, 'connection_failed', 'Failed setup must not wait for cleanup');
    assert.match(outcome.message, /shell protocol/);
    assert.equal(authenticatedCloses, 1);
    assert.equal(rawCloses, 1);
    await assert.rejects(transport.connect(device), error => error.code === 'usb_busy');
    await assert.rejects(competitor.connect(device), error => error.code === 'usb_busy');
    assert.equal(opens, 1, 'Neither instance may reopen USB while old cleanup can still close it');
    cleanup.resolve();
    await nextTurn();
    assert.equal(raw.opened, false);
    supported = true;
    await transport.connect(device);
    assert.equal(opens, 2, 'Retry must be allowed once all cleanup settles');
    assert.ok(transport.adb);
    assert.equal(transport.device, device);
    assert.equal(raw.opened, true);
  } finally {
    cleanup.resolve();
    await result;
    await transport.close();
    await competitor.close();
  }
});

for (const stop of ['deadline', 'abort']) test(`stalled credentials release the real authenticator on ${stop}`, async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const credential = deferred(), abort = new AbortController();
  let reader, outcome, closes = 0;
  const connection = {
    readable: new ReadableStream({start(controller) {
      reader = controller;
      controller.enqueue({command: AdbCommand.Auth, arg0: AdbAuthType.Token,
        arg1: 0, payload: new Uint8Array(20)});
    }}),
    writable: new Consumable.WritableStream({write() {}}),
  };
  const raw = {opened: true, async close() {
    closes++;
    if (raw.opened) {raw.opened = false; reader.error(Error('USB closed'));}
  }};
  const transport = new WebUsbTransport({credentials: {async *iterateKeys() {await credential.promise;}}});
  const messages = [];
  const result = transport.connect({serial: 'fixture', raw, connect: async () => connection},
    abort.signal, message => messages.push(message)).then(() => {outcome = 'resolved';}, error => {outcome = error;});
  try {
    await nextTurn();
    assert.match(messages.at(-1), /saved USB debugging key/);
    if (stop === 'deadline') context.mock.timers.tick(60000);
    else abort.abort(new DOMException('Stopped by user', 'AbortError'));
    await nextTurn();
    assert.ok(outcome instanceof Error, 'The pending credential must not keep connect waiting');
    assert.equal(outcome.code, stop === 'deadline' ? 'credential_timeout' : 'connection_failed');
    assert.ok(closes > 0);
    assert.equal(transport.adb, null);
    assert.equal(transport.device, null);
  } finally {
    credential.reject(Error('Release the synthetic credential'));
    await result;
  }
});

test('timed-out opening cannot overlap a retry or authenticate after it completes late', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const opening = deferred(), raw = {opened: true, async close() {raw.opened = false;}};
  let opens = 0, outcome;
  const device = {serial: 'fixture', raw, connect: () => {opens++; return opening.promise;}};
  const transport = new WebUsbTransport();
  context.mock.method(AdbDaemonTransport, 'authenticate', () => assert.fail('Late open must not authenticate'));
  const result = transport.connect(device).catch(error => {outcome = error;});
  await nextTurn();
  context.mock.timers.tick(60000);
  await nextTurn();
  assert.equal(outcome?.code, 'usb_timeout');
  await assert.rejects(transport.connect(device), error => error.code === 'usb_busy');
  assert.equal(opens, 1);
  raw.opened = true;
  opening.resolve({});
  await nextTurn();
  assert.equal(raw.opened, false);
  assert.equal(transport.adb, null);
  device.connect = async () => {throw Error('Retry reached USB');};
  await assert.rejects(transport.connect(device), /Retry reached USB/);
  await result;
});

test('late authentication is closed without replacing another device connection', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const authentication = deferred();
  context.mock.method(AdbDaemonTransport, 'authenticate', () => authentication.promise);
  const raw = {opened: true, async close() {raw.opened = false;}};
  const transport = new WebUsbTransport({credentials: {}});
  let outcome, lateClosed = 0;
  const result = transport.connect({serial: 'old', raw, connect: async () => ({})}).catch(error => {outcome = error;});
  await nextTurn();
  context.mock.timers.tick(60000);
  await nextTurn();
  assert.equal(outcome?.code, 'usb_timeout');
  const replacement = {serial: 'new'};
  transport.device = replacement;
  authentication.resolve({close: async () => {lateClosed++;}});
  await nextTurn();
  assert.equal(lateClosed, 1);
  assert.equal(transport.device, replacement);
  assert.equal(transport.adb, null);
  await result;
});

test('a hung USB close does not keep timeout recovery waiting', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const opening = deferred(), closing = deferred();
  const transport = new WebUsbTransport();
  let outcome;
  const result = transport.connect({serial: 'fixture', raw: {opened: true, close: () => closing.promise},
    connect: () => opening.promise}).catch(error => {outcome = error;});
  await nextTurn();
  context.mock.timers.tick(60000);
  await nextTurn();
  assert.equal(outcome?.code, 'usb_timeout');
  opening.reject(Error('Released open')); closing.resolve();
  await result;
});

for (const stop of ['deadline', 'abort']) test(`real shell socket startup respects ${stop} before OPEN completes`, async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const opening = deferred(), abort = new AbortController();
  const adb = {createSocket: () => opening.promise, canUseFeature: () => true};
  const transport = new WebUsbTransport();
  transport.adb = {subprocess: {shellProtocol: new AdbShellProtocolSubprocessService(adb)}};
  let outcome, lateClosed = 0;
  const result = transport.shell(['getprop', 'ro.product.model'], {timeout: 15000, signal: abort.signal})
    .then(() => {outcome = 'resolved';}, error => {outcome = error;});
  await nextTurn();
  if (stop === 'deadline') context.mock.timers.tick(15000);
  else abort.abort(new DOMException('Stopped by user', 'AbortError'));
  await nextTurn();
  assert.ok(outcome instanceof Error, 'Socket OPEN must not keep the command waiting');
  assert.match(outcome.message, stop === 'deadline' ? /did not respond/ : /Stopped by user/);
  assert.equal(transport.adb, null);
  let reader;
  opening.resolve({
    readable: new ReadableStream({start(controller) {reader = controller;}}),
    writable: new WritableStream({write() {}}),
    close: async () => {lateClosed++; reader.close();},
  });
  await nextTurn();
  assert.equal(lateClosed, 1);
  await result;
});

for (const stop of ['disconnect', 'deadline', 'abort']) test(`real shell cleanup consumes a rejected socket close after ${stop}`, async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const abort = new AbortController(), transport = new WebUsbTransport();
  let reader, closed = false, closes = 0;
  const socket = {
    readable: new ReadableStream({start(controller) {reader = controller;}}),
    writable: new WritableStream({write() {}}),
    async close() {
      if (closed) return;
      closed = true; closes++;
      reader.error(Error('USB disconnected'));
      throw Error('CLSE write rejected: USB disconnected');
    },
  };
  transport.adb = {subprocess: {shellProtocol: new AdbShellProtocolSubprocessService({
    createSocket: async () => socket, canUseFeature: () => true,
  })}};
  const result = transport.shell(['getprop'], {timeout: 15000, signal: abort.signal}).catch(error => error);
  await nextTurn();
  if (stop === 'disconnect') reader.error(Error('USB disconnected'));
  else if (stop === 'deadline') context.mock.timers.tick(15000);
  else abort.abort(new DOMException('Stopped by user', 'AbortError'));
  const error = await result;
  assert.ok(error instanceof Error);
  assert.match(error.message, stop === 'disconnect' ? /USB disconnected|Socket ended without exit message/
    : stop === 'deadline' ? /did not respond/ : /Stopped by user/);
  // Let node:test observe any unhandled rejection from Tango's socket cleanup.
  await nextTurn(); await nextTurn();
  assert.equal(closes, 1);
});

test('an old shell deadline cannot close a replacement connection', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const opening = deferred(), transport = new WebUsbTransport();
  transport.adb = {subprocess: {shellProtocol: {spawn: () => opening.promise}}};
  let outcome;
  const result = transport.shell(['getprop'], {timeout: 15000}).catch(error => {outcome = error;});
  await nextTurn();
  const replacement = {close: () => assert.fail('The replacement connection must remain open')};
  transport.adb = replacement;
  context.mock.timers.tick(15000);
  await nextTurn();
  assert.match(outcome?.message, /did not respond/);
  assert.equal(transport.adb, replacement);
  opening.reject(Error('Release old socket'));
  await result;
});
