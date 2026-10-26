from pathlib import Path

ENGINE = Path(__file__).resolve().parent
ROOT = ENGINE.parent
LAYERS = ENGINE / "workloads"
BUILD = ENGINE / ".build"
STATE = ENGINE / ".state"
ENV = ENGINE / ".env"
COMPOSE = ENGINE / "compose.yaml"
