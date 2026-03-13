import plistlib
import sqlite3
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()


def list_installed_apps(backup_path: str):
    """List apps installed on the device at backup time.

    Reads from Manifest.plist (Applications key) which is present without
    decryption.  Falls back to scanning domain names in Manifest.db if the
    Applications key is absent.
    """
    manifest_path = Path(backup_path) / 'Manifest.plist'

    if not manifest_path.exists():
        console.print(f"[red]Manifest.plist not found at {manifest_path}[/red]")
        return

    with open(manifest_path, 'rb') as f:
        manifest = plistlib.load(f)

    apps = manifest.get('Applications', {})

    if apps:
        _render_apps_from_manifest(apps)
    else:
        console.print("[dim]No Applications key in Manifest.plist — falling back to Manifest.db[/dim]")
        _list_apps_from_manifest_db(backup_path)


def _render_apps_from_manifest(apps: dict):
    table = Table(title=f"Installed Apps ({len(apps)} total)")
    table.add_column("#", style="dim", width=4)
    table.add_column("Bundle ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Version", style="dim")

    for i, (bundle_id, info) in enumerate(sorted(apps.items()), 1):
        name = (
            info.get('CFBundleDisplayName')
            or info.get('CFBundleName')
            or ''
        )
        version = info.get('CFBundleShortVersionString') or info.get('CFBundleVersion') or ''
        table.add_row(str(i), bundle_id, name, version)

    console.print(table)


def _list_apps_from_manifest_db(backup_path: str):
    """Extract unique app bundle IDs from AppDomain entries in Manifest.db."""
    db_path = Path(backup_path) / 'Manifest.db'

    if not db_path.exists():
        console.print("[red]Manifest.db not found either — backup may be incomplete.[/red]")
        return

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT DISTINCT domain FROM Files WHERE domain LIKE 'AppDomain-%' ORDER BY domain"
    ).fetchall()
    conn.close()

    table = Table(title=f"App Domains ({len(rows)} total)")
    table.add_column("#", style="dim", width=4)
    table.add_column("Bundle ID", style="cyan")

    for i, (domain,) in enumerate(rows, 1):
        bundle_id = domain.removeprefix('AppDomain-')
        table.add_row(str(i), bundle_id)

    console.print(table)
