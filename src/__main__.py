import sys

if '--developer' in sys.argv:
    sys.argv.remove('--developer')
    if '--port' not in sys.argv and not any(arg.startswith('--port=') for arg in sys.argv):
        sys.argv.extend(['--port', '8766'])
    from engine.__main__ import main
else:
    from engine.static_site import main

if __name__ == "__main__":
    main()
