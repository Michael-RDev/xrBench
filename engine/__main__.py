import argparse
import sys
import webbrowser

from engine.adb import ADB
from engine.controller import Controller
from engine.db import SchemaNotReady
from engine.db.config import DatabaseStartupError, database
from engine.paths import BUILD, STATE
from engine.server import create_server


def main(*, default_port=8765, automatic=False):
    description = ('Start Docker, PostgreSQL, pending SQL migrations, and the xrBench website.' if automatic
                   else 'Start only the local benchmark server. Start PostgreSQL and apply SQL manually first (engine/README.md).')
    parser=argparse.ArgumentParser(description=description)
    parser.set_defaults(auto_setup=automatic)
    parser.add_argument("--port",type=int,default=default_port)
    parser.add_argument("--adb")
    parser.add_argument("--no-docker",action="store_true",help="Use an already-running database; still apply pending SQL" if automatic else argparse.SUPPRESS)
    parser.add_argument("--no-open",action="store_true")
    args=parser.parse_args()
    try:
        launch(args)
    except DatabaseStartupError as error:
        raise SystemExit(str(error)) from None
    except KeyboardInterrupt:
        print("\nxrBench startup canceled.", file=sys.stderr)
        raise SystemExit(130) from None


def launch(args):
    if args.auto_setup:
        from engine.startup import prepare_database
        store = prepare_database(start_container=not args.no_docker)
    else:
        store=database()
    print("Checking the existing PostgreSQL schema…", flush=True)
    try:
        store.health()
    except SchemaNotReady:
        raise DatabaseStartupError("Database schema is not ready. Apply the SQL files in engine/db/migrations/ in order, then start the server again. See engine/README.md.") from None
    except Exception:
        raise DatabaseStartupError("PostgreSQL is unavailable. Start the database container manually and check engine/.env or XRGPU_DATABASE_URL, then retry. See engine/README.md.") from None
    controller=Controller(ADB(args.adb),store,STATE/"runs",BUILD)
    server=create_server(controller,args.port)
    url=f"http://localhost:{server.server_port}"
    print("xrBench: "+url,flush=True)
    if not args.no_open:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:controller.stopping.set();server.server_close()


if __name__=="__main__":main()
