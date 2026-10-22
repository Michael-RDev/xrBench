import {MAX_REPORT_BYTES, prepareSubmission} from './report-contract.mjs';

export class ReceiverError extends Error {
  constructor(status, message) {super(message); this.status = status;}
}

async function readBody(request) {
  const length = request.headers.get('content-length');
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > MAX_REPORT_BYTES))
    throw new ReceiverError(413, 'Report is too large.');
  if (!request.body) throw new ReceiverError(400, 'Missing report.');
  const reader = request.body.getReader(), chunks = []; let size = 0;
  try {
    while (true) {
      const {value, done} = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > MAX_REPORT_BYTES) {await reader.cancel(); throw new ReceiverError(413, 'Report is too large.');}
      chunks.push(value);
    }
  } finally {reader.releaseLock();}
  if (!size) throw new ReceiverError(400, 'Missing report.');
  const payload = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) {payload.set(chunk, offset); offset += chunk.byteLength;}
  return payload;
}

export function supabaseStore({url, serviceKey, fetcher = globalThis.fetch}) {
  return async submission => {
    if (!url || !serviceKey) throw new ReceiverError(503, 'Report storage is not configured.');
    const response = await fetcher(new URL('/rest/v1/rpc/ingest_xrbench_report', url), {
      method: 'POST', headers: {'Content-Type': 'application/json', apikey: serviceKey, Authorization: 'Bearer ' + serviceKey},
      body: JSON.stringify(submission), signal: AbortSignal.timeout(20000), redirect: 'error',
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
    return receipt;
  };
}

export function createReportHandler({origins, manifests, store}) {
  const allowed = new Set(origins);
  return async request => {
    const origin = request.headers.get('origin');
    const headers = {'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Vary': 'Origin'};
    const respond = (status, body) => new Response(body === null ? null : JSON.stringify(body), {status, headers});
    if (!allowed.has(origin)) return respond(403, {error: 'This website is not an allowed report source.'});
    headers['Access-Control-Allow-Origin'] = origin;
    headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS';
    headers['Access-Control-Allow-Headers'] = 'content-type';
    if (request.method === 'OPTIONS') return respond(204, null);
    if (request.method !== 'POST') return respond(405, {error: 'Use POST to submit a finished report.'});
    if (request.headers.get('content-type')?.split(';')[0].trim() !== 'application/json' ||
        !['identity', null].includes(request.headers.get('content-encoding')))
      return respond(415, {error: 'Send an uncompressed JSON report.'});
    try {
      const payload = await readBody(request);
      let submission;
      try {submission = await prepareSubmission(payload, manifests);}
      catch {return respond(400, {error: 'Expected a finished native report from a supported runner release.'});}
      // A successful response is sent only after the transactional database receipt.
      return respond(200, await store(submission));
    } catch (error) {
      return respond(error instanceof ReceiverError ? error.status : 503,
        {error: error instanceof ReceiverError ? error.message : 'Report storage is unavailable. The browser will retry.'});
    }
  };
}
