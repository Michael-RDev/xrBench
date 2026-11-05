import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {AdbDaemonTransport, AdbFeature} from '@yume-chan/adb';
import {AdbDaemonWebUsbDevice} from '@yume-chan/adb-daemon-webusb';
import {WebUsbTransport} from '../web/browser/transport.mjs';

function deferred() {
  let resolve;
  const promise = new Promise(yes => {resolve = yes;});
  return {promise, resolve};
}
function authenticated(serial, close = async () => {}) {
  return {serial, clientFeatures: [AdbFeature.ShellV2], banner: {features: [AdbFeature.ShellV2]},
    disconnected: new Promise(() => {}), close};
}
function device(serial) {
  const raw = {serialNumber: serial, opened: false, async close() {this.opened = false;}};
  return {serial, raw, opens: 0, async connect() {this.opens++; raw.opened = true; return {device: this};}};
}

for (const stalled of ['authenticated', 'raw']) test(`immediate close retains USB ownership until ${stalled} cleanup finishes`, async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const cleanup = deferred(), headset = device('fixture');
  const closeRaw = headset.raw.close;
  let authentications = 0;
  context.mock.method(headset.raw, 'close', async function() {
    if (stalled === 'raw' && authentications === 1) await cleanup.promise;
    await closeRaw.call(this);
  });
  context.mock.method(AdbDaemonTransport, 'authenticate', async ({serial}) => {
    const attempt = ++authentications;
    return authenticated(serial, async () => {
      if (stalled === 'authenticated' && attempt === 1) await cleanup.promise;
      await headset.raw.close();
    });
  });
  const transport = new WebUsbTransport({credentials: {}});
  const competitor = new WebUsbTransport({credentials: {}});
  try {
    await transport.connect(headset);
    // Close directly at the public completion boundary, before setup's cleanup reactions run.
    const closing = transport.close();
    await nextTurn();
    context.mock.timers.tick(1000);
    await closing;
    await assert.rejects(transport.connect(headset), error => error.code === 'usb_busy');
    await assert.rejects(competitor.connect(headset), error => error.code === 'usb_busy');
    assert.equal(headset.opens, 1);
    cleanup.resolve();
    await nextTurn();
    await transport.connect(headset);
    assert.equal(headset.opens, 2);
    await nextTurn();
    assert.equal(headset.raw.opened, true, 'Old cleanup must not close the replacement connection');
    assert.equal(transport.device, headset);
    assert.ok(transport.adb);
  } finally {
    cleanup.resolve();
    await transport.close();
    await competitor.close();
  }
});

test('closing pending setup retains ownership of every raw USB close', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const opening = deferred(), cleanup = deferred(), headset = device('fixture');
  let closes = 0;
  context.mock.method(headset, 'connect', () => {headset.opens++; headset.raw.opened = true; return opening.promise;});
  context.mock.method(headset.raw, 'close', async () => {
    // Abort starts one close; explicit close starts another while USB is still open.
    if (++closes === 1) await Promise.resolve();
    else await cleanup.promise;
    headset.raw.opened = false;
  });
  context.mock.method(AdbDaemonTransport, 'authenticate', () => assert.fail('Canceled setup must not authenticate'));
  const transport = new WebUsbTransport({credentials: {}});
  const connecting = transport.connect(headset);
  const rejected = assert.rejects(connecting, /Connection closed/);
  let closing;
  try {
    await nextTurn();
    closing = transport.close();
    await nextTurn();
    opening.resolve({});
    await rejected;
    await nextTurn();
    context.mock.timers.tick(1000);
    await closing;
    assert.equal(closes, 2);
    await assert.rejects(transport.connect(headset), error => error.code === 'usb_busy');
    assert.equal(headset.opens, 1);
  } finally {
    cleanup.resolve(); opening.resolve({});
    await rejected;
    await closing;
    await transport.close();
  }
});

