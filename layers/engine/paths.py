from pathlib import Path

ENGINE = Path(__file__).resolve().parent
LAYERS = ENGINE.parent
ROOT = LAYERS.parent
BUILD = LAYERS / ".build"
STATE = LAYERS / ".state"
ENV = LAYERS / ".env"
COMPOSE = ENGINE / "compose.yaml"
