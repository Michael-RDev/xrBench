import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AdbCommand, AdbAuthType, AdbDaemonTransport, ADB_DEFAULT_AUTHENTICATORS, AdbPacketHeader} from '@yume-chan/adb';
import {AdbDaemonWebUsbConnection} from '@yume-chan/adb-daemon-webusb';
import {Consumable} from '@yume-chan/stream-extra';
import {WebUsbTransport} from '../web/browser/transport.mjs';

const flush = async () => {for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve));};
const deferred = () => {let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject};};
const packet = (command, arg0 = 0, arg1 = 0, payload = new Uint8Array()) => ({command, arg0, arg1, payload});
const connected = () => packet(AdbCommand.Connect, 0x01000001, 1048576, new TextEncoder().encode('device::features=shell_v2;'));
function publicKey(context) {
  const original = ADB_DEFAULT_AUTHENTICATORS.slice();
  ADB_DEFAULT_AUTHENTICATORS.splice(0, ADB_DEFAULT_AUTHENTICATORS.length, async function* () {
    yield packet(AdbCommand.Auth, AdbAuthType.PublicKey, 0, new TextEncoder().encode('PRIVATE TEST PAYLOAD\0'));
  });
  context.after(() => ADB_DEFAULT_AUTHENTICATORS.splice(0, ADB_DEFAULT_AUTHENTICATORS.length, ...original));
}
function fixture({write = () => {}, closeSettlesRead = true, close = null, open = null} = {}) {
  let input, opens = 0, closes = 0;
  const writes = [], trace = [], messages = [];
  const connection = {readable: new ReadableStream({start(controller) {input = controller;}}, {highWaterMark: 0}),
    writable: new Consumable.WritableStream({async write(p) {writes.push(p.command); await write(p, receive);}})};
  const receive = p => input.enqueue(p);
  const raw = {opened: false, async close() {
    closes++; if (close) await close(); raw.opened = false;
    if (closeSettlesRead) {try {input.error(Error('USB closed'));} catch {}}
  }};
  const device = {serial: 'DO NOT LOG THIS SERIAL', raw, async connect() {opens++; await open?.(); raw.opened = true; return connection;}};
  const transport = new WebUsbTransport({credentials: {}});
  return {device, raw, transport, trace, messages, writes, receive, connection,
    failRead: () => input.error(Error('USB finally closed')),
    get opens() {return opens;}, get closes() {return closes;},
    connect: signal => transport.connect(device, signal, text => messages.push(text), event => trace.push(event)),
  };
}

test('real authentication waits for consumed public-key writes before approval, then records CNXN and completion', async context => {
  context.mock.timers.enable({apis: ['setTimeout']}); publicKey(context);
  const delivery = deferred();
  const f = fixture({write: async (p, receive) => {
    if (p.command === AdbCommand.Connect) receive(packet(AdbCommand.Auth, AdbAuthType.Token));
    else await delivery.promise;
  }});
  const connecting = f.connect(); await flush();
  assert.match(f.messages.at(-1), /sending to the headset/);
  assert.equal(f.trace.some(e => e.command === 'AUTH' && e.event === 'packet_write_complete'), false);
  assert.equal(f.messages.some(m => /allow USB debugging inside/i.test(m)), false);
  context.mock.timers.tick(59999); assert.equal(f.raw.opened, true);
  delivery.resolve(); await flush();
  assert.match(f.messages.at(-1), /Browser key sent/);
  context.mock.timers.tick(60000); assert.equal(f.raw.opened, true, 'approval gets a separate three-minute window');
  f.receive(connected()); await connecting;
  const keyWrite = f.trace.findIndex(e => e.command === 'AUTH' && e.event === 'packet_write_complete');
  const cnxn = f.trace.findIndex(e => e.command === 'CNXN' && e.event === 'packet_received');
  const authenticated = f.trace.findIndex(e => e.event === 'authenticate_complete');
  assert.ok(keyWrite >= 0 && cnxn > keyWrite && authenticated > cnxn);
  assert.equal(f.trace.at(-1).event, 'connected');
  assert.match(f.messages.at(-1), /finishing browser authentication/);
  assert.doesNotMatch(JSON.stringify(f.trace), /PRIVATE TEST PAYLOAD|DO NOT LOG THIS SERIAL|payload|checksum/);
  for (const e of f.trace) assert.ok(Object.keys(e).every(k => ['attempt', 'event', 'stage', 'elapsed_ms', 'command', 'auth_type'].includes(k)));
  await f.transport.close();
});

