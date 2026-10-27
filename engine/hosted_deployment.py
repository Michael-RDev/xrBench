"""Explicit, scoped hosted migration application and read-only readiness checks."""

import argparse
import hashlib
import json
import os
import re

from engine.db import Store
from engine.paths import ROOT

MIGRATION_NAMES = (
    '20261027000100_xrbench_core.sql',
    '20261027000200_xrbench_hosted_uploads.sql',
)
API_ROLES = ('anon', 'authenticated', 'service_role')
TABLES = (
    'xrgpu_benchmarks.schema_migrations',
    'xrgpu_benchmarks.benchmark_runs',
    'xrgpu_benchmarks.benchmark_results',
    'xrbench_private.upload_policy',
    'xrbench_private.upload_daily',
    'xrbench_private.upload_reservations',
    'xrbench_private.upload_owners',
)
FUNCTIONS = {
    'reserve_xrbench_upload': 'uuid, bigint',
    'ingest_xrbench_report': 'jsonb, jsonb, text, text, uuid, uuid',
}
HOSTED_MAX_BYTES = 8 * 1024 * 1024
STORAGE_RESERVATION_BYTES = HOSTED_MAX_BYTES * 4 + 1048576


class DeploymentError(RuntimeError):
    """An intentionally credential-free deployment diagnostic."""


def migration_files():
    files = tuple(sorted((ROOT / 'supabase/migrations').glob('*xrbench*.sql')))
    if tuple(path.name for path in files) != MIGRATION_NAMES:
        raise DeploymentError('The reviewed xrBench migration package is missing or has unexpected files.')
    return files


def _role_names(roles):
    roles = dict(zip(API_ROLES, API_ROLES)) if roles is None else dict(roles)
    if set(roles) != set(API_ROLES) or any(not isinstance(name, str) or not name for name in roles.values()):
        raise DeploymentError('The hosted API role configuration is invalid.')
    return roles


def _function_sources():
    source = migration_files()[1].read_text()
    matches = re.findall(r'CREATE OR REPLACE FUNCTION public\.(\w+)\([\s\S]*?'
                         r'AS \$function\$([\s\S]*?)\$function\$;', source)
    expected = {name: body for name, body in matches}
    if set(expected) != set(FUNCTIONS):
        raise DeploymentError('The reviewed xrBench RPC definitions are missing.')
    return expected


def apply_migrations(store, *, roles=None):
    """Apply only the reviewed files, atomically; never called by default startup.

    The role override exists solely for isolated local tests that must not create
    or alter Supabase's real cluster-wide API roles.
    """
    files = migration_files()
    role_names = _role_names(roles)
    from psycopg import sql

    with store.connect() as connection:
        connection.execute("SET LOCAL lock_timeout = '3s'")
        connection.execute("SET LOCAL statement_timeout = '120s'")
        for path in files:
            lines = path.read_text().splitlines()
            # Remove only the reviewed outer transaction wrapper so both files
            # share one transaction: a second-file failure leaves neither applied.
            begin = next((i for i, line in enumerate(lines) if line.strip() == 'BEGIN;'), None)
            if begin is None or lines[-1].strip() != 'COMMIT;':
                raise DeploymentError('A reviewed xrBench migration has an invalid transaction wrapper.')
            source = '\n'.join(lines[:begin] + lines[begin + 1:-1])
            if roles is not None:
                source = re.sub(r'\b(anon|authenticated|service_role)\b',
                    lambda match: sql.Identifier(role_names[match.group()]).as_string(connection), source)
            connection.execute(source)
    return [path.name for path in files]


