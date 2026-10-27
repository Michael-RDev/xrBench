import {createReportHandler, ReceiverError} from './report-receiver.mjs';
import {validateManifest} from '../src/bench_web/web/browser/evidence.mjs';

export const MAX_HOSTED_REPORT_BYTES = 8 * 1024 * 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const JWT = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
const LOCAL = new Set(['localhost', '127.0.0.1', '[::1]']);

function capabilityBytes(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(value))
    throw new TypeError('Configure a 256-bit base64url upload proxy secret.');
  const raw = atob(value.replaceAll('-', '+').replaceAll('_', '/') + '=');
  const canonical = btoa(raw).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
  if (raw.length !== 32 || canonical !== value)
    throw new TypeError('Configure a 256-bit base64url upload proxy secret.');
  return Uint8Array.from(raw, character => character.charCodeAt(0));
}

function capabilityVerifier(secret) {
  const expected = capabilityBytes(secret);
  let key, signature;
  return async value => {
    let candidate;
    try {candidate = capabilityBytes(value);} catch {return false;}
    key ||= crypto.subtle.importKey('raw', expected, {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
    const imported = await key;
    signature ||= crypto.subtle.sign('HMAC', imported, expected);
    // Delegate the fixed-length MAC comparison to WebCrypto's implementation;
    // do not compare secret strings or exit a byte loop at the first difference.
    return crypto.subtle.verify('HMAC', imported, await signature, candidate);
  };
}

export function supabaseProjectUrl(value) {
  let url;
  try {url = new URL(value);} catch {throw new TypeError('Configure a valid Supabase project URL.');}
  if (typeof value !== 'string' || value !== value.trim() || url.username || url.password ||
      url.search || url.hash || url.pathname !== '/' ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && LOCAL.has(url.hostname))))
    throw new TypeError('The Supabase project URL must be an HTTPS origin.');
  return url.origin;
}

export function allowedOrigins(value) {
  let origins;
  try {origins = typeof value === 'string' ? JSON.parse(value) : value;}
  catch {throw new TypeError('Configure XRBENCH_ALLOWED_ORIGINS as a JSON array.');}
  if (!Array.isArray(origins) || origins.length < 1 || origins.length > 16)
    throw new TypeError('Configure at least one exact website origin.');
  for (const origin of origins) {
    if (typeof origin !== 'string' || supabaseProjectUrl(origin) !== origin)
      throw new TypeError('Allowed website origins must be exact HTTPS origins.');
  }
  if (new Set(origins).size !== origins.length) throw new TypeError('Allowed website origins must be unique.');
  return Object.freeze([...origins]);
}

export function hostedConfiguration(environment) {
  const read = name => typeof environment.get === 'function' ? environment.get(name) : environment[name];
  const url = supabaseProjectUrl(read('SUPABASE_URL'));
  const serviceKey = read('SUPABASE_SERVICE_ROLE_KEY');
  if (typeof serviceKey !== 'string' || serviceKey.length < 20 || serviceKey.length > 16384 || /\s/.test(serviceKey))
    throw new TypeError('Configure the server-only Supabase service-role key.');
  const uploadProxySecret = read('XRBENCH_UPLOAD_PROXY_SECRET');
  if (uploadProxySecret !== undefined) capabilityBytes(uploadProxySecret);
  return Object.freeze({url, serviceKey, origins: allowedOrigins(read('XRBENCH_ALLOWED_ORIGINS')),
    ...(uploadProxySecret === undefined ? {} : {uploadProxySecret})});
}

function denied(status, message) {throw new ReceiverError(status, message);}

// This bounds the complete upstream exchange, including its JSON body. Neither
// error text nor upstream response bodies are returned to the browser or logged.
async function jsonCall(fetcher, url, options, {signal, timeoutMs, limit = 65536}) {
  const abort = new AbortController(); let timer, onAbort, reader;
  const expired = new Promise((_, reject) => {
    const stop = () => {abort.abort(); reject(new ReceiverError(503, 'The upload service is temporarily unavailable.'));};
    timer = setTimeout(stop, timeoutMs);
    onAbort = stop;
    signal?.addEventListener('abort', onAbort, {once: true});
    if (signal?.aborted) stop();
  });
  const exchange = async () => {
    const response = await fetcher(url, {...options, credentials: 'omit', redirect: 'error', signal: abort.signal});
    if (!response.body) throw new ReceiverError(503, 'The upload service returned an invalid response.');
    reader = response.body.getReader();
    const parts = []; let size = 0;
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new ReceiverError(503, 'The upload service returned an invalid response.');
      parts.push(value);
    }
    const payload = new Uint8Array(size); let offset = 0;
    for (const part of parts) {payload.set(part, offset); offset += part.byteLength;}
    let json;
    try {json = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(payload));}
    catch {throw new ReceiverError(503, 'The upload service returned an invalid response.');}
    return {response, json};
  };
  try {return await Promise.race([exchange(), expired]);}
  catch (error) {
    abort.abort();
    if (reader) reader.cancel().catch(() => {});
    if (error instanceof ReceiverError) throw error;
    throw new ReceiverError(503, 'The upload service is temporarily unavailable.');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
    // releaseLock is safe for completed and cancelled reads; it also rejects any
    // still-pending read after a non-cooperative test/upstream stream times out.
    reader?.releaseLock();
  }
}