test('overlapping connects reserve the instance before closing or opening USB', async context => {
  const authentication = deferred(), first = device('first'), second = device('second');
  context.mock.method(AdbDaemonTransport, 'authenticate', async ({serial, connection}) => {
    if (serial === 'first') await authentication.promise;
    return authenticated(serial, () => connection.device.raw.close());
  });
  const transport = new WebUsbTransport({credentials: {}});
  const connecting = transport.connect(first);
  const overlapping = transport.connect(second);
  try {
    await assert.rejects(overlapping, error => error.code === 'usb_busy');
    assert.equal(second.opens, 0);
    authentication.resolve();
    await connecting;
    assert.equal(transport.device, first);
    assert.equal(transport.adb.serial, first.serial);
    // The instance reservation must also release after a successful request.
    await transport.connect(second);
    assert.equal(second.opens, 1);
    assert.equal(first.raw.opened, false);
    assert.equal(transport.device, second);
    assert.equal(transport.adb.serial, second.serial);
  } finally {
    authentication.resolve();
    await Promise.allSettled([connecting, overlapping]);
    await transport.close();
  }
});

test('late authentication after a deadline cannot replace a different headset connection', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const authentication = deferred(), first = device('first'), second = device('second');
  context.mock.method(AdbDaemonTransport, 'authenticate', async ({serial, connection}) => {
    if (serial === first.serial) await authentication.promise;
    return authenticated(serial, () => connection.device.raw.close());
  });
  const transport = new WebUsbTransport({credentials: {}});
  const timedOut = assert.rejects(transport.connect(first), error => error.code === 'usb_timeout');
  try {
    await nextTurn();
    context.mock.timers.tick(60000);
    await timedOut;
    await transport.connect(second);
    authentication.resolve();
    await nextTurn();
    assert.equal(first.raw.opened, false);
    assert.equal(second.raw.opened, true);
    assert.equal(transport.device, second);
    assert.equal(transport.adb.serial, second.serial);
  } finally {
    authentication.resolve();
    await timedOut;
    await transport.close();
  }
});

test('close before the first USB open cancels the reserved connection request', async context => {
  const headset = device('fixture'), transport = new WebUsbTransport({credentials: {}});
  context.mock.method(AdbDaemonTransport, 'authenticate', async () => authenticated(headset.serial));
  const connecting = transport.connect(headset);
  await transport.close();
  await assert.rejects(connecting, /Connection closed/);
  assert.equal(headset.opens, 0);
  assert.equal(transport.device, null);
  assert.equal(transport.adb, null);
  // Cancellation must not leave the instance permanently busy.
  await transport.connect(headset);
  assert.equal(headset.opens, 1);
  await transport.close();
});

test('connection reuse requires the same raw USB device, even when serials match', async context => {
  const first = device('same'), second = device('same');
  context.mock.method(AdbDaemonTransport, 'authenticate', async ({serial, connection}) =>
    authenticated(serial, () => connection.device.raw.close()));
  const transport = new WebUsbTransport({credentials: {}});
  try {
    await transport.connect(first);
    const current = transport.adb;
    await transport.connect({...first, connect: () => assert.fail('The same USB device must be reused')});
    assert.equal(transport.adb, current);
    await transport.connect(second);
    assert.equal(first.raw.opened, false);
    assert.equal(second.raw.opened, true);
    assert.equal(second.opens, 1);
    assert.equal(transport.device, second);
    assert.notEqual(transport.adb, current);
  } finally {
    await transport.close();
  }
});

for (const serialNumber of [null, '']) test(`USB devices with ${JSON.stringify(serialNumber)} serials cannot use a model ID for recovery`, async context => {
  const raw = () => ({serialNumber, vendorId: 0x2833, productId: 0x0183});
  const first = new AdbDaemonWebUsbDevice(raw(), {}, {});
  const second = new AdbDaemonWebUsbDevice(raw(), {}, {});
  assert.equal(first.serial, second.serial, 'Tango falls back to vendor/product IDs');
  context.mock.method(first, 'connect', () => assert.fail('An unstable identity must be rejected before opening USB'));
  context.mock.method(second, 'connect', () => assert.fail('An unstable identity must be rejected before opening USB'));
  const previous = globalThis.isSecureContext;
  globalThis.isSecureContext = true;
  try {
    const transport = new WebUsbTransport({manager: {requestDevice: async () => second}, credentials: {}});
    await assert.rejects(transport.connect(first), /stable USB identity/);
    await assert.rejects(transport.choose(), /stable USB identity/);
    transport.devices = [first];
    await assert.rejects(transport.choose(first.serial), /stable USB identity/);
    assert.equal(transport.device, null);
    assert.equal(transport.adb, null);
  } finally {
    globalThis.isSecureContext = previous;
  }
});
