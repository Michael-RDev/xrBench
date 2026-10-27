import {BrowserController} from './controller.mjs';
import {BrowserStore} from './store.mjs';
import {WebUsbTransport} from './transport.mjs';
import {ReportDelivery} from './report-delivery.mjs';
import {reportEndpoint} from './report-endpoint.mjs';

// One identity per document/module lifetime, shared by USB and delivery evidence.
// Recreating the service in this page does not pretend that the tab refreshed.
const pageId = 'page-' + crypto.randomUUID().replaceAll('-', '');

export function createBrowserService() {
  // The production bundle is bench/app.js; all runtime assets share its HTTPS origin.
  const store = new BrowserStore();
  const siteSha256 = document.querySelector('meta[name="xrbench-site-sha256"]')?.content || null;
  const reportDeliveryMode = document.querySelector('meta[name="xrbench-report-delivery"]')?.content || 'local';
  if (!['automatic', 'local', 'download'].includes(reportDeliveryMode)) throw Error('Invalid report delivery mode.');
  // Download mode never initializes an outbox worker, so prior queued uploads
  // stay untouched. Hosted automatic mode uses only its own protected route.
  const endpoint = reportDeliveryMode === 'download' ? null : reportEndpoint(
    document.querySelector('meta[name="xrbench-report-endpoint"]')?.content ||
      (reportDeliveryMode === 'automatic' ? '/api/reports' : './api/reports'), new URL('../', import.meta.url));
  if (reportDeliveryMode === 'automatic' && (endpoint.origin !== location.origin || endpoint.pathname !== '/api/reports'))
    throw Error('Hosted automatic uploads require the protected same-origin /api/reports endpoint.');
  const delivery = reportDeliveryMode === 'download' ? null : new ReportDelivery({store, pageId, siteSha256,
    siteOrigin: location.origin, endpoint});
  const controller = new BrowserController({transport: new WebUsbTransport(), store, delivery, pageId,
    siteSha256,
    manifestUrl: new URL('../runner/manifest.json', import.meta.url)});
  return {
    controller,
    reportDeliveryMode,
    start: (serial, requestId, processor = 'gpu') => controller.start(serial, requestId, processor),
    retry: id => controller.retry(id),
    upgrade: (id,requestId) => controller.upgrade(id,requestId),
    exportSession: id => controller.exportSession(id),
    retryReport: id => delivery?.retryRejected(id),
    dispose: () => controller.dispose(),
    async request(path, body) {
      if (path === '/api/session') return {token: 'browser'};
      if (path === '/api/status') return controller.snapshot();
      if (path === '/api/device-catalog') {await controller.init(); return controller.manifest.device_catalog;}
      const match = /^\/api\/benchmark-sessions\/(bench-[a-f0-9]{32})\/stop$/.exec(path);
      if (match && body) return controller.stop(match[1]);
      const report = /^\/api\/runs\/(run-[a-f0-9]{32})\/report$/.exec(path);
      if (report) return controller.report(report[1]);
      throw Error('Unsupported browser operation');
    },
  };
}
