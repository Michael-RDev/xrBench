import os
import secrets
import subprocess

from engine.db import Store
from engine.paths import COMPOSE, ENV


DOCKER_CHECK_TIMEOUT = 10
DATABASE_START_TIMEOUT = 120


class DatabaseStartupError(RuntimeError):
    """An actionable startup failure that does not expose database credentials."""


def docker_command(command, *, timeout, failure, advice, password=""):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise DatabaseStartupError(
            "Docker CLI was not found. Start/install Docker Desktop, or configure "
            "XRGPU_DATABASE_URL to use an existing PostgreSQL server."
        ) from None
    except subprocess.TimeoutExpired:
        raise DatabaseStartupError(f"{failure} Timed out after {timeout} seconds. {advice}") from None
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        if password:
            detail = detail.replace(password, "[redacted]")
        detail = detail[-2000:]
        raise DatabaseStartupError(
            f"{failure} {advice}" + (f"\nDocker: {detail}" if detail else "")
        ) from None
    except OSError as error:
        raise DatabaseStartupError(f"Could not run Docker: {error}. {advice}") from None


def database(start=True):
    from psycopg.conninfo import make_conninfo
    if os.environ.get("XRGPU_DATABASE_URL"):
        return Store(os.environ["XRGPU_DATABASE_URL"])
    path=ENV
    if not path.exists():
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as stream:
            stream.write("XRGPU_DB_PASSWORD="+secrets.token_urlsafe(32)+"\nXRGPU_DB_PORT=5432\n")
    env=dict(line.split("=",1) for line in path.read_text().splitlines() if line and not line.startswith("#"))
    if start:
        print("Checking Docker engine…", flush=True)
        docker_command(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            timeout=DOCKER_CHECK_TIMEOUT,
            failure="Docker engine is unavailable or not responding.",
            advice="Open Docker Desktop and wait for the engine to be running, then retry. "
                   "If it remains stuck starting, restart Docker Desktop.",
        )
        print("Starting local PostgreSQL (first launch may download the image)…", flush=True)
        docker_command(
            ["docker", "compose", "--project-name", "xrgpu-benchmarks", "--env-file", str(path),
             "-f", str(COMPOSE), "up", "-d", "--wait", "--wait-timeout", "60", "db"],
            timeout=DATABASE_START_TIMEOUT,
            failure="The benchmark PostgreSQL container could not start.",
            advice="Check Docker Desktop's database container logs and whether the port in "
                   "engine/.env is already in use. Retry after any image download finishes.",
            password=env["XRGPU_DB_PASSWORD"],
        )
    return Store(make_conninfo(host="127.0.0.1",port=env.get("XRGPU_DB_PORT","5432"),dbname="xrgpu",user="xrgpu",password=env["XRGPU_DB_PASSWORD"]))
