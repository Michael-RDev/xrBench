import {sha256, terminal} from './evidence.mjs';

const simulated = value => value && typeof value === 'object' && (
  value.simulated === true || value.source === 'simulated' || value.presentation_source === 'simulated' ||
  Object.values(value).some(simulated));
const rejections = new Map([[400, 'The report was rejected.'], [409, 'This run ID already has a different report.'],
  [413, 'The report exceeds the upload limit.'], [415, 'The receiver does not support this report format.']]);

// Separate from USB/session state: a receiver outage must never fail or rerun a benchmark.
export class ReportDelivery {
  constructor({store, endpoint, auth = null, siteOrigin = globalThis.location?.origin, siteSha256 = null, pageId = 'page-' + crypto.randomUUID(), fetcher = globalThis.fetch.bind(globalThis), clock = Date.now, timeout = 30000}) {
    Object.assign(this, {store, endpoint, auth, siteOrigin, fetcher, clock, timeout});
    this.rows = new Map(); this.queuing = new Map(); this.task = null; this.disposed = false; this.error = '';
    this.pageId = pageId;
    this.siteSha256 = siteSha256;
    this.authBlocked = false;
    if (auth) {
      const receiver = new URL(endpoint);
      if (receiver.protocol !== 'https:' || receiver.username || receiver.password || receiver.hash || receiver.search)
        throw Error('Authenticated reports require a trusted HTTPS receiver.');
      // Pin the full destination once. Neither navigation nor an upload redirect
      // may forward the bearer token to another destination.
      this.trustedEndpoint = receiver.href;
      this.unsubscribe = auth.subscribe(() => {
        if (this.disposed) return;
        if (!auth.getToken()) {this.abort?.abort(); return;}
        this.authBlocked = false;
        for (const row of this.rows.values()) if (row.auth_required) {
          delete row.auth_required; delete row.error; row.next_attempt_at = 0;
        }
        // A sign-in may arrive while an earlier signed-out drain is settling.
        // Resume once that drain finishes instead of losing the notification.
        if (this.task) void this.task.then(() => this.kick()); else this.kick();
      });
    }
  }
  event(row, event, extra = {}) {
    return {event, at: this.clock(), page_id: this.pageId, site_sha256: this.siteSha256, payload_sha256: row.payload_sha256, ...extra};
  }
  async init() {
    this.ready ||= this.store.deliveries().then(async rows => {for (const row of rows) {
      if (row.status !== 'sent') {
        (row.history ||= []).push(this.event(row, 'restored'));
        await this.store.saveDelivery(row);
      }
      this.rows.set(row.run_id, row);
    }})
      .catch(error => {this.ready = null; throw error;});
    await this.ready;
  }
  has(id) {return this.rows.has(id);}
  async queue(native) {
    if (![2,3].includes(native?.schema_version) || native.test_only===true || !/^run-[a-f0-9]{32}$/.test(native.run_id) || !terminal.has(native.status) || simulated(native))
      throw Error('Only finished native reports can be sent.');
    await this.init();
    if (this.has(native.run_id)) return;
    if (!this.queuing.has(native.run_id)) {
      const queued = (async () => {
        const body = JSON.stringify(native), payload_sha256 = await sha256(new TextEncoder().encode(body));
        const row = {run_id: native.run_id, body, payload_sha256, status: 'pending', attempts: 0, next_attempt_at: 0,
          endpoint: this.endpoint ? String(this.endpoint) : null, site_sha256: this.siteSha256};
        row.history = [this.event(row, 'queued')];
        await this.store.saveDelivery(row); this.rows.set(row.run_id, row); this.error = '';
      })().finally(() => this.queuing.delete(native.run_id));
      this.queuing.set(native.run_id, queued);
    }
    await this.queuing.get(native.run_id);
  }
  snapshot() {
    return {error: this.error, auth_required: !!this.auth && (!this.auth.getToken() || this.authBlocked),
      reports: [...this.rows.values()].map(({run_id, status, receipt, error, last_http_status}) =>
        ({run_id, status, receipt, error, rejection: status === 'blocked' ? last_http_status : null, rate_limited: last_http_status === 429}))};
  }
  async retryRejected(id) {
    await this.init();
    const row = this.rows.get(id);
    if (!row || row.status !== 'blocked') return;
    row.status = 'pending'; delete row.error; delete row.last_http_status; row.next_attempt_at = 0;
    (row.history ||= []).push(this.event(row, 'manual_retry'));
    await this.store.saveDelivery(row); this.kick();
  }
  kick() {
    if (this.disposed || this.task) return;
    this.task = this.drain().catch(error => {this.error = error.message;}).finally(() => {this.task = null;});
  }
  async drain() {
    await this.init();
    for (const row of this.rows.values()) {
      if (this.disposed) break;
      if (row.status === 'sent' || row.status === 'blocked' || row.next_attempt_at > this.clock()) continue;
      const accessToken = this.auth?.getToken();
      if (this.auth && (!accessToken || this.authBlocked)) break;
      this.abort = new AbortController();
      const timer = setTimeout(() => this.abort?.abort(), this.timeout);
      row.status = 'sending'; let retryAfter = 0, rejected = false, responseReceived = false;
      delete row.last_http_status;
      try {
        const endpoint = this.trustedEndpoint || this.endpoint;
        let credentials = 'omit';
        // A private same-origin host may authenticate its proxy with cookies.
        // Cross-origin receivers must never receive those browser credentials.
        if (endpoint && this.siteOrigin && new URL(endpoint, this.siteOrigin).origin === this.siteOrigin)
          credentials = 'same-origin';
        const response = await this.fetcher(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json',
          ...(accessToken ? {Authorization: 'Bearer ' + accessToken} : {})},
          body: row.body, credentials, redirect: 'error', signal: this.abort.signal});
        responseReceived = true; row.last_http_status = response.status;
        if (this.auth && [401, 403].includes(response.status)) {
          this.authBlocked = true; row.auth_required = true; this.auth.invalidate();
          throw Error('Sign in again to send your saved reports.');
        }
        if (response.status === 429) {
          const value = response.headers?.get('retry-after');
          const duration = value && /^\d+$/.test(value) ? Number(value) * 1000 : Date.parse(value || '') - this.clock();
          if (Number.isFinite(duration)) retryAfter = Math.min(86400000, Math.max(0, duration));
        }
        if (rejections.has(response.status)) {
          rejected = true;
          throw Error(rejections.get(response.status) + ' A copy remains in this browser.');
        }
        if (!response.ok) throw Error(`Report receiver returned HTTP ${response.status}.`);
        const receipt = await response.json();
        if (receipt.stored !== true || receipt.storage!=='postgresql' || receipt.run_id !== row.run_id || receipt.payload_sha256 !== row.payload_sha256)
          throw Error('Report receiver did not confirm this report.');
        // Preserve the exact submitted bytes for hardware-validation evidence, even after acknowledgment.
        const sent = {...row, status: 'sent', receipt, acknowledged_at: this.clock(), error: undefined,
          history: [...(row.history || []), this.event(row, 'acknowledged')]};
        await this.store.saveDelivery(sent); this.rows.set(row.run_id, sent); this.error = '';
      } catch (error) {
        row.status = rejected ? 'blocked' : 'pending'; row.error = error.message;
        if (!this.disposed) {
          row.attempts++;
          (row.history ||= []).push(this.event(row, rejected ? 'rejected' : 'attempt_failed', {
            error: error.message, failure_kind: responseReceived ? 'http' : 'transport',
            http_status: Number.isInteger(row.last_http_status) ? row.last_http_status : null}));
          row.next_attempt_at = rejected ? 0 : this.clock() + Math.max(retryAfter, Math.min(300000, 5000 * 2 ** Math.min(row.attempts - 1, 6)));
          await this.store.saveDelivery(row);
        }
      } finally {clearTimeout(timer); this.abort = null;}
      if (this.authBlocked) break;
    }
  }
  dispose() {this.disposed = true; this.unsubscribe?.(); this.abort?.abort();}
}
