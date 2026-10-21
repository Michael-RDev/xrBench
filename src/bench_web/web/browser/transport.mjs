import {Adb, AdbDaemonTransport} from '@yume-chan/adb';
import {AdbDaemonWebUsbDeviceManager} from '@yume-chan/adb-daemon-webusb';
import AdbWebCredentialStore from '@yume-chan/adb-credential-web';
import {sha256, runPattern, EvidenceError} from './evidence.mjs';

const quote = value => "'" + String(value).replaceAll("'", "'\\''") + "'";
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
export class MissingFile extends Error {}
export function connectionMessage(error) {
  if (error?.name === 'NotFoundError') return 'No headset selected. Connect a USB data cable, enable headset developer mode, then press Run.';
  if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') return 'USB permission was denied. Allow this website to access the headset, then retry.';
  if (/busy|claim|already.*open/i.test(error?.message || '')) return 'The USB interface is in use. Close other headset tools or browser tabs, then retry.';
  if (/Unable to open|Access denied/i.test(error?.message || '')) return 'The browser could not open the headset USB interface. Check the cable and device USB driver, then retry.';
  return error?.message || 'Headset connection failed. Reconnect the USB cable and retry.';
}
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
    return known ? Promise.resolve(known) : this.manager.requestDevice().then(device => {
      if (!device) throw new DOMException('No headset selected', 'NotFoundError');
      return device;
    });
  }
  async connect(device, signal) {
    signal?.throwIfAborted();
    if (this.adb && this.device?.serial === device.serial) return;
    await this.close(); this.device = device;
    const cancel = () => {void device.raw.close().catch(() => {});};
    signal?.addEventListener('abort', cancel, {once: true});
    let timedOut = false;
    const timer = setTimeout(() => {timedOut = true; cancel();}, 60000);
    try {
      const connection = await device.connect();
      signal?.throwIfAborted();
      const transport = await AdbDaemonTransport.authenticate({serial: device.serial, connection,
        credentialStore: this.credentials ||= new AdbWebCredentialStore('xrBench@' + location.host)});
      signal?.throwIfAborted();
      this.adb = new Adb(transport);
      const current = this.adb;
      current.disconnected.then(() => {if (this.adb === current) this.adb = null;}, () => {if (this.adb === current) this.adb = null;});
      if (!this.adb.subprocess.shellProtocol) throw Error('This headset does not support the required Android shell protocol.');
    } catch (error) {
      await this.close();
      throw Error(timedOut ? 'USB debugging approval timed out. Put on the headset, allow USB debugging, then retry.' : connectionMessage(error));
    }
    finally {clearTimeout(timer); signal?.removeEventListener('abort', cancel);}
  }
  async close() {
    const adb = this.adb, device = this.device; this.adb = null; this.device = null;
    try {await adb?.close();} catch {}
    try {if (device?.raw.opened) await device.raw.close();} catch {}
  }
  async shell(args, {data, signal, timeout = 15000, limit = 64 * 1024 ** 2, allowFailure = false, onWrite} = {}) {
    if (!this.adb) throw Error('Headset disconnected. Reconnect and retry to recover this run.');
    const abort = new AbortController(), cancel = () => abort.abort(signal.reason);
    signal?.throwIfAborted(); signal?.addEventListener('abort', cancel, {once: true});
    const timer = setTimeout(() => abort.abort(Error('Headset command timed out. Wake the headset and retry.')), timeout);
    let process, completed = false;
    try {
      // Tango joins array entries with spaces; shell-quote every argument ourselves.
      process = await this.adb.subprocess.shellProtocol.spawn(args.map(quote), abort.signal);
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
      const [stdout, stderr, code, inputError] = await Promise.all([collect(process.stdout, limit), collect(process.stderr, 1024 ** 2), process.exited,
        input().then(() => null, error => error)]);
      abort.signal.throwIfAborted();
      if (code && !allowFailure) throw Error(stderr.trim() || stdout.trim() || 'Headset command failed');
      if (!code && inputError) throw inputError;
      completed = true; return {stdout, stderr, code};
    } catch (error) {
      throw Error(`Android ${args.slice(0, 2).join(' ')}: ${error.message}`, {cause: error});
    } finally {
      clearTimeout(timer); signal?.removeEventListener('abort', cancel);
      if (!completed) {try {await process?.kill();} catch {}}
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
    const installed = await this.shell(['pm', 'install', '-r', '-t', '-S', String(size)], {data: bytes, signal, timeout: 180000,
      allowFailure: true, onWrite: (sent, total) => message(`Installing on your headset · ${Math.floor(100 * sent / total)}%`)});
    const detail = installed.stderr + installed.stdout;
    if (installed.code || !/\bSuccess\b/.test(installed.stdout)) {
      if (/UPDATE_INCOMPATIBLE|INCONSISTENT_CERTIFICATES|VERSION_DOWNGRADE/.test(detail)) throw new EvidenceError('The installed web runner has a different signing identity or newer version. It was not removed. Use the matching website release.');
      throw Error('Headset installation failed. Check available storage and headset permissions, then retry. ' + detail.trim().slice(0, 500));
    }
    if (await this.installedHash(manifest, signal) !== manifest.apk_sha256) throw new EvidenceError('Installed runner verification failed');
  }
  async readJson(manifest, path, {signal, limit} = {}) {
    const r = await this.shell(['run-as', manifest.package, 'cat', path], {signal, limit, allowFailure: true});
    if (r.code) {
      if (/No such file/.test(r.stderr)) throw new MissingFile('Waiting for native evidence. Wake the headset and keep the benchmark app open.');
      throw Error(r.stderr.trim() || 'Could not read headset evidence');
    }
    try {return JSON.parse(r.stdout);} catch {throw new EvidenceError('Malformed native JSON evidence');}
  }
  async launchActivity(manifest, key, id, signal) {
    const r = await this.shell(['am', 'start', '-W', '-n', manifest.package + '/' + manifest.activity, '--es', key, id], {signal, timeout: 30000});
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
        if (p?.probe_id !== id || p.profile_version !== 1 || !p.backends || Array.isArray(p.backends)
            || p.runner_revision !== manifest.runner_revision || p.runtime_revision !== manifest.runtime_revision
            || ['executorch_cpu', 'executorch_vulkan'].some(backend => typeof p.backends[backend]?.compatible !== 'boolean')) {
          throw new EvidenceError('Native capability identity mismatch. Reload the website for a matched runner.');
        }
        return p;
      } catch (e) {if (!(e instanceof MissingFile)) throw e;}
      await pause(250);
    }
    throw Error('Capability check timed out. Wake the headset and retry.');
  }
  async launch(manifest, config, signal) {
    if (!runPattern.test(config.run_id)) throw new EvidenceError('Invalid run identity');
    const id = config.run_id;
    const script = `mkdir -p files/commands files/runs/${id} && test ! -e files/runs/${id}/report.json && cat > files/commands/${id}.part && mv files/commands/${id}.part files/commands/${id}.json`;
    await this.shell(['run-as', manifest.package, 'sh', '-c', script], {data: new TextEncoder().encode(JSON.stringify(config)), signal});
    await this.shell(['input', 'keyevent', 'KEYCODE_WAKEUP'], {signal});
    await this.launchActivity(manifest, 'run_id', id, signal);
  }
  read(manifest, id, filename, signal) {
    if (!runPattern.test(id) || !['report.json', 'progress.json'].includes(filename)) throw new EvidenceError('Invalid evidence path');
    return this.readJson(manifest, `files/runs/${id}/${filename}`, {signal});
  }
  async isRunning(manifest, signal) {
    const result = await this.shell(['pidof', manifest.package], {signal, allowFailure: true});
    if (result.code === 1 && !result.stdout.trim() && !result.stderr.trim()) return false;
    if (result.code === 0 && /^\d+(?:\s+\d+)*$/.test(result.stdout.trim())) return true;
    throw Error('Could not check whether the headset runner is still running. Reconnect to recover this run.');
  }
  async stop(manifest, id) {
    if (!runPattern.test(id)) throw new EvidenceError('Invalid run identity');
    // Also preserve Stop when a disconnect happened before launch created its directory.
    await this.shell(['run-as', manifest.package, 'sh', '-c', `mkdir -p files/runs/${id} && touch files/runs/${id}/cancel`]);
  }
}
