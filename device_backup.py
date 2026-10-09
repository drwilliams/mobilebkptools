import asyncio
import inspect
import secrets
import plistlib
from pathlib import Path
from datetime import datetime
from rich.console import Console

console = Console()

BACKUPS_DIR = Path(__file__).parent / 'backups'


def generate_password() -> str:
    """Generate a cryptographically secure random backup password."""
    return secrets.token_hex(16)


async def _maybe_await(value):
    """Support both older sync and newer async pymobiledevice3 APIs."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _create_backup_async(full: bool = True):
    """Connect to the iOS device and create an encrypted backup."""
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service as MobileBackup2Service
    except ImportError:
        console.print("[red]pymobiledevice3 not installed. Run: pip install pymobiledevice3[/red]")
        return

    from db import get_latest_password_for_device, store_backup

    BACKUPS_DIR.mkdir(exist_ok=True)

    console.print("[cyan]Connecting to iOS device over USB...[/cyan]")
    try:
        lockdown = await _maybe_await(create_using_usbmux())
    except Exception as e:
        console.print(f"[red]Failed to connect to device: {e}[/red]")
        console.print("[yellow]Ensure the device is connected via USB and has been trusted on this machine.[/yellow]")
        return

    device_udid = getattr(lockdown, 'identifier', None) or getattr(lockdown, 'udid', None)
    device_name = await _maybe_await(lockdown.get_value('', 'DeviceName'))
    product_type = await _maybe_await(lockdown.get_value('', 'ProductType'))
    ios_version = await _maybe_await(lockdown.get_value('', 'ProductVersion'))

    console.print(f"[green]Connected:[/green] {device_name} ({product_type}) — iOS {ios_version}")
    console.print(f"[dim]UDID: {device_udid}[/dim]")

    # Create a timestamped session directory so each backup run is isolated
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_dir = BACKUPS_DIR / timestamp
    session_dir.mkdir(parents=True, exist_ok=True)

    password = generate_password()
    backup_password = password
    console.print(f"\n[cyan]Generated encryption password:[/cyan] {password}")
    console.print("[dim](Stored securely in iphonetools.db)[/dim]\n")

    # Check encryption state, then run the backup over the same service
    # connection. Newer pymobiledevice3/iOS combinations can time out when
    # reopening MobileBackup2 immediately after checking encryption.
    try:
        async with MobileBackup2Service(lockdown) as service:
            already_encrypted = await _maybe_await(
                service.get_will_encrypt() if hasattr(service, 'get_will_encrypt') else service.will_encrypt
            )
            console.print(f"[dim]Device backup encryption currently: {'on' if already_encrypted else 'off'}[/dim]")

            if not already_encrypted:
                console.print("[cyan]Enabling backup encryption on device...[/cyan]")
                await _maybe_await(service.change_password(backup_directory=str(session_dir), old='', new=password))
                console.print("[green]Encryption enabled.[/green]")
            else:
                console.print("[yellow]Device already has a backup password set.[/yellow]")
                stored_password = get_latest_password_for_device(device_udid)
                if stored_password:
                    console.print("[green]Using stored password from iphonetools.db.[/green]")
                    backup_password = stored_password
                    password = stored_password
                else:
                    console.print("[yellow]No stored password found; backup may require manual DB correction.[/yellow]")
                    backup_password = ''

            backup_type = "full" if full else "incremental"
            console.print(f"\n[cyan]Starting {backup_type} backup → {session_dir}[/cyan]")
            await _maybe_await(service.backup(full=full, backup_directory=str(session_dir), password=backup_password))
    except Exception as e:
        console.print(f"[red]Backup failed: {repr(e)}[/red]")
        return

    # Resolve the actual backup path (pymobiledevice3 nests it under the UDID)
    actual_backup_path = session_dir / device_udid
    if not actual_backup_path.exists():
        # Fallback: some versions put files directly in session_dir
        actual_backup_path = session_dir

    backup_id = store_backup(
        device_udid=device_udid,
        device_name=device_name,
        product_type=product_type,
        ios_version=ios_version,
        backup_path=actual_backup_path,
        encryption_password=password,
    )

    console.print(f"\n[bold green]Backup complete![/bold green]")
    console.print(f"  Backup ID : [cyan]{backup_id}[/cyan]")
    console.print(f"  Path      : {actual_backup_path}")
    console.print(f"  Password  : stored in iphonetools.db")


def create_backup(full: bool = True):
    """Connect to the iOS device and create an encrypted backup."""
    asyncio.run(_create_backup_async(full=full))
