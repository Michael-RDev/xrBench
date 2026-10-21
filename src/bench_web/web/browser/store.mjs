export class BrowserStore {
  constructor(indexedDB = globalThis.indexedDB) {
    this.ready = new Promise((resolve, reject) => {
      if (!indexedDB) {reject(Error('Browser storage is unavailable. Enable site storage and retry.')); return;}
      const open = indexedDB.open('xrbench-browser-v1', 2);
      open.onupgradeneeded = () => {
        for (const name of ['sessions', 'reports', 'report-deliveries'])
          if (!open.result.objectStoreNames.contains(name)) open.result.createObjectStore(name);
      };
      open.onsuccess = () => {open.result.onversionchange = () => open.result.close(); resolve(open.result);};
      open.onerror = () => reject(Error('Could not open browser storage. Enable site storage and retry.'));
      open.onblocked = () => reject(Error('Close other xrBench tabs to update browser storage.'));
    });
  }
  async transaction(name, mode, action) {
    const db = await this.ready;
    return new Promise((resolve, reject) => {
      const tx = db.transaction(name, mode), request = action(tx.objectStore(name));
      tx.oncomplete = () => resolve(request.result);
      tx.onabort = tx.onerror = () => reject(Error('Could not save benchmark data in this browser. Free site storage before starting another run.'));
    });
  }
  sessions() {return this.transaction('sessions', 'readonly', s => s.getAll());}
  save(session) {return this.transaction('sessions', 'readwrite', s => s.put(structuredClone(session), session.session_id));}
  report(id) {return this.transaction('reports', 'readonly', s => s.get(id));}
  saveReport(id, report) {return this.transaction('reports', 'readwrite', s => s.put(structuredClone(report), id));}
  deliveries() {return this.transaction('report-deliveries', 'readonly', s => s.getAll());}
  saveDelivery(row) {return this.transaction('report-deliveries', 'readwrite', s => s.put(structuredClone(row), row.run_id));}
}

// Hold the Web Lock until the controller releases it, including disconnected runs.
export function acquireDeviceLock(serial, locks = globalThis.navigator?.locks) {
  if (!locks) return Promise.reject(Error('This browser cannot reserve the headset. Use desktop Chrome or Edge.'));
  return new Promise((resolve, reject) => {
    locks.request('xrbench-usb:' + serial, {ifAvailable: true}, async lock => {
      if (!lock) {reject(Error('This headset is in use in another xrBench tab. Return to that tab or close it and retry.')); return;}
      await new Promise(release => resolve(release));
    }).catch(reject);
  });
}
