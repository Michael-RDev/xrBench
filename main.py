"""Start Docker, prepare PostgreSQL, and open the local xrBench website."""
from engine.__main__ import main


if __name__ == "__main__":
    main(default_port=8766, automatic=True)
