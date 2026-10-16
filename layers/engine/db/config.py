import os
import secrets
import subprocess

from layers.engine.db import Store
from layers.engine.paths import COMPOSE, ENV


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
        subprocess.run(["docker","compose","--project-name","xrgpu-benchmarks","--env-file",str(path),"-f",str(COMPOSE),"up","-d","--wait","db"],check=True)
    return Store(make_conninfo(host="127.0.0.1",port=env.get("XRGPU_DB_PORT","5432"),dbname="xrgpu",user="xrgpu",password=env["XRGPU_DB_PASSWORD"]))
