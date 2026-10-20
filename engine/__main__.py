import argparse
import sys
import time
import webbrowser

from engine.adb import ADB
from engine.controller import Controller
from engine.db.config import DatabaseStartupError, database
from engine.paths import BUILD, STATE
from engine.server import create_server


def main():
    parser=argparse.ArgumentParser(description='Start PostgreSQL and the local native-headset benchmark website.')
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--adb")
    parser.add_argument("--no-docker",action="store_true",help="Use an already-running PostgreSQL server")
    parser.add_argument("--no-open",action="store_true")
    parser.add_argument("--migrate-only",action="store_true")
    args=parser.parse_args()
    try:
        launch(args)
    except DatabaseStartupError as error:
        raise SystemExit(str(error)) from None
    except KeyboardInterrupt:
        print("\nxrBench startup canceled.", file=sys.stderr)
        raise SystemExit(130) from None


def launch(args):
    store=database(not args.no_docker)
    print("Connecting to PostgreSQL and applying migrations…", flush=True)
    for attempt in range(30):
        try:store.migrate();break
        except Exception:
            if attempt==29:raise SystemExit("PostgreSQL unavailable or migration failed. Check local database configuration.")
            time.sleep(1)
    if args.migrate_only:print("Benchmark PostgreSQL migrations applied.");return
    controller=Controller(ADB(args.adb),store,STATE/"runs",BUILD)
    server=create_server(controller,args.port)
    url=f"http://localhost:{server.server_port}"
    print("xrBench: "+url,flush=True)
    if not args.no_open:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:controller.stopping.set();server.server_close()


if __name__=="__main__":main()
