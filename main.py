#!/usr/bin/env python3
"""
iphonetools — Create and inspect encrypted iPhone/iPad backups.

Usage:
    python main.py backup              # Back up connected device
    python main.py list-backups        # Show all stored backups
    python main.py list-apps <ID>      # List apps from backup #ID
    python main.py extract-texts <ID>  # Extract SMS/iMessages from backup #ID
"""

import sys
import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
def cli():
    """iOS Backup Tool — create encrypted backups and extract data from them."""
    pass


@cli.command()
@click.option('--incremental', is_flag=True, default=False,
              help='Perform an incremental backup instead of a full one.')
def backup(incremental):
    """Create an encrypted backup of the connected iOS device.

    Generates a random encryption password, sets it on the device,
    runs the backup, and stores the password in iphonetools.db.
    """
    from device_backup import create_backup
    create_backup(full=not incremental)


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
