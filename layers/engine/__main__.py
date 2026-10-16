import argparse
import time
import webbrowser

from layers.engine.adb import ADB
from layers.engine.controller import Controller
from layers.engine.db.config import database
from layers.engine.paths import BUILD, STATE
from layers.engine.server import create_server


def main():
    parser=argparse.ArgumentParser(description='Start PostgreSQL and the local native-headset benchmark website.')
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--adb")
    parser.add_argument("--no-docker",action="store_true",help="Use an already-running PostgreSQL server")
    parser.add_argument("--no-open",action="store_true")
    parser.add_argument("--migrate-only",action="store_true")
    args=parser.parse_args()
    store=database(not args.no_docker)
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
