import {BrowserController} from './controller.mjs';
import {BrowserStore} from './store.mjs';
import {WebUsbTransport} from './transport.mjs';

export function createBrowserService() {
  // The production bundle is bench/app.js; all runtime assets share its HTTPS origin.
  const controller = new BrowserController({transport: new WebUsbTransport(), store: new BrowserStore(),
    manifestUrl: new URL('../runner/manifest.json', import.meta.url)});
  return {
    controller,
    start: (serial, requestId) => controller.start(serial, requestId),
    exportSession: id => controller.exportSession(id),
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
