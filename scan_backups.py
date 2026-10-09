"""Read-only discovery of local iTunes-format device backups."""

import os
import plistlib
from pathlib import Path


def read_backup(path):
    """Validate one backup folder and read its device metadata."""
    path = Path(path).resolve()
    if not any((path / name).is_file() for name in ('Manifest.db', 'Manifest.mbdb')):
        raise ValueError('No backup manifest database found')
    with (path / 'Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    with (path / 'Manifest.plist').open('rb') as stream:
        manifest = plistlib.load(stream)
    if not isinstance(info, dict) or not isinstance(manifest, dict):
        raise ValueError('Backup metadata must be plist dictionaries')
    return {
        'path': str(path),
        'device': str(info.get('Device Name', 'Unknown')),
        'ios': str(info.get('Product Version', '?')),
        'date': str(info.get('Last Backup Date', '?')),
        'encrypted': 'Yes' if manifest.get('IsEncrypted') else 'No',
        'udid': str(info.get('Target Identifier') or info.get('Unique Identifier') or path.name),
        'product_type': str(info.get('Product Type', '')),
    }


def scan_backups(roots, on_error):
    """Yield backup metadata, skipping symlinks and duplicate directories."""
    seen = set()
    for root in roots:
        for directory, children, files in os.walk(root, onerror=on_error, followlinks=False):
            path = Path(directory)
            resolved = path.resolve()
            if resolved in seen:
                children[:] = []
                continue
            seen.add(resolved)
            if not {'Info.plist', 'Manifest.plist'}.issubset(files):
                continue
            if not ({'Manifest.db', 'Manifest.mbdb'} & set(files)):
                continue
            try:
                metadata = read_backup(path)
            except (OSError, ValueError, plistlib.InvalidFileException) as error:
                on_error(error)
                continue
            children[:] = []
            yield metadata