def audit(store, *, roles=None):
    """Return safe readiness metadata, using an explicitly read-only transaction."""
    role_names = _role_names(roles)
    expected_sources = _function_sources()
    checks, errors = {}, []
    result = {'ready': False, 'schema_versions': [], 'checks': checks, 'errors': errors,
              'physical_storage_headroom_verified': False}

    def check(name, passed, message):
        checks[name] = bool(passed)
        if not passed:
            errors.append(message)

    with store.connect() as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        connection.execute("SET LOCAL statement_timeout = '20s'")
        connection.execute('SET LOCAL search_path TO pg_catalog')
        rows = connection.execute('SELECT rolname,oid FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)',
                                  (list(role_names.values()),)).fetchall()
        role_ids = dict(rows)
        check('api_roles_exist', set(role_ids) == set(role_names.values()),
              'The required hosted API roles are missing.')

        table_rows = connection.execute("""SELECT n.nspname||'.'||c.relname,c.oid,c.relrowsecurity,c.relowner,
            EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,
                pg_catalog.acldefault('r',c.relowner))) a WHERE a.grantee=0)
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind='r' AND n.nspname||'.'||c.relname=ANY(%s)""", (list(TABLES),)).fetchall()
        tables = {row[0]: row for row in table_rows}
        check('required_tables_exist', set(tables) == set(TABLES),
              'The required xrBench database tables are missing.')
        check('required_tables_rls', len(tables) == len(TABLES) and all(row[2] for row in tables.values()),
              'RLS must be enabled on every xrBench table.')
        check('public_table_access_denied', len(tables) == len(TABLES) and not any(row[4] for row in tables.values()),
              'PUBLIC has unexpected xrBench table privileges.')
        privileges = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']
        if connection.info.server_version >= 170000:
            privileges.append('MAINTAIN')
        direct_access = False
        for row in tables.values():
            for role in role_ids:
                if row[3] == role_ids[role]:
                    direct_access = True
                for privilege in privileges:
                    direct_access |= connection.execute('SELECT pg_catalog.has_table_privilege(%s,%s,%s)',
                        (role, row[1], privilege)).fetchone()[0]
        check('api_direct_table_access_denied', checks['api_roles_exist'] and not direct_access,
              'An API role has direct xrBench table privileges or ownership.')
        schemas = connection.execute("SELECT oid,nspowner FROM pg_catalog.pg_namespace "
            "WHERE nspname IN ('xrgpu_benchmarks','xrbench_private')").fetchall()
        schema_access = False
        for oid, owner in schemas:
            for role, role_id in role_ids.items():
                schema_access |= owner == role_id or connection.execute(
                    "SELECT pg_catalog.has_schema_privilege(%s,%s,'USAGE') "
                    "OR pg_catalog.has_schema_privilege(%s,%s,'CREATE')", (role, oid, role, oid)).fetchone()[0]
        check('api_private_schema_access_denied', len(schemas) == 2 and checks['api_roles_exist'] and not schema_access,
              'An API role has unexpected xrBench schema privileges or ownership.')

        if 'xrgpu_benchmarks.schema_migrations' in tables:
            versions = [row[0] for row in connection.execute(
                'SELECT version FROM xrgpu_benchmarks.schema_migrations ORDER BY version')]
            result['schema_versions'] = versions
        check('core_schema_versions', result['schema_versions'] == [1, 2, 3],
              'The xrBench core schema versions do not match this release.')

        function_rows = connection.execute("""SELECT p.oid,p.proname,pg_catalog.oidvectortypes(p.proargtypes),
            p.prosecdef,p.proconfig,p.prosrc,p.proowner,p.prorettype::regtype::text,
            COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))
            FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname=ANY(%s)""", (list(FUNCTIONS),)).fetchall()
        canonical = [row for row in function_rows if FUNCTIONS.get(row[1]) == row[2]]
        check('canonical_rpc_signatures', len(canonical) == len(FUNCTIONS),
              'The canonical reservation-bound xrBench RPC signatures are missing.')
        definitions_match = len(canonical) == len(FUNCTIONS)
        fixed_security = definitions_match
        grants_safe = checks['api_roles_exist']
        for row in function_rows:
            oid, name, arguments, definer, configs, source, owner, return_type, acl = row
            is_canonical = FUNCTIONS.get(name) == arguments
            if is_canonical:
                definitions_match &= source == expected_sources[name]
                settings = {key.lower(): value for key, value in
                            (entry.split('=', 1) for entry in (configs or []))}
                fixed_security &= (definer and settings.get('search_path') == '""'
                    and settings.get('lock_timeout') == '3s' and settings.get('timezone') == 'UTC'
                    and return_type == 'jsonb' and owner not in role_ids.values())
            for kind, role in role_names.items():
                if role in role_ids:
                    allowed = connection.execute('SELECT pg_catalog.has_function_privilege(%s,%s,\'EXECUTE\')',
                                                 (role, oid)).fetchone()[0]
                    grants_safe &= allowed == (is_canonical and kind == 'service_role')
            # Reject PUBLIC or additional custom grants, not just the usual API
            # roles. The trusted owner retains its implicit administrative access.
            entries = connection.execute('SELECT grantee,is_grantable FROM pg_catalog.aclexplode(%s::aclitem[]) '
                                         "WHERE privilege_type='EXECUTE'", (acl,)).fetchall()
            allowed_grantees = {owner}
            if is_canonical and role_names['service_role'] in role_ids:
                allowed_grantees.add(role_ids[role_names['service_role']])
            grants_safe &= all(grantee in allowed_grantees and (not grantable or grantee == owner)
                               for grantee, grantable in entries)
        check('rpc_definitions_match_release', definitions_match,
              'The hosted RPC implementation differs from the reviewed release.')
        check('rpc_security_settings', fixed_security,
              'An xrBench RPC lacks fixed security settings or trusted administrative ownership.')
        check('rpc_execute_grants_and_legacy_denials', grants_safe and len(canonical) == len(FUNCTIONS),
              'An xrBench RPC or legacy overload has unexpected EXECUTE privileges.')
        result['rpc_body_sha256'] = {row[1]: hashlib.sha256(row[5].encode()).hexdigest() for row in canonical}

        if 'xrbench_private.upload_policy' in tables:
            policy = connection.execute('SELECT principal_daily_attempts,principal_daily_bytes,global_daily_attempts,'
                'global_daily_bytes,storage_limit_bytes,reservation_seconds,storage_used_bytes,storage_reserved_bytes '
                'FROM xrbench_private.upload_policy WHERE singleton').fetchone()
        else:
            policy = None
        if policy and 'xrbench_private.upload_reservations' in tables:
            reserved, expired = connection.execute('SELECT COALESCE(sum(storage_bytes),0),'
                'COALESCE(sum(storage_bytes) FILTER (WHERE expires_at<=clock_timestamp()),0) '
                'FROM xrbench_private.upload_reservations').fetchone()
            reserved, expired = int(reserved), int(expired)
            remaining = policy[4] - policy[6] - policy[7]
            check('quota_reservation_ledger', reserved == policy[7],
                  'The xrBench reservation capacity ledger is inconsistent.')
            if 'xrbench_private.upload_daily' in tables:
                daily_attempts, daily_bytes = connection.execute(
                    "SELECT COALESCE(max(attempts),0),COALESCE(max(bytes),0) FROM xrbench_private.upload_daily "
                    "WHERE principal='*' AND day=(clock_timestamp() AT TIME ZONE 'UTC')::date").fetchone()
            else:
                daily_attempts, daily_bytes = policy[2], policy[3]
            check('global_daily_capacity', policy[2] > daily_attempts
                and policy[3] - daily_bytes >= HOSTED_MAX_BYTES,
                'The global daily upload budget cannot admit another hosted upload.')
            check('quota_policy_and_capacity', policy[0] > 0 and policy[2] > 0
                and policy[1] >= HOSTED_MAX_BYTES and policy[3] >= HOSTED_MAX_BYTES
                and 60 <= policy[5] <= 3600 and policy[6] >= 0 and policy[7] >= 0
                and remaining + expired >= STORAGE_RESERVATION_BYTES,
                'The operator policy lacks capacity for an admitted hosted upload.')
            result['quota'] = {'storage_limit_bytes': policy[4], 'storage_used_bytes': policy[6],
                'storage_reserved_bytes': policy[7], 'expired_reservation_bytes': expired,
                'remaining_storage_bytes': remaining,
                'remaining_after_expiry_bytes': remaining + expired,
                'global_daily_remaining_attempts': policy[2] - daily_attempts,
                'global_daily_remaining_bytes': policy[3] - daily_bytes,
                'required_reservation_bytes': STORAGE_RESERVATION_BYTES,
                'hosted_upload_limit_bytes': HOSTED_MAX_BYTES}
        else:
            check('quota_policy_and_capacity', False, 'The xrBench upload policy or reservation ledger is missing.')
        result['physical_database_bytes'] = connection.execute(
            'SELECT pg_catalog.pg_database_size(pg_catalog.current_database())').fetchone()[0]
    result['ready'] = all(checks.values())
    result['physical_storage_note'] = (
        'Shared database size is a metric only. Verify provider storage limits, portfolio usage, '
        'WAL/backups and available headroom independently.'
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='Read-only readiness audit (the default)')
    modes.add_argument('--apply', action='store_true', help='Explicitly apply the reviewed xrBench migrations, then audit')
    arguments = parser.parse_args(argv)
    dsn = os.environ.get('XRBENCH_MIGRATION_DATABASE_URL')
    if not dsn:
        print(json.dumps({'ready': False, 'error': 'Set the separate XRBENCH_MIGRATION_DATABASE_URL administrative secret. '
                          'Runtime/local credentials are never used for hosted deployment.'}))
        return 2
    try:
        store = Store(dsn)
        applied = apply_migrations(store) if arguments.apply else []
        result = audit(store)
        result['applied_migrations'] = applied
        result['mode'] = 'apply' if arguments.apply else 'check'
    except DeploymentError as error:
        print(json.dumps({'ready': False, 'error': str(error)}))
        return 2
    except Exception:
        # Neither SQL/connection errors nor exception tracebacks may expose a
        # project address, password or administrative connection string.
        print(json.dumps({'ready': False, 'error': 'Hosted database verification failed. Check administrative access, '
                          'verified TLS, the reviewed xrBench migrations and database permissions.'}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
