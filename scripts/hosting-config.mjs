// Public deployment configuration only. Backend credentials never enter a build.
export function httpsOrigin(value, name) {
  let url;
  try {url = new URL(value);} catch {throw Error(`${name} must be an HTTPS origin.`);}
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash ||
      url.pathname !== '/' || ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))
    throw Error(`${name} must be an HTTPS origin without a path or credentials.`);
  return url.origin;
}

export function hostingConfig(env = process.env) {
  const mode = env.XRBENCH_DEPLOYMENT || 'local';
  if (!['local', 'hosted'].includes(mode)) throw Error('XRBENCH_DEPLOYMENT must be local or hosted.');
  const rawUrl = env.XRBENCH_SUPABASE_URL || '';
  if (mode === 'hosted' && !rawUrl) throw Error('Hosted builds require the intended backend project URL.');
  const projectUrl = rawUrl ? httpsOrigin(rawUrl, 'XRBENCH_SUPABASE_URL') : null;
  const delivery = env.XRBENCH_REPORT_DELIVERY || (mode === 'hosted' ? 'automatic' : 'local');
  if (!['automatic', 'download', 'local'].includes(delivery) || (mode === 'hosted' && delivery === 'local'))
    throw Error('Hosted delivery must be automatic or download; local delivery is for the private developer service.');
  let endpoint = delivery === 'download' ? null : (env.XRBENCH_REPORT_ENDPOINT || './api/reports');
  if (delivery === 'automatic') {
    if (env.XRBENCH_REPORT_ENDPOINT && env.XRBENCH_REPORT_ENDPOINT !== '/api/reports')
      throw Error('Hosted automatic uploads require the protected same-origin /api/reports endpoint.');
    endpoint = '/api/reports';
  }
  const siteOrigin = env.XRBENCH_SITE_ORIGIN ? httpsOrigin(env.XRBENCH_SITE_ORIGIN, 'XRBENCH_SITE_ORIGIN') : null;
  if (mode === 'hosted' && !siteOrigin) throw Error('Hosted builds require XRBENCH_SITE_ORIGIN.');
  const sitePath = env.XRBENCH_SITE_PATH || '/';
  if (!/^\/(?:[A-Za-z0-9_-]+\/)*$/.test(sitePath))
    throw Error('XRBENCH_SITE_PATH must be an absolute directory path ending with /.');
  return {mode, delivery, projectUrl, endpoint, siteOrigin, sitePath};
}

export function hostedHeaders({endpoint}) {
  const origins = new Set();
  if (endpoint?.startsWith('https://')) origins.add(new URL(endpoint).origin);
  return {
    'Content-Security-Policy': `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'${[...origins].map(origin => ' ' + origin).join('')}; object-src 'none'; base-uri 'self'; frame-ancestors 'none'`,
    'Permissions-Policy': 'usb=(self)',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Strict-Transport-Security': 'max-age=31536000',
  };
}
