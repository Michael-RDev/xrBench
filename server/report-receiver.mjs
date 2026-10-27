import {MAX_REPORT_BYTES, ReportLimitError, prepareSubmission} from './report-contract.mjs';

export class ReceiverError extends Error {
  constructor(status, message, {retryAfter} = {}) {
    super(message); this.status = status; this.retryAfter = retryAfter;
  }
}

function discardBody(request) {
  if (request.body && !request.body.locked) request.body.cancel().catch(() => {});
}

async function readBody(request, timeoutMs, maximum) {
  const length = request.headers.get('content-length');
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > maximum))
    throw new ReceiverError(413, 'Report is too large.');
  if (!request.body) throw new ReceiverError(400, 'Missing report.');
  const reader = request.body.getReader(), chunks = []; let size = 0, timer, abort;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new ReceiverError(408, 'Report upload timed out.')), timeoutMs);
    abort = () => reject(new ReceiverError(408, 'Report upload was interrupted.'));
    request.signal.addEventListener('abort', abort, {once: true});
    if (request.signal.aborted) abort();
  });
  let complete = false;
  try {
    while (true) {
      const {value, done} = await Promise.race([reader.read(), deadline]); if (done) break;
      size += value.byteLength;
      if (size > maximum) throw new ReceiverError(413, 'Report is too large.');
      chunks.push(value);
    }
    complete = true;
  } finally {
    clearTimeout(timer);
    request.signal.removeEventListener('abort', abort);
    // A hostile stream's cancel promise must not keep a slot occupied forever.
    if (!complete) reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  if (!size) throw new ReceiverError(400, 'Missing report.');
  const payload = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) {payload.set(chunk, offset); offset += chunk.byteLength;}
  return payload;
}

// Authentication must verify a session/token or a trusted gateway assertion.
// The quota callback must atomically debit a shared budget for this principal;
// Origin and client-supplied identity/IP headers do not establish identity.
export function createAdmissionPolicy({authenticate, consumeQuota}) {
  if (typeof authenticate !== 'function' || typeof consumeQuota !== 'function')
    throw new TypeError('Configure authentication and a shared upload quota.');
  return async (request, {signal}) => {
    const principal = await authenticate(request, {signal});
    if (typeof principal !== 'string' || !principal.trim() || principal.length > 256)
      throw new ReceiverError(401, 'An authorized upload session is required.');
    if (await consumeQuota({principal, request, signal, maxBytes: MAX_REPORT_BYTES}) !== true)
      throw new ReceiverError(429, 'The report upload quota has been reached.');
    return true;
  };
}

