# Local server security boundary

The Python website, test receiver, and developer dashboard bind to `127.0.0.1`.
Keep them on the developer's computer. Publish the packaged `dist/` assets through
an HTTPS static host and use a separately configured hosted report receiver.
See [report receiver hosting](report-receiver-hosting.md) and
[database hosting](database-hosting.md).

Do not expose these local services through a public reverse proxy or tunnel.
The developer dashboard can install software, control USB devices, and read saved
reports. Its `/api/session` token protects local browser interactions; anyone with
direct access to that service can request a token. It is not a login or a public
authorization system. Host, Origin, and browser Fetch headers are not credentials.

All local HTTP entrypoints reject `Forwarded`, `Via`, `X-Real-IP`, and
`X-Forwarded-*` headers. This catches ordinary proxy forwarding, but a proxy can
strip those headers; it does not make public proxying safe. Keep the listener
private and restrict database network access independently.

The local servers cap active connections at 16, allow at most 10 seconds for
request headers, and enforce a 30-second absolute request-body deadline. Extra
connections receive 503. Slow bodies receive 408, invalid or duplicate length
headers are rejected, and transfer/content encoding is unsupported. These bounds
also apply to clients that continually send small pieces of a request.

The packaged-site handlers serve only the website's known HTML, JS, CSS, runner
manifest/APKs, license files, and third-party notices. Hidden files, arbitrary
extra files, directory listings, traversal paths, and symlinks are excluded. On
platforms supporting directory-relative opens, each path component is opened
without following symlinks. Keep the package directory writable only by its
operator; other platforms additionally rely on that ownership to prevent local
path-swap races. Never put credentials, signing keys, or reports in a static host's
published directory, even though these Python handlers exclude them.

Report ingestion is limited to 32 MiB with bounded JSON nesting, strings, object
keys, arrays, and node count before recursive validation and hashing. Reports
remain self-reported measurements; schema validation does not prove that physical
hardware produced them.
