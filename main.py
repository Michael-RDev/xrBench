"""Open the public browser benchmark; --developer opts into local build tools."""
import sys


def main():
    if '--developer' in sys.argv:
        sys.argv.remove('--developer')
        from engine.__main__ import main as developer
        developer(default_port=8766, automatic=True)
    else:
        from engine.static_site import main as browser
        browser()


if __name__ == "__main__":
    main()
