import {sha256, terminal} from './evidence.mjs';

const simulated = value => value && typeof value === 'object' && (
  value.simulated === true || value.source === 'simulated' || value.presentation_source === 'simulated' ||
  Object.values(value).some(simulated));

// Separate from USB/session state: a receiver outage must never fail or rerun a benchmark.
export class ReportDelivery {
  constructor({store, endpoint, fetcher = globalThis.fetch.bind(globalThis), clock = Date.now, timeout = 30000}) {
    Object.assign(this, {store, endpoint, fetcher, clock, timeout});
    this.rows = new Map(); this.queuing = new Map(); this.task = null; this.disposed = false; this.error = '';
  }
  async init() {
    this.ready ||= this.store.deliveries().then(rows => {for (const row of rows) this.rows.set(row.run_id, row);})
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
        const row = {run_id: native.run_id, body, payload_sha256, status: 'pending', attempts: 0, next_attempt_at: 0};
        await this.store.saveDelivery(row); this.rows.set(row.run_id, row); this.error = '';
      })().finally(() => this.queuing.delete(native.run_id));
      this.queuing.set(native.run_id, queued);
    }
    await this.queuing.get(native.run_id);
  }
  snapshot() {
    return {error: this.error, reports: [...this.rows.values()].map(({run_id, status, receipt, error}) => ({run_id, status, receipt, error}))};
  }
  kick() {
    if (this.disposed || this.task) return;
    this.task = this.drain().catch(error => {this.error = error.message;}).finally(() => {this.task = null;});
  }
  async drain() {
    await this.init();
    for (const row of this.rows.values()) {
      if (this.disposed) break;
      if (row.status === 'sent' || row.next_attempt_at > this.clock()) continue;
      this.abort = new AbortController();
      const timer = setTimeout(() => this.abort?.abort(), this.timeout);
      row.status = 'sending';
      try {
        const response = await this.fetcher(this.endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: row.body, credentials: 'omit', redirect: 'error', signal: this.abort.signal});
        if (!response.ok) throw Error(`Report receiver returned HTTP ${response.status}.`);
        const receipt = await response.json();
        if (receipt.stored !== true || receipt.storage!=='postgresql' || receipt.run_id !== row.run_id || receipt.payload_sha256 !== row.payload_sha256)
          throw Error('Report receiver did not confirm this report.');
        // Persist acknowledgment before releasing the queued copy. Native evidence stays in reports.
        const sent = {...row, status: 'sent', receipt, body: undefined, error: undefined};
        await this.store.saveDelivery(sent); this.rows.set(row.run_id, sent); this.error = '';
      } catch (error) {
        row.status = 'pending'; row.error = error.message;
        if (!this.disposed) {
          row.attempts++;
          row.next_attempt_at = this.clock() + Math.min(300000, 5000 * 2 ** Math.min(row.attempts - 1, 6));
          await this.store.saveDelivery(row);
        }
      } finally {clearTimeout(timer); this.abort = null;}
    }
  }
  dispose() {this.disposed = true; this.abort?.abort();}
}
