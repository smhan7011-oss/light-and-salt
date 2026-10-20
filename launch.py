"""Stable Windows entry: update, prepare dependencies, start local server."""
from pathlib import Path
import os
import socket
import subprocess
import sys
import webbrowser

ROOT = Path(__file__).resolve().parent

def main():
    os.chdir(ROOT)
    # Do not replace running server files or launch a second instance.
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 8000)) == 0:
            print('Port 8000 is already in use. If the studio is running, close its server window before updating.')
            webbrowser.open('http://127.0.0.1:8000')
            return 0
    if (ROOT / 'update-manifest.json').exists():
        try:
            from updater import update
            print('Checking GitHub for updates...', flush=True)
            print(update(ROOT), flush=True)
        except Exception as error:
            print('Update was not applied. Current version will run.', flush=True)
            print(str(error), flush=True)
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    # Avoid network dependency installation when the exact requirement is already present.
    check = subprocess.run([str(python), '-c', "from importlib.metadata import version; import pathlib; expected=pathlib.Path('requirements.txt').read_text().strip().split('=='); assert version(expected[0])==expected[1]"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'requirements.txt'], check=True)
    return subprocess.run([str(python), 'server.py', '--open']).returncode

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('Startup failed:', str(error))
        sys.exit(1)
