"""Verified, allowlisted updates from the project's HTTPS GitHub origin."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import tempfile

BASE = 'https://raw.githubusercontent.com/smhan7011-oss/light-and-salt/main/'
ALLOWED = {'README.md', 'commerce.py', 'server.py', 'requirements.txt',
           'launch.py', 'updater.py', 'start-windows.bat', '.gitignore',
           'static/app.js', 'static/index.html', 'static/connect.js',
           'static/connect.html', 'static/connect.css', 'tests/test_api.py',
           'tests/test_updater.py'}

class UpdateError(Exception):
    pass

def digest(data):
    return hashlib.sha256(data).hexdigest()

def fetch(path):
    request = urllib.request.Request(BASE + path, headers={'User-Agent': 'LightAndSalt-Updater/1'})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(4_000_001)
    if len(data) > 4_000_000:
        raise UpdateError('Update file is too large.')
    return data

def manifest(data):
    value = json.loads(data)
    if not isinstance(value, dict) or not isinstance(value.get('version'), str):
        raise UpdateError('Invalid update manifest.')
    entries = value.get('files')
    if not isinstance(entries, dict) or not entries or len(entries) > len(ALLOWED):
        raise UpdateError('Invalid update file list.')
    for name, sha in entries.items():
        if name not in ALLOWED or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise UpdateError('Unsupported update path or checksum.')
    return value

def update(root, download=fetch):
    root = Path(root).resolve()
    local_path = root / 'update-manifest.json'
    local = manifest(local_path.read_bytes())
    remote_bytes = download('update-manifest.json')
    remote = manifest(remote_bytes)
    if remote == local:
        return 'Already up to date: ' + remote['version']
    pending = {}
    for name, expected in remote['files'].items():
        path = root / name
        if path.is_symlink() or root not in path.resolve().parents:
            raise UpdateError('Unsafe local update path.')
        previous = path.read_bytes() if path.exists() else None
        if previous is not None and digest(previous) == expected:
            continue
        if name == 'start-windows.bat':
            raise UpdateError('Launcher format changed. Download this release manually.')
        if previous is not None and digest(previous) != local['files'].get(name):
            raise UpdateError('Locally modified file preserved: ' + name)
        incoming = download(name)
        if digest(incoming) != expected:
            raise UpdateError('Checksum mismatch; no files changed: ' + name)
        pending[name] = (previous, incoming)
    # All network access and checks finish before the first source file changes.
    with tempfile.TemporaryDirectory(prefix='light-salt-update-') as temp:
        stage = Path(temp)
        for name, (_, incoming) in pending.items():
            staged = stage / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(incoming)
        backup = root / '.update-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        backup.mkdir(parents=True)
        (backup / 'update-manifest.json').write_bytes(local_path.read_bytes())
        applied = []
        try:
            for name, (previous, incoming) in pending.items():
                target = root / name
                if previous is not None:
                    saved = backup / name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    saved.write_bytes(previous)
                target.parent.mkdir(parents=True, exist_ok=True)
                replacement = target.with_name(target.name + '.update-tmp')
                replacement.write_bytes(incoming)
                replacement.replace(target)
                applied.append(name)
            new_manifest = root / 'update-manifest.json.update-tmp'
            new_manifest.write_bytes(remote_bytes)
            new_manifest.replace(local_path)
        except Exception:
            for name in reversed(applied):
                previous = pending[name][0]
                target = root / name
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(previous)
            raise
    return 'Updated to ' + remote['version'] + '. Previous files: ' + str(backup)
