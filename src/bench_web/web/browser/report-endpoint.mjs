// Deployment configuration belongs to the site owner; visitors never enter credentials.
export function reportEndpoint(value, base) {
  const url = new URL(value || './api/reports', base);
  const site = new URL(base);
  if (url.username || url.password || url.hash || url.search ||
      (url.protocol !== 'https:' && !(url.origin === site.origin && url.protocol === 'http:')))
    throw Error('The website report receiver must use HTTPS.');
  return url;
}
