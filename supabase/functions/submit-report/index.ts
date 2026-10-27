// Built by npm run build:receiver; no network-fetched runtime dependencies.
import {createSupabaseReportHandler, hostedConfiguration} from './receiver.mjs';
import manifests from './releases.json' with {type: 'json'};

// Invalid configuration stops startup. Every upload authenticates either the
// trusted server relay or a verified Auth user before reserving durable quotas.
const configuration = hostedConfiguration(Deno.env);
const handle = createSupabaseReportHandler({...configuration, manifests});
Deno.serve(handle);
