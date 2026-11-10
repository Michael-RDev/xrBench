import {Adb, AdbDaemonTransport, ADB_DEFAULT_AUTHENTICATORS, AdbAuthType} from '@yume-chan/adb';
import {AdbDaemonWebUsbDevice, AdbDaemonWebUsbDeviceManager} from '@yume-chan/adb-daemon-webusb';
import AdbWebCredentialStore from '@yume-chan/adb-credential-web';
import {sha256, runPattern, EvidenceError} from './evidence.mjs';

const quote = value => "'" + String(value).replaceAll("'", "'\\''") + "'";
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const pendingUsb = new WeakMap();
const quiet = action => {try {return Promise.resolve(action()).catch(() => {});} catch {return Promise.resolve();}};
function retainUsb(raw, cleanup) {
  const settled = Promise.all([pendingUsb.get(raw), cleanup]);
  pendingUsb.set(raw, settled);
  void settled.then(() => {if (pendingUsb.get(raw) === settled) pendingUsb.delete(raw);});
  return settled;
}
function requireDeviceIdentity(device) {
  // Tango substitutes vendor/product IDs when the USB serial is absent. Those
  // identify a model, not a headset, and cannot safely recover a saved run.
  if (!device.serial || (device instanceof AdbDaemonWebUsbDevice && !device.raw.serialNumber))
    throw new EvidenceError('This headset has no stable USB identity and cannot be safely recovered.');
  return device;
}
// USB, IndexedDB, and Tango socket-open promises do not all observe AbortSignal.
// Always consume their eventual result, but stop awaiting them at our deadline.
export function abortable(task, signal) {
  return new Promise((resolve, reject) => {
    const finish = callback => value => {signal.removeEventListener('abort', abort); callback(value);};
    const abort = () => finish(reject)(signal.reason);
    Promise.resolve(task).then(finish(resolve), finish(reject));
    signal.addEventListener('abort', abort, {once: true});
    if (signal.aborted) abort();
  });
}
export class MissingFile extends Error {}
export class ConnectionError extends Error {
  constructor(code, message, cause) {super(message, {cause}); this.name = 'ConnectionError'; this.code = code;}
}
export function connectionError(error) {
  if (error instanceof ConnectionError) return error;
  const causes = new Set();
  for (let cause = error; cause && !causes.has(cause); cause = cause.cause) {
    causes.add(cause);
    if (cause instanceof ConnectionError) return cause;
    if (cause instanceof AdbDaemonWebUsbDevice.DeviceBusyError || cause.name === 'DeviceBusyError'
        || /busy|claim|already.*open|already in use(?:d)?|USB interface is in use|another xrBench tab/i.test(cause.message || ''))
      return new ConnectionError('usb_busy', 'The USB interface is in use. Close other headset tools (such as Unity, Meta Quest Developer Hub, SideQuest, or ADB) and other headset browser tabs, then select Retry connection.', error);
    if (cause.name === 'NotFoundError') return new ConnectionError('not_selected', 'No headset selected. Connect a USB data cable, enable headset developer mode, then press Run.', error);
    if (cause.name === 'NotAllowedError' || cause.name === 'SecurityError') return new ConnectionError('permission_denied', 'USB permission was denied. Allow this website to access the headset, then retry.', error);
  }
  if (/Unable to open|Access denied/i.test(error?.message || '')) return new ConnectionError('connection_failed', 'The browser could not open the headset USB interface. Check the cable and device USB driver, then retry.', error);
  return new ConnectionError('connection_failed', error?.message || 'Headset connection failed. Reconnect the USB cable and retry.', error);
}
export const connectionMessage = error => connectionError(error).message;
async function collect(stream, limit) {
  const reader = stream.getReader(), chunks = []; let length = 0;
  try {
    while (true) {
      const {value, done} = await reader.read(); if (done) break;
      length += value.byteLength;
      if (length > limit) throw new EvidenceError('Native response exceeds the size limit');
      chunks.push(value);
    }
  } catch (error) {await reader.cancel().catch(() => {}); throw error;}
  finally {reader.releaseLock();}
  const result = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) {result.set(chunk, offset); offset += chunk.length;}
  return new TextDecoder().decode(result);
}
export class WebUsbTransport {
  constructor({manager = AdbDaemonWebUsbDeviceManager.BROWSER, fetcher = globalThis.fetch.bind(globalThis), credentials} = {}) {
    this.manager = manager; this.fetcher = fetcher; this.credentials = credentials;
    this.devices = []; this.adb = null; this.device = null;
  }
  get supported() {return !!this.manager && !!globalThis.isSecureContext;}
  async discover() {this.devices = this.manager ? await this.manager.getDevices() : []; return this.devices;}
  choose(serial) {
    if (!this.supported) return Promise.reject(Error('Use desktop Chrome or Edge on the HTTPS website to connect a headset.'));
    const known = this.devices.find(d => d.serial === serial) || (!serial && this.devices.length === 1 ? this.devices[0] : null);
    // Invoke the chooser before any await: WebUSB requires the Run click's activation.
    return known ? Promise.resolve(known).then(requireDeviceIdentity) : this.manager.requestDevice().then(device => {
      if (!device) throw new DOMException('No headset selected', 'NotFoundError');
      return requireDeviceIdentity(device);
    });
  }
  async connect(device, signal, message = () => {}) {
    signal?.throwIfAborted();
    requireDeviceIdentity(device);
    if (this.connecting) throw new ConnectionError('usb_busy',
      'A headset connection is already in progress. Wait for it to finish or stop it before retrying.');
    if (this.adb && this.device?.raw === device.raw) return;
    // Reserve the instance before close() yields, so two requests cannot both
    // authenticate and publish connections for different headsets.
    const attempt = new AbortController();
    this.connecting = attempt;
    const cancel = () => attempt.abort(signal.reason);
    signal?.addEventListener('abort', cancel, {once: true});
    try {
      await this.#connect(device, attempt, message);
      attempt.signal.throwIfAborted();
    } catch (error) {
      if (this.connecting === attempt) {
        const adb = this.adb;
        this.adb = null; this.device = null;
        if (adb) retainUsb(device.raw, quiet(() => adb.close()));
      }
      throw connectionError(error);
    } finally {
      signal?.removeEventListener('abort', cancel);
      if (this.connecting === attempt) this.connecting = null;
    }
  }
  async #connect(device, attempt, message) {
    await this.#close(attempt);
    attempt.signal.throwIfAborted();
    if (pendingUsb.has(device.raw)) throw new ConnectionError('usb_busy',
      'The previous USB connection is still closing. Reconnect the USB cable or reload this tab, then retry.');
    this.device = device;
    let stage = 'opening', closing;
    const closeRaw = () => closing ||= quiet(() => device.raw.close()).finally(() => {closing = null;});
    attempt.signal.addEventListener('abort', () => {retainUsb(device.raw, closeRaw());}, {once: true});
    const progress = (next, text) => {attempt.signal.throwIfAborted(); stage = next; message(text);};
    const expire = () => {
      const detail = stage === 'approval'
        ? ['approval_timeout', 'USB debugging approval timed out. Put on the headset, allow USB debugging, then retry.']
        : stage === 'credentials'
          ? ['credential_timeout', 'The browser could not read or prepare its USB debugging key. Reload this website, then retry.']
          : ['usb_timeout', stage === 'opening'
            ? 'Opening the headset USB interface timed out. Close other headset tools, reconnect the USB cable, then retry.'
            : 'The headset did not finish the USB handshake. Reconnect the USB cable and retry.'];
      attempt.abort(new ConnectionError(...detail));
    };
    let timer = setTimeout(expire, 60000);
    // Register the settlement promise before any callback can close this request.
    const opening = Promise.resolve().then(async () => {
      progress('opening', 'Opening the headset USB interface…');
      const connection = await device.connect();
      attempt.signal.throwIfAborted();
      progress('handshake', 'USB opened · waiting for the headset to respond…');
      const credentials = this.credentials ||= new AdbWebCredentialStore('xrBench@' + location.host);
      const credentialStore = {
        async *iterateKeys() {
          progress('credentials', 'Reading this browser’s saved USB debugging key…');
          const keys = credentials.iterateKeys()[Symbol.asyncIterator]();
          try {
            while (true) {
              const {value: key, done} = await abortable(keys.next(), attempt.signal);
              if (done) break;
              progress('handshake', 'Checking this browser’s USB debugging permission…');
              yield key;
            }
          } finally {
            void quiet(() => keys.return?.());
          }
          progress('handshake', 'Checking this browser’s USB debugging permission…');
        },
        async generateKey() {
          progress('credentials', 'Preparing this browser’s USB debugging key…');
          const key = await abortable(credentials.generateKey(), attempt.signal);
          progress('handshake', 'Requesting USB debugging permission…');
          return key;
        },
      };
      const authenticators = ADB_DEFAULT_AUTHENTICATORS.map(authenticate => async function* (store, request) {
        for await (const packet of authenticate(store, request)) {
          attempt.signal.throwIfAborted();
          if (packet.arg0 === AdbAuthType.PublicKey) {
            // Start the human approval window only once the key is ready.
            // Reaching the Quest menu can take longer than the USB handshake.
            clearTimeout(timer); timer = setTimeout(expire, 180000);
            progress('approval', 'Allow USB debugging inside the headset for this website · waiting up to 3 minutes…');
          }
          yield packet;
        }
      });
      const transport = await AdbDaemonTransport.authenticate({serial: device.serial, connection,
        credentialStore, authenticators});
      try {
        attempt.signal.throwIfAborted();
        const current = new Adb(transport);
        if (!current.subprocess.shellProtocol) throw Error('This headset does not support the required Android shell protocol.');
        this.adb = current;
        current.disconnected.then(() => {if (this.adb === current) this.adb = null;}, () => {if (this.adb === current) this.adb = null;});
      } catch (error) {
        // Fail the caller and start raw USB cleanup immediately. This operation
        // retains ownership while the authenticated transport finishes closing.
        attempt.abort(error);
        await quiet(() => transport.close());
        throw error;
      }
    });
    // Keep ownership until late opening/authentication and cleanup settle. A stale
    // attempt must never close a new connection to the same raw USBDevice.
    const settled = opening.then(async () => {
      if (attempt.signal.aborted) await closeRaw();
    }, async () => {
      if (device.raw.opened || closing) await closeRaw();
    }).finally(() => {
      if (pendingUsb.get(device.raw) === settled) pendingUsb.delete(device.raw);
    });
    pendingUsb.set(device.raw, settled);
    try {
      await abortable(opening, attempt.signal);
      attempt.signal.throwIfAborted();
    } finally {clearTimeout(timer);}
  }
  async close() {await this.#close();}
  async #close(keepAttempt) {
    const adb = this.adb, device = this.device, attempt = this.connecting;
    this.adb = null; this.device = null;
    if (attempt !== keepAttempt) {
      this.connecting = null;
      attempt?.abort(new DOMException('Connection closed', 'AbortError'));
    }
    if (!adb && !device) return;
    // Join setup's existing ownership with every close operation. Setup may
    // settle before authenticated cleanup, including immediately after connect().
    let cleanup = Promise.all([quiet(() => adb?.close()), quiet(() => device?.raw.opened && device.raw.close())]);
    if (device) cleanup = retainUsb(device.raw, cleanup);
    let timer;
    try {await Promise.race([cleanup, new Promise(resolve => {timer = setTimeout(resolve, 1000);})]);}
    finally {clearTimeout(timer);}
  }
  async shell(args, {data, signal, timeout = 15000, limit = 64 * 1024 ** 2, allowFailure = false, onWrite} = {}) {
    const adb = this.adb;
    if (!adb) throw Error('Headset disconnected. Reconnect and retry to recover this run.');
    const abort = new AbortController(), cancel = () => abort.abort(signal.reason);
    signal?.throwIfAborted(); signal?.addEventListener('abort', cancel, {once: true});
    const timer = setTimeout(() => abort.abort(Error('The device did not respond. Keep USB connected and retry the connection.')), timeout);
    let process, completed = false;
    try {
      // Tango joins array entries with spaces; shell-quote every argument ourselves.
      // Own cancellation here: Tango's abort listener drops socket.close()
      // rejections. Our bounded waits and late cleanup consume those failures.
      const spawning = adb.subprocess.shellProtocol.spawn(args.map(quote));
      void Promise.resolve(spawning).then(late => {
        if (abort.signal.aborted) {void quiet(() => late.exited); void quiet(() => late.kill());}
      }, () => {});
      process = await abortable(spawning, abort.signal);
      const input = async () => {
        // Read-only commands need no stdin traffic. Their exit can precede a
        // CloseStdin acknowledgement, which Tango correctly rejects as closed.
        if (data === undefined) return;
        const writer = process.stdin.getWriter();
        try {
          if (data) for (let offset = 0; offset < data.length; offset += 65536) {
            abort.signal.throwIfAborted(); await writer.write(data.subarray(offset, offset + 65536));
            onWrite?.(Math.min(offset + 65536, data.length), data.length);
          }
          try {await writer.close();}
          catch (error) {
            // A size-delimited consumer (pm install -S) may exit after the last
            // byte. Accept only this close race and still require its exit frame.
            if (error?.message !== 'Socket closed') throw error;
            await process.exited;
          }
        } finally {writer.releaseLock();}
      };
      // Drain the command's diagnostic even if it rejects stdin early (for
      // example, an install failure before the APK has finished streaming).
      const [stdout, stderr, code, inputError] = await abortable(Promise.all([collect(process.stdout, limit), collect(process.stderr, 1024 ** 2), process.exited,
        input().then(() => null, error => error)]), abort.signal);
      abort.signal.throwIfAborted();
      if (code && !allowFailure) throw Error(stderr.trim() || stdout.trim() || 'Headset command failed');
      if (!code && inputError) throw inputError;
      completed = true; return {stdout, stderr, code};
    } catch (error) {
      if (abort.signal.aborted && this.adb === adb) void this.close();
      throw Error(`Android ${args.slice(0, 2).join(' ')}: ${error.message}`, {cause: error});
    } finally {
      clearTimeout(timer); signal?.removeEventListener('abort', cancel);
      if (!completed) {abort.abort(); void quiet(() => process?.kill());}
    }
  }
  async properties(signal) {
    const fields = {model: 'ro.product.model', sdk: 'ro.build.version.sdk', abi: 'ro.product.cpu.abilist',
      manufacturer: 'ro.product.manufacturer', product: 'ro.product.name', device: 'ro.product.device', soc_model: 'ro.soc.model', soc_manufacturer: 'ro.soc.manufacturer'};
    const result = {};
    for (const [field, prop] of Object.entries(fields)) result[field] = (await this.shell(['getprop', prop], {signal})).stdout.trim();
    result.sdk = Number(result.sdk); return result;
  }
  async installedHash(manifest, signal) {
    const path = (await this.shell(['pm', 'path', manifest.package], {signal, allowFailure: true})).stdout.trim().split('\n')[0]?.replace(/^package:/, '');
    if (!path?.startsWith('/data/app/')) return null;
    return (await this.shell(['sha256sum', path], {signal, timeout: 30000})).stdout.split(/\s+/)[0];
  }
  async install(manifest, manifestUrl, signal, message) {
    if (await this.installedHash(manifest, signal) === manifest.apk_sha256) return;
    message('Downloading headset runner…');
    const url = new URL(manifest.apk, manifestUrl);
    if (url.origin !== new URL(manifestUrl).origin) throw new EvidenceError('Runner must be hosted with this website');
    const response = await this.fetcher(url, {signal, cache: 'force-cache'});
    if (!response.ok) throw Error('Runner download failed. Check your connection and retry.');
    if (response.url && new URL(response.url).origin !== url.origin) throw new EvidenceError('Unexpected runner download redirect');
    const reader = response.body.getReader(), chunks = []; let size = 0;
    try {
      while (true) {
        const {value, done} = await reader.read(); if (done) break;
        size += value.length;
        if (size > manifest.apk_bytes) throw new EvidenceError('Runner download is larger than its manifest');
        chunks.push(value); message(`Downloading headset runner · ${Math.floor(100 * size / manifest.apk_bytes)}%`);
      }
    } finally {await reader.cancel().catch(() => {}); reader.releaseLock();}
    if (size !== manifest.apk_bytes) throw Error('Runner download was incomplete. Retry the download.');
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
    message('Verifying headset runner…');
    if (await sha256(bytes) !== manifest.apk_sha256) throw new EvidenceError('Runner checksum failed. Reload the website to get a verified release.');
    signal?.throwIfAborted(); message('Installing on your headset…');
    // Omitting the path selects stdin. A bare '-' is parsed as an option by
    // Android's install argument parser (including the Quest 3 firmware).
    const installed = await this.shell(['pm', 'install', '-r', '-t', '-d', '-S', String(size)], {data: bytes, signal, timeout: 180000,
      allowFailure: true, onWrite: (sent, total) => message(`Installing on your headset · ${Math.floor(100 * sent / total)}%`)});
    const detail = installed.stderr + installed.stdout;
    if (installed.code || !/\bSuccess\b/.test(installed.stdout)) {
      if (/UPDATE_INCOMPATIBLE|INCONSISTENT_CERTIFICATES|VERSION_DOWNGRADE/.test(detail)) throw new EvidenceError('The installed runner has a different signing identity. It was not removed. Reload the latest website release to install its separate runner and keep prior reports.');
      throw Error('Headset installation failed. Check available storage and headset permissions, then retry. ' + detail.trim().slice(0, 500));
    }
    if (await this.installedHash(manifest, signal) !== manifest.apk_sha256) throw new EvidenceError('Installed runner verification failed');
  }
  async readJson(manifest, path, {signal, limit} = {}) {
    const r = await this.shell(['run-as', manifest.package, 'cat', path], {signal, limit, allowFailure: true});
    if (r.code) {
      if (/No such file/.test(r.stderr)) {
        const match=/^files\/runs\/(run-[0-9a-f]{32})\/report\.json$/.exec(path);
        if(match){
          const diagnostic=await this.shell(['run-as',manifest.package,'cat',`files/runs/${match[1]}/startup-error.json`],{signal,limit:8192,allowFailure:true});
          if(!diagnostic.code){
            let issue;try{issue=JSON.parse(diagnostic.stdout);}catch{throw new EvidenceError('Malformed native startup diagnostic');}
            if(issue?.run_id!==match[1]||typeof issue.error!=='string')throw new EvidenceError('Native startup diagnostic identity mismatch');
            throw new EvidenceError(issue.error);
          }
        }
        throw new MissingFile('Waiting for the native benchmark. Keep USB connected; the headset can remain unworn.');
      }
      throw Error(r.stderr.trim() || 'Could not read headset evidence');
    }
    try {return JSON.parse(r.stdout);} catch {throw new EvidenceError('Malformed native JSON evidence');}
  }
  async launchActivity(manifest, key, id, signal) {
    // The Activity hands work to a foreground service and immediately finishes.
    // Waiting for it to display can time out on an unworn headset.
    const r = await this.shell(['am', 'start', '-n', manifest.package + '/' + manifest.activity, '--es', key, id], {signal, timeout: 30000});
    if (/Error:|Error type/.test(r.stdout)) throw Error('Could not open the headset runner: ' + r.stdout.trim());
  }
  async probe(manifest, signal) {
    const id = 'probe-' + crypto.randomUUID().replaceAll('-', '');
    await this.launchActivity(manifest, 'probe_id', id, signal);
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      signal?.throwIfAborted();
      try {
        const p = await this.readJson(manifest, `files/profiles/${id}.json`, {signal, limit: 1024 ** 2});
        if(p?.probe_id===id&&typeof p.error==='string')throw new EvidenceError(p.error);
        if (p?.probe_id !== id || p.profile_version !== 1 || !p.backends || Array.isArray(p.backends)
            || p.runner_revision !== manifest.runner_revision || p.runtime_revision !== manifest.runtime_revision
            || (manifest.protocol_version===2?['xrgpu_vulkan']:['executorch_cpu', 'executorch_vulkan']).some(backend => typeof p.backends[backend]?.compatible !== 'boolean')) {
          throw new EvidenceError('Native capability identity mismatch. Reload the website for a matched runner.');
        }
        return p;
      } catch (e) {if (!(e instanceof MissingFile)) throw e;}
      await pause(250);
    }
    throw new EvidenceError('The headset runner did not respond to its capability check. Another benchmark may still be running. Stop it from the headset\'s xrBench notification, then press Run again. Existing reports are preserved.');
  }
  async launch(manifest, config, signal) {
    if (!runPattern.test(config.run_id)) throw new EvidenceError('Invalid run identity');
    const id = config.run_id;
    const script = `mkdir -p files/commands files/runs/${id} && test ! -e files/runs/${id}/report.json && cat > files/commands/${id}.part && mv files/commands/${id}.part files/commands/${id}.json`;
    await this.shell(['run-as', manifest.package, 'sh', '-c', script], {data: new TextEncoder().encode(JSON.stringify(config)), signal});
    await this.launchActivity(manifest, 'run_id', id, signal);
  }
  read(manifest, id, filename, signal) {
    if (!runPattern.test(id) || !['report.json', 'progress.json'].includes(filename)) throw new EvidenceError('Invalid evidence path');
    return this.readJson(manifest, `files/runs/${id}/${filename}`, {signal});
  }
  async diagnostics(manifest, id) {
    if (!runPattern.test(id)) throw new EvidenceError('Invalid evidence identity');
    const result = {run_id: id, package: manifest.package, apk_sha256: manifest.apk_sha256,
      started_at: new Date().toISOString(), commands: {}};
    try {
      const host_started_at = new Date().toISOString();
      const clock = await this.shell(['date', '+%s%3N'], {timeout: 5000, limit: 128});
      const host_finished_at = new Date().toISOString(), milliseconds = clock.stdout.trim();
      if (!/^\d{13}$/.test(milliseconds)) throw Error('Device did not return milliseconds');
      result.clock = {host_started_at, host_finished_at, device_at: new Date(Number(milliseconds)).toISOString()};
    } catch(error) {result.clock_error = error.message;}
    // Use the current WebUSB owner. Diagnostics never start a host ADB server.
    try {
      for (let attempt = 0; attempt < 8; attempt++) {
        result.lifecycle = await this.readJson(manifest, `files/runs/${id}/lifecycle.json`, {limit: 2 * 1024 ** 2});
        if (result.lifecycle.events?.some(e => e.event === 'service_destroyed')) break;
        await pause(250);
      }
    }
    catch (error) {result.lifecycle_error = error.message;}
    for (const [key, args] of Object.entries({service: ['dumpsys', 'activity', 'services', manifest.package],
      power: ['dumpsys', 'power'], thermal: ['dumpsys', 'thermalservice'],
      notification: ['dumpsys', 'notification', manifest.package]})) {
      const started_at = new Date().toISOString();
      try {result.commands[key] = {...await this.shell(args, {allowFailure: true, timeout: 5000, limit: 2 * 1024 ** 2}),
        args, started_at, finished_at: new Date().toISOString()};}
      catch (error) {result.commands[key] = {args, started_at, finished_at: new Date().toISOString(), error: error.message};}
    }
    result.captured_at = new Date().toISOString();
    return result;
  }
  async installationSnapshot(manifest, signal) {
    const result = {kind: 'xrbench.installation-snapshot', package: manifest.package,
      captured_at: new Date().toISOString(), apk_sha256: await this.installedHash(manifest, signal)};
    if (!result.apk_sha256) return {...result, installed: false};
    result.installed = true;
    result.connection_id = (await sha256(new TextEncoder().encode(this.device.serial))).slice(0, 24);
    const dump = await this.shell(['dumpsys', 'package', manifest.package], {signal, timeout: 5000, limit: 2 * 1024 ** 2});
    const version = /\bversionCode=(\d+)\b/.exec(dump.stdout);
    if (!version) throw Error('Cannot verify installed version');
    result.version_code = Number(version[1]); result.package_dump = dump.stdout;
    const prefs = await this.shell(['run-as', manifest.package, 'cat', 'shared_prefs/device-profile.xml'], {signal,allowFailure:true,limit:8192});
    result.installation_id = /<string name=["']id["']>([a-f0-9-]{36})<\/string>/.exec(prefs.stdout)?.[1] || null;
    const command = 'for file in files/runs/run-*/report.json; do if [ -f "$file" ]; then sha256sum "$file"; fi; done';
    const reports = await this.shell(['run-as',manifest.package,'sh','-c',command], {signal,limit:2 * 1024 ** 2});
    result.reports = reports.stdout.trim() ? reports.stdout.trim().split('\n').map(line => {
      const match = /^([a-f0-9]{64})\s+(files\/runs\/run-[a-f0-9]{32}\/report\.json)$/.exec(line);
      if (!match) throw Error('Invalid saved report inventory');
      return {path:match[2],sha256:match[1]};
    }) : [];
    result.captured_at = new Date().toISOString();
    return result;
  }
  async isRunning(manifest, signal) {
    const result = await this.shell(['pidof', manifest.package], {signal, allowFailure: true});
    if (result.code === 1 && !result.stdout.trim() && !result.stderr.trim()) return false;
    if (result.code === 0 && /^\d+(?:\s+\d+)*$/.test(result.stdout.trim())) return true;
    throw Error('Could not check whether the headset runner is still running. Reconnect to recover this run.');
  }
  async stop(manifest, id, signal) {
    if (!runPattern.test(id)) throw new EvidenceError('Invalid run identity');
    // Also preserve Stop when a disconnect happened before launch created its directory.
    await this.shell(['run-as', manifest.package, 'sh', '-c', `mkdir -p files/runs/${id} && touch files/runs/${id}/cancel`], {signal});
  }
}
