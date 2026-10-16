import json
from pathlib import Path

from layers.engine.reports import digest, validate


class Store:
    def __init__(self, dsn):
        self.dsn = dsn

    def connect(self):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=3)

    def migrate(self):
        with self.connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(871624931)")
            db.execute("CREATE SCHEMA IF NOT EXISTS xrgpu_benchmarks")
            db.execute("SET LOCAL search_path TO xrgpu_benchmarks")
            exists = db.execute("SELECT to_regclass('xrgpu_benchmarks.schema_migrations')").fetchone()[0]
            applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations")} if exists else set()
            for path in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
                version = int(path.name.split("_")[0])
                if version not in applied:
                    db.execute(path.read_text())
                    db.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))

    def health(self):
        with self.connect() as db:
            db.execute("SELECT 1 FROM xrgpu_benchmarks.schema_migrations LIMIT 1")
        return True

    def put(self, report):
        validate(report)
        sha = digest(report)
        config = report["configuration"]
        with self.connect() as db:
            db.execute("SET LOCAL search_path TO xrgpu_benchmarks")
            inserted = db.execute("""INSERT INTO benchmark_runs
                (run_id,schema_version,suite_revision,runtime_revision,backend,mode,preset,status,
                 started_at,finished_at,device,configuration,report,report_sha256)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s)
                ON CONFLICT (run_id) DO NOTHING RETURNING run_id""",
                (report["run_id"], 2, report["suite_revision"], report["runtime_revision"],
                 report["backend"], config["mode"], config["preset"], report["status"],
                 report["started_at"], report["finished_at"], json.dumps(report["device"]),
                 json.dumps(config), json.dumps(report, allow_nan=False), sha)).fetchone()
            if not inserted:
                previous = db.execute("SELECT report_sha256 FROM benchmark_runs WHERE run_id=%s", (report["run_id"],)).fetchone()[0]
                if previous != sha:
                    raise FileExistsError("Run ID already stores different content")
            else:
                for result in report["results"]:
                    db.execute("""INSERT INTO benchmark_results
                        (run_id,case_id,phase,trial,operation,status,correctness,timings,samples,result)
                        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb)""",
                        (report["run_id"], result["case"], result["phase"], result["trial"], result["operation"],
                         result["status"], json.dumps(result.get("correctness")), json.dumps(result.get("timings", {})),
                         json.dumps(result["samples"]), json.dumps(result)))
        # The deterministic receipt is identical on every accepted retry.
        return dict(stored=True, run_id=report["run_id"], sha256=sha)

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT report FROM xrgpu_benchmarks.benchmark_runs WHERE run_id=%s", (run_id,)).fetchone()
            return row[0] if row else None

    def result(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT report, report_sha256 FROM xrgpu_benchmarks.benchmark_runs WHERE run_id=%s", (run_id,)).fetchone()
        if row is None:
            return None
        report, sha = row
        if report.get("run_id") != run_id or digest(report) != sha:
            raise RuntimeError("Stored report integrity check failed")
        return dict(report=report, receipt=dict(stored=True, run_id=run_id, sha256=sha))

    def recent(self, limit=30, offset=0):
        if not 1 <= limit <= 101 or not 0 <= offset <= 100000:raise ValueError("Invalid history page")
        from layers.engine.analysis import metadata
        with self.connect() as db:
            rows = db.execute("""SELECT jsonb_build_object(
                'run_id',run_id,'status',status,'device',device,'configuration',configuration,
                'suite_revision',suite_revision,'runtime_revision',runtime_revision,
                'started_at',started_at,'finished_at',finished_at), received_at, report_sha256
                FROM xrgpu_benchmarks.benchmark_runs
                ORDER BY received_at DESC, run_id DESC LIMIT %s OFFSET %s""", (limit, offset))
            return [dict(metadata(r[0]), received_at=r[1].isoformat(), storage="stored",
                         receipt=dict(stored=True, run_id=r[0]["run_id"], sha256=r[2])) for r in rows]