test('a public key whose write stalls retains the setup deadline and never reports approval', async context => {
  context.mock.timers.enable({apis: ['setTimeout']}); publicKey(context);
  const f = fixture({write: (p, receive) => {
    if (p.command === AdbCommand.Connect) receive(packet(AdbCommand.Auth, AdbAuthType.Token));
    else return new Promise(() => {});
  }});
  const result = assert.rejects(f.connect(), error => error.code === 'usb_timeout' && /Sending/.test(error.message));
  await flush(); context.mock.timers.tick(60000); await result;
  assert.equal(f.trace.some(e => e.stage === 'approval'), false);
  assert.equal(f.transport.adb, null);
  await f.transport.close();
});

test('deadline rejects even when raw close and real authentication never settle; retry retains USB ownership', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const f = fixture({close: () => new Promise(() => {})});
  const result = assert.rejects(f.connect(), error => error.code === 'usb_timeout');
  await flush(); context.mock.timers.tick(60000); await result;
  await f.transport.close();
  await assert.rejects(f.connect(), error => error.code === 'usb_busy' && /still closing/.test(error.message));
  assert.equal(f.opens, 1); assert.equal(f.transport.adb, null); assert.equal(f.closes, 1);
});

test('an unsettled authentication stays quarantined after close, then releases ownership when it finally rejects', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const f = fixture({closeSettlesRead: false});
  const result = assert.rejects(f.connect(), error => error.code === 'usb_timeout');
  await flush(); context.mock.timers.tick(60000); await result; await flush();
  assert.equal(f.raw.opened, false);
  await assert.rejects(f.connect(), error => error.code === 'usb_busy');
  assert.equal(f.opens, 1);
  f.failRead(); await flush();
  assert.equal(f.transport._connectionAttempt, null); assert.equal(f.transport._usbCleanup, null);
  const next = fixture({write(p, receive) {if (p.command === AdbCommand.Connect) receive(connected());}});
  await f.transport.connect(next.device);
  await flush(); assert.equal(next.raw.opened, true);
  await f.transport.close();
});

test('abort during opening returns immediately and closes a late open before permitting retry', async () => {
  const opening = deferred(), abort = new AbortController();
  const f = fixture({open: () => opening.promise});
  const result = assert.rejects(f.connect(abort.signal), {name: 'AbortError'});
  await flush(); abort.abort(); await result;
  await assert.rejects(f.connect(), error => error.code === 'usb_busy');
  opening.resolve(); await flush();
  assert.equal(f.raw.opened, false); assert.equal(f.closes, 2);
  assert.equal(f.transport.adb, null); assert.equal(f.transport._connectionAttempt, null);
});

test('a failed close of an existing transport cannot be bypassed by choosing another device', async () => {
  const f = fixture();
  f.transport.device = {serial: 'previous', raw: {opened: true, close: async () => {throw Error('close failed');}}};
  f.transport.adb = {close: async () => {}};
  await assert.rejects(f.connect(), error => error.code === 'usb_busy');
  await flush();
  assert.equal(f.opens, 0);
  await assert.rejects(f.connect(), error => error.code === 'usb_busy');
  assert.equal(f.opens, 0);
});