export function createSupabaseReportHandler({url, serviceKey, origins, manifests,
  fetcher = globalThis.fetch.bind(globalThis), clock = Date.now, authTimeoutMs = 4000,
  quotaTimeoutMs = 4000, rpcTimeoutMs = 20000, uploadProxySecret = undefined, ...limits}) {
  const project = supabaseProjectUrl(url);
  const config = hostedConfiguration({SUPABASE_URL: project,
    SUPABASE_SERVICE_ROLE_KEY: serviceKey, XRBENCH_ALLOWED_ORIGINS: origins,
    XRBENCH_UPLOAD_PROXY_SECRET: uploadProxySecret});
  const verifyProxy = config.uploadProxySecret === undefined ? null : capabilityVerifier(config.uploadProxySecret);
  if (!Array.isArray(manifests) || manifests.length < 1 || manifests.length > 1000)
    throw new TypeError('Package at least one supported runner release.');
  for (const manifest of manifests) validateManifest(manifest);
  if (new Set(manifests.map(manifest => manifest.apk_sha256)).size !== manifests.length)
    throw new TypeError('Supported runner releases must have unique APK hashes.');
  for (const timeout of [authTimeoutMs, quotaTimeoutMs, rpcTimeoutMs])
    if (!Number.isFinite(timeout) || timeout <= 0 || timeout > 25000)
      throw new TypeError('Configure finite upstream deadlines.');
  const admitted = new WeakMap();
  const serviceHeaders = {'Content-Type': 'application/json', apikey: config.serviceKey,
    Authorization: 'Bearer ' + config.serviceKey};
  const rpc = (name, body, signal, timeoutMs) => jsonCall(fetcher, project + '/rest/v1/rpc/' + name,
    {method: 'POST', headers: serviceHeaders, body: JSON.stringify(body)}, {signal, timeoutMs});
  const quotaError = error => {
    if (error?.code === 'P0001') {
      const seconds = Math.max(1, Math.min(86400,
        Math.ceil((86400000 - (clock() % 86400000)) / 1000)));
      throw new ReceiverError(429, 'The report upload budget is exhausted. Try again later.', {retryAfter: seconds});
    }
    if (error?.code === '23505') throw new ReceiverError(409, 'This run ID already has a different report.');
    throw new ReceiverError(503, 'Report storage is temporarily unavailable.');
  };
  return createReportHandler({origins: config.origins, manifests, admissionTimeoutMs: 10000, ...limits,
    // Supabase Edge memory is finite. Keep one bounded payload/projection alive
    // per instance; independent instances still share the database's budgets.
    maxInFlight: 1, maxReportBytes: MAX_HOSTED_REPORT_BYTES,
    // Exact true remains the generic receiver's admission contract. Verified
    // identity and DB reservations are retained only in this server WeakMap.
    admit: async (request, {signal}) => {
      let principal;
      if (request.headers.has('x-xrbench-proxy-key') || request.headers.has('x-xrbench-principal')) {
        const asserted = request.headers.get('x-xrbench-principal');
        if (!verifyProxy || !UUID.test(asserted) ||
            !await verifyProxy(request.headers.get('x-xrbench-proxy-key')))
          denied(401, 'A trusted upload proxy is required.');
        // Only the server holding the private capability may assert an anonymous
        // browser's quota/ownership UUID. The public request cannot choose it.
        principal = asserted.toLowerCase();
      } else {
        const authorization = request.headers.get('authorization');
        const token = authorization?.startsWith('Bearer ') ? authorization.slice(7) : '';
        if (token.length > 8192 || !JWT.test(token) || token === config.serviceKey)
          denied(401, 'Sign in to send benchmark reports.');
        const auth = await jsonCall(fetcher, project + '/auth/v1/user', {method: 'GET',
          headers: {apikey: config.serviceKey, Authorization: 'Bearer ' + token}},
        {signal, timeoutMs: authTimeoutMs});
        if (!auth.response.ok) {
          if ([400, 401, 403].includes(auth.response.status)) denied(401, 'Your upload session has expired. Sign in again.');
          denied(503, 'Sign-in verification is temporarily unavailable.');
        }
        const user = auth.json;
        if (!UUID.test(user?.id) || user.role !== 'authenticated' || user.is_anonymous === true ||
            typeof user.email !== 'string' || !user.email ||
            typeof user.email_confirmed_at !== 'string' || !Number.isFinite(Date.parse(user.email_confirmed_at)))
          denied(403, 'A verified email account is required to send reports.');
        principal = user.id.toLowerCase();
      }
      const quota = await rpc('reserve_xrbench_upload', {p_principal: principal, p_max_bytes: MAX_HOSTED_REPORT_BYTES},
        signal, quotaTimeoutMs);
      if (!quota.response.ok) quotaError(quota.json);
      const reservation = quota.json;
      if (!UUID.test(reservation?.reservation_id) || reservation.principal !== principal ||
          reservation.max_bytes !== MAX_HOSTED_REPORT_BYTES || !Number.isFinite(Date.parse(reservation.expires_at)) ||
          Date.parse(reservation.expires_at) <= clock())
        denied(503, 'The upload budget could not be confirmed.');
      admitted.set(request, Object.freeze({principal, reservation: reservation.reservation_id}));
      return true;
    },
    store: async (submission, {signal, request}) => {
      const admission = admitted.get(request);
      if (!admission) denied(503, 'A verified upload reservation is required.');
      admitted.delete(request);
      const result = await rpc('ingest_xrbench_report', {...submission,
        p_principal: admission.principal, p_reservation_id: admission.reservation}, signal, rpcTimeoutMs);
      if (!result.response.ok) quotaError(result.json);
      const receipt = result.json;
      if (receipt?.stored !== true || receipt.run_id !== submission.p_report.run_id ||
          receipt.payload_sha256 !== submission.p_payload_sha256 || receipt.report_sha256 !== submission.p_report_sha256)
        denied(503, 'PostgreSQL did not confirm this report.');
      return {...receipt, storage: 'postgresql'};
    },
  });
}