export function supabaseStore({url, serviceKey, fetcher = globalThis.fetch}) {
  return async (submission, {signal} = {}) => {
    if (!url || !serviceKey) throw new ReceiverError(503, 'Report storage is not configured.');
    let endpoint;
    try {
      endpoint = new URL('/rest/v1/rpc/ingest_xrbench_report', url);
      const local = ['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname);
      if ((endpoint.protocol !== 'https:' && !(local && endpoint.protocol === 'http:')) ||
          endpoint.username || endpoint.password) throw Error('Unsafe storage endpoint');
    } catch {throw new ReceiverError(503, 'Report storage requires a secure endpoint.');}
    const timeout = AbortSignal.timeout(20000);
    const response = await fetcher(endpoint, {
      method: 'POST', headers: {'Content-Type': 'application/json', apikey: serviceKey, Authorization: 'Bearer ' + serviceKey},
      body: JSON.stringify(submission), signal: signal ? AbortSignal.any([signal, timeout]) : timeout, redirect: 'error',
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      if (error.code === '23505') throw new ReceiverError(409, 'This run ID already has a different report.');
      throw new ReceiverError(503, 'Report storage is unavailable.');
    }
    const receipt = await response.json();
    if (receipt?.stored !== true || receipt.run_id !== submission.p_report.run_id ||
        receipt.payload_sha256 !== submission.p_payload_sha256 || receipt.report_sha256 !== submission.p_report_sha256)
      throw new ReceiverError(503, 'Report storage did not confirm this report.');
    return {...receipt,storage:'postgresql'};
  };
}

export function createReportHandler({origins, manifests, store, admit, maxInFlight = 4,
  uploadTimeoutMs = 30000, admissionTimeoutMs = 5000, storeTimeoutMs = 25000,
  maxReportBytes = MAX_REPORT_BYTES}) {
  if (!Number.isSafeInteger(maxInFlight) || maxInFlight < 1 || maxInFlight > 64 ||
      !Number.isSafeInteger(maxReportBytes) || maxReportBytes < 1 || maxReportBytes > MAX_REPORT_BYTES ||
      !Number.isFinite(uploadTimeoutMs) || uploadTimeoutMs <= 0 ||
      !Number.isFinite(admissionTimeoutMs) || admissionTimeoutMs <= 0 ||
      !Number.isFinite(storeTimeoutMs) || storeTimeoutMs <= 0)
    throw new TypeError('Configure finite receiver deadlines and concurrency.');
  const allowed = new Set(origins);
  let inFlight = 0;
  return async request => {
    const origin = request.headers.get('origin');
    const headers = {'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Vary': 'Origin'};
    const respond = (status, body) => {
      if (status >= 400) discardBody(request);
      if (status === 429 && !headers['Retry-After']) headers['Retry-After'] = '10';
      if (status === 401) headers['WWW-Authenticate'] = 'Bearer realm="xrbench-upload"';
      return new Response(body === null ? null : JSON.stringify(body), {status, headers});
    };
    if (!allowed.has(origin)) return respond(403, {error: 'This website is not an allowed report source.'});
    headers['Access-Control-Allow-Origin'] = origin;
    headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS';
    headers['Access-Control-Allow-Headers'] = 'content-type, authorization';
    headers['Access-Control-Expose-Headers'] = 'Retry-After';
    if (request.method === 'OPTIONS') return respond(204, null);
    if (request.method !== 'POST') return respond(405, {error: 'Use POST to submit a finished report.'});
    if (request.headers.get('content-type')?.split(';')[0].trim() !== 'application/json' ||
        !['identity', null].includes(request.headers.get('content-encoding')))
      return respond(415, {error: 'Send an uncompressed JSON report.'});
    // CORS is a browser boundary, not permission to spend database resources.
    // An unconfigured hosted adapter must fail closed, including spoofed Origin.
    if (typeof admit !== 'function') return respond(503, {error: 'Report admission is not configured.'});
    if (inFlight >= maxInFlight) return respond(429, {error: 'The report receiver is busy. Retry later.'});
    inFlight++;
    try {
      const admissionAbort = new AbortController(); let admissionTimer;
      try {
        const admitted = await Promise.race([
          Promise.resolve().then(() => admit(request, {signal: admissionAbort.signal})),
          new Promise((_, reject) => {admissionTimer = setTimeout(() => {
            admissionAbort.abort();
            reject(new ReceiverError(503, 'Report admission is temporarily unavailable.'));
          }, admissionTimeoutMs);}),
        ]);
        if (admitted !== true) throw new ReceiverError(403, 'This upload is not authorized.');
      } finally {clearTimeout(admissionTimer);}
      const payload = await readBody(request, uploadTimeoutMs, maxReportBytes);
      let submission;
      try {submission = await prepareSubmission(payload, manifests);}
      catch (error) {return respond(error instanceof ReportLimitError ? 413 : 400,
        {error: 'Expected a finished native report within the supported size and complexity limits.'});}
      // A successful response is sent only after the transactional database receipt.
      const storageAbort = new AbortController(); let storageTimer;
      try {
        const receipt = await Promise.race([
          Promise.resolve().then(() => store(submission, {signal: storageAbort.signal, request})),
          new Promise((_, reject) => {storageTimer = setTimeout(() => {
            storageAbort.abort();
            reject(new ReceiverError(503, 'Report storage timed out. The browser will retry.'));
          }, storeTimeoutMs);}),
        ]);
        return respond(200, receipt);
      } finally {clearTimeout(storageTimer);}
    } catch (error) {
      if (error instanceof ReceiverError && error.status === 429 &&
          Number.isSafeInteger(error.retryAfter) && error.retryAfter > 0 && error.retryAfter <= 86400)
        headers['Retry-After'] = String(error.retryAfter);
      return respond(error instanceof ReceiverError ? error.status : 503,
        {error: error instanceof ReceiverError ? error.message : 'Report storage is unavailable. The browser will retry.'});
    } finally {inFlight--;}
  };
}