test('abort during authentication cannot adopt a late transport or let its cleanup close a newer attempt', async context => {
  const late = deferred(), cleanup = deferred(), abort = new AbortController();
  let lateCloses = 0;
  context.mock.method(AdbDaemonTransport, 'authenticate', () => late.promise);
  const f = fixture();
  const result = assert.rejects(f.connect(abort.signal), {name: 'AbortError'});
  await flush(); abort.abort(); await result;
  late.resolve({close: async () => {lateCloses++; await cleanup.promise;}}); await flush();
  assert.equal(f.transport.adb, null); assert.equal(lateCloses, 1);
  await assert.rejects(f.connect(), error => error.code === 'usb_busy');
  cleanup.resolve(); await flush();
  assert.equal(f.transport._usbCleanup, null); assert.equal(f.transport._connectionAttempt, null);
});

test('CNXN followed by stalled authenticate completion reports transport finalization instead of another approval', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const actual = AdbDaemonTransport.authenticate;
  context.mock.method(AdbDaemonTransport, 'authenticate', async options => {
    const hidden = await actual.call(AdbDaemonTransport, options);
    hidden.disconnected.catch(() => {}); // The injected stall deliberately hides this transport from the caller.
    return new Promise(() => {});
  });
  const f = fixture({write(p, receive) {if (p.command === AdbCommand.Connect) receive(connected());}});
  const result = assert.rejects(f.connect(), error => error.code === 'usb_timeout' && /CNXN/.test(error.message));
  await flush(); context.mock.timers.tick(60000); await result;
  assert.match(f.messages.at(-1), /finishing browser authentication/);
  assert.equal(f.trace.some(e => e.event === 'authenticate_complete'), false);
});

test('packet write rejection fails authentication without claiming delivery or recording error payloads', async context => {
  publicKey(context);
  const f = fixture({write(p, receive) {
    if (p.command === AdbCommand.Connect) receive(packet(AdbCommand.Auth, AdbAuthType.Token));
    else throw Error('PRIVATE ERROR PAYLOAD');
  }});
  await assert.rejects(f.connect()); await flush();
  assert.ok(f.trace.some(e => e.event === 'packet_write_failed' && e.command === 'AUTH'));
  assert.equal(f.trace.some(e => e.stage === 'approval'), false);
  assert.doesNotMatch(JSON.stringify(f.trace), /PRIVATE ERROR PAYLOAD/);
});

test('the real WebUSB serializer preserves separate header/payload writes and finishes authentication', async context => {
  publicKey(context);
  const queue = [], pending = [], lengths = [];
  let header;
  const incoming = p => {
    const h = new Uint8Array(24);
    AdbPacketHeader.serialize({...p, payloadLength: p.payload.length, checksum: 0, magic: p.command ^ 0xffffffff}, h);
    for (const bytes of [h, p.payload].filter(b => b.length)) {
      const result = {status: 'ok', data: new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)};
      if (pending.length) pending.shift().resolve(result); else queue.push(result);
    }
  };
  const raw = {opened: false,
    transferIn() {if (queue.length) return Promise.resolve(queue.shift()); const d = deferred(); pending.push(d); return d.promise;},
    async transferOut(endpoint, bytes) {
      lengths.push(bytes.length);
      if (bytes.length === 24 && !header) {header = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(0, true); return {status: 'ok', bytesWritten: bytes.length};}
      if (!bytes.length) return {status: 'ok', bytesWritten: 0};
      const command = header; header = null;
      incoming(command === AdbCommand.Connect ? packet(AdbCommand.Auth, AdbAuthType.Token, 0, new Uint8Array(20)) : connected());
      return {status: 'ok', bytesWritten: bytes.length};
    },
    async close() {raw.opened = false; for (const p of pending.splice(0)) p.reject(Error('USB closed'));},
  };
  const device = {serial: 'simulation', raw, async connect() {
    raw.opened = true;
    return new AdbDaemonWebUsbConnection(device, {endpointNumber: 1, packetSize: 64}, {endpointNumber: 2, packetSize: 64},
      {addEventListener() {}, removeEventListener() {}});
  }};
  const t = new WebUsbTransport({credentials: {}}), trace = [];
  await t.connect(device, undefined, () => {}, entry => trace.push(entry));
  assert.equal(lengths[0], 24); assert.equal(lengths[2], 24);
  assert.equal(trace.at(-1).event, 'connected');
  await t.close(); assert.equal(raw.opened, false);
});
