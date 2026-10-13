#!/usr/bin/env python3
"""
iphonetools — Create and inspect encrypted iPhone/iPad backups.

Usage:
    python main.py backup              # Back up connected device
    python main.py list-backups        # Show all stored backups
    python main.py set-password <ID>   # Update a stored backup password
    python main.py list-apps <ID>      # List apps from backup #ID
    python main.py extract-texts <ID>  # Extract SMS/iMessages from backup #ID
"""

import sys
from pathlib import Path
import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()


@click.group()
def cli():
    """iOS Backup Tool — create encrypted backups and extract data from them."""
    pass


@cli.command('gui')
def gui():
    """Open the desktop interface."""
    from gui import main
    main()


@cli.command()
@click.option('--udid', help='Back up the USB device with this identifier.')
@click.option('--incremental', is_flag=True, default=False,
              help='Perform an incremental backup instead of a full one.')
def backup(incremental, udid):
    """Create an encrypted backup of the connected iOS device.

    Generates a random encryption password, sets it on the device,
    runs the backup, and stores the password in iphonetools.db.
    """
    from device_backup import create_backup
    try:
        create_backup(full=not incremental, udid=udid)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command('list-devices')
def list_devices():
    """List connected USB devices as JSON."""
    import asyncio
    import json
    from device_backup import list_connected_devices
    try:
        click.echo(json.dumps(asyncio.run(list_connected_devices())))
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        raise click.ClickException(f'Cannot discover devices: {detail}. Check that usbmuxd is running.') from exc


@cli.command('list-backups')
def list_backups():
    """List all backups stored in the local database."""
    from db import get_all_backups

    backups = get_all_backups()
    if not backups:
        console.print("[yellow]No backups found. Run 'python main.py backup' first.[/yellow]")
        return

    table = Table(title="Stored Backups")
    table.add_column("ID", style="cyan", width=4)
    table.add_column("Device", style="green")
    table.add_column("iOS", style="dim", width=6)
    table.add_column("UDID", style="dim")
    table.add_column("Created", style="yellow")
    table.add_column("Path", style="dim")

    for b in backups:
        udid = b['device_udid']
        table.add_row(
            str(b['id']),
            f"{b['device_name'] or 'Unknown'} ({b['product_type'] or '?'})",
            b['ios_version'] or '?',
            udid[:8] + '…' + udid[-4:],
            b['created_at'],
            b['backup_path'],
        )

    console.print(table)


@cli.command('scan-backups')
@click.argument('paths', nargs=-1, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option('--json', 'json_output', is_flag=True, help='Emit discoveries as JSON lines.')
def scan_backups(paths, json_output):
    """Find local iPhone/iPad backups under PATHS.

    Defaults to ~/Library/Application Support/MobileSync/Backup on macOS,
    or the home folder on other systems.

    Discovery is read-only and does not register backups in the database.
    """
    from scan_backups import scan_backups as discover

    default_root = Path.home()
    if sys.platform == 'darwin':
        default_root /= 'Library/Application Support/MobileSync/Backup'
    roots = paths or (default_root,)
    if not json_output:
        console.print('Scanning: ' + ', '.join(str(path) for path in roots), markup=False)
    errors = []
    table = Table(title='Discovered Backups')
    for column in ('Device', 'iOS', 'Backup date', 'Encrypted', 'Path'):
        table.add_column(column, overflow='fold')
    for result in discover(roots, errors.append):
        if json_output:
            import json
            click.echo(json.dumps(result))
            continue
        table.add_row(*(Text(result[key]) for key in ('device', 'ios', 'date', 'encrypted', 'path')))
    if json_output:
        if errors:
            click.echo(f'Skipped {len(errors)} inaccessible folders or unreadable metadata files.', err=True)
        return
    if table.row_count:
        console.print(table)
    else:
        console.print('No backups found in the accessible folders.')
    if errors:
        console.print(f'Skipped {len(errors)} inaccessible folders or unreadable backup metadata files. '
                      'On macOS, Terminal may need Full Disk Access to scan protected folders.')


@cli.command('import-backup')
@click.argument('path', type=click.Path(exists=True, file_okay=False, path_type=Path))
def import_backup(path):
    """Register an existing backup folder without copying its files."""
    from db import import_backup as register
    try:
        backup_id, created = register(path)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f'{"Imported" if created else "Already registered"} backup {backup_id}.')
    click.echo('For encrypted backups, use set-password to store the existing password before extracting messages.')


@cli.command('set-password')
@click.argument('backup_id', type=click.IntRange(min=1))
def set_password(backup_id):
    """Update the database password for BACKUP_ID from 'list-backups'.

    This does not change the device's backup encryption password.
    """
    from db import get_backup_by_id, update_backup_password

    if not get_backup_by_id(backup_id):
        raise click.ClickException(f'No backup with ID {backup_id}.')

    password = click.prompt('Backup encryption password', hide_input=True,
                            confirmation_prompt=True)
    if not update_backup_password(backup_id, password):
        raise click.ClickException(f'No backup with ID {backup_id}.')
    console.print(f'[green]Updated stored password for backup {backup_id}.[/green]')


@cli.command('list-apps')
@click.argument('backup_id')
def list_apps(backup_id):
    """List apps installed on the device at backup time.

    BACKUP_ID is the numeric ID shown by 'list-backups'.
    """
    from db import get_backup_by_id
    from extract_apps import list_installed_apps

    backup = get_backup_by_id(backup_id)
    if not backup:
        console.print(f"[red]No backup with ID {backup_id}.[/red]")
        sys.exit(1)

    console.print(f"[dim]Backup: {backup['device_name']} — {backup['created_at']}[/dim]\n")
    list_installed_apps(backup['backup_path'])


@cli.command('extract-texts')
@click.argument('backup_id')
@click.option('--limit', default=200, show_default=True,
              help='Maximum number of messages to retrieve.')
@click.option('--output', default=None, metavar='FILE',
              help='Save messages to a CSV file instead of printing them.')
def extract_texts(backup_id, limit, output):
    """Extract SMS and iMessage texts from a backup.

    BACKUP_ID is the numeric ID shown by 'list-backups'.

    Messages are printed to the terminal by default, or saved to a CSV
    with --output messages.csv.
    """
    from db import get_backup_by_id
    from extract_sms import extract_messages

    backup = get_backup_by_id(backup_id)
    if not backup:
        console.print(f"[red]No backup with ID {backup_id}.[/red]")
        sys.exit(1)

    console.print(f"[dim]Backup: {backup['device_name']} — {backup['created_at']}[/dim]\n")
    extract_messages(
        backup_path=backup['backup_path'],
        password=backup['encryption_password'],
        limit=limit,
        output_file=output,
    )


if __name__ == '__main__':
    cli()
