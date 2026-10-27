"""Explicit all-in-one startup used by the repository's main.py."""
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from engine.db.config import DatabaseStartupError, database
from engine.paths import COMPOSE, ENV, ROOT

DOCKER_WAIT_SECONDS = 120
DATABASE_WAIT_SECONDS = 30
DATABASE_VOLUME = 'xrgpu-benchmarks_benchmark-postgres'


def command(args, *, timeout, failure):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True,
                              timeout=timeout, cwd=ROOT)
    except FileNotFoundError:
        raise DatabaseStartupError('Docker Desktop and its docker CLI must be installed before running main.py.') from None
    except subprocess.TimeoutExpired:
        raise DatabaseStartupError(f'{failure} Timed out after {timeout} seconds.') from None
    except (subprocess.CalledProcessError, OSError):
        # Never echo command output: Compose errors may contain interpolated credentials.
        raise DatabaseStartupError(failure) from None


def docker_ready():
    try:
        result = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'],
                                capture_output=True, text=True, timeout=5, cwd=ROOT)
        return result.returncode == 0
    except FileNotFoundError:
        raise DatabaseStartupError('Docker Desktop and its docker CLI must be installed before running main.py.') from None
    except (subprocess.TimeoutExpired, OSError):
        return False


def start_docker():
    print('Checking Docker…', flush=True)
    if docker_ready():
        return
    print('Starting Docker Desktop…', flush=True)
    if sys.platform == 'darwin':
        command(['open', '-a', 'Docker'], timeout=10,
                failure='Could not open Docker Desktop. Open it manually, then rerun main.py.')
    elif sys.platform == 'win32':
        desktop = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Docker/Docker/Docker Desktop.exe'
        try:
            subprocess.Popen([str(desktop)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise DatabaseStartupError('Could not open Docker Desktop. Open it manually, then rerun main.py.') from None
    else:
        raise DatabaseStartupError('Docker is not ready. Start your Docker engine, then rerun main.py.')
    deadline = time.monotonic() + DOCKER_WAIT_SECONDS
    while time.monotonic() < deadline:
        if docker_ready():
            return
        time.sleep(2)
    raise DatabaseStartupError('Docker did not become ready within 120 seconds. Check Docker Desktop, then rerun main.py.')


def ensure_configuration(*, check_volume):
    if ENV.exists() or os.environ.get('XRGPU_DB_PASSWORD'):
        return
    if check_volume:
        volumes = command(['docker', 'volume', 'ls', '--format', '{{.Name}}'], timeout=10,
                          failure='Could not check the existing database volume. Check Docker and retry.')
        if DATABASE_VOLUME in volumes.stdout.splitlines():
            raise DatabaseStartupError('An existing xrBench database was found, but engine/.env is missing. Restore its original credentials or set XRGPU_DB_PASSWORD; the database will not be replaced.')
    else:
        raise DatabaseStartupError('Configure engine/.env or XRGPU_DATABASE_URL before using --no-docker.')
    try:
        # Exclusive creation protects existing credentials, including simultaneous launches.
        with open(ENV, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            stream.write('XRGPU_DB_PASSWORD=' + secrets.token_hex(32) + '\nXRGPU_DB_PORT=5432\n')
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    except OSError:
        raise DatabaseStartupError('Could not create engine/.env. Check directory permissions and retry.') from None


def prepare_database(*, start_container=True):
    external = bool(os.environ.get('XRGPU_DATABASE_URL'))
    if not external:
        if start_container:
            start_docker()
        ensure_configuration(check_volume=start_container)
    store = database()
    if not external and start_container:
        from psycopg.conninfo import conninfo_to_dict
        settings = conninfo_to_dict(store.dsn)
        # Use the same resolved values as Python, even when environment variables
        # override the file. Keep passwords out of command arguments and output.
        compose_env = dict(os.environ, XRGPU_DB_PASSWORD=settings['password'], XRGPU_DB_PORT=settings['port'])
        args = ['docker', 'compose', '--project-name', 'xrgpu-benchmarks']
        if ENV.exists():
            args += ['--env-file', str(ENV)]
        args += ['-f', str(COMPOSE), 'up', '-d', '--wait', '--wait-timeout', '60', 'db']
        print('Starting PostgreSQL…', flush=True)
        try:
            subprocess.run(args, env=compose_env, check=True, capture_output=True, text=True, timeout=180, cwd=ROOT)
        except (OSError, subprocess.SubprocessError):
            raise DatabaseStartupError('PostgreSQL could not start. Check Docker Desktop, the database container logs, and the port in engine/.env. Existing data is preserved.') from None
    print('Waiting for PostgreSQL…', flush=True)
    deadline = time.monotonic() + DATABASE_WAIT_SECONDS
    while True:
        try:
            with store.connect() as connection:
                connection.execute('SELECT 1')
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise DatabaseStartupError(
                    'Could not connect to PostgreSQL. Check the address, port and original credentials '
                    'in engine/.env or XRGPU_DATABASE_URL. Remote databases require verified TLS and '
                    'trusted CA configuration; see docs/database-hosting.md.'
                ) from None
            time.sleep(1)
    print('Checking the existing PostgreSQL schema…' if external else 'Applying pending SQL migrations…', flush=True)
    try:
        if not external:
            store.migrate()
        store.health()
    except Exception:
        raise DatabaseStartupError(
            'Database schema is not ready. Apply migrations separately with administrative credentials '
            '(python -m engine.db.migrate); see docs/database-hosting.md. Existing reports are preserved.'
            if external else
            'Database setup failed. Check SQL permissions and engine/db/migrations/. Existing reports are preserved; rerun after fixing the database issue.'
        ) from None
    return store
