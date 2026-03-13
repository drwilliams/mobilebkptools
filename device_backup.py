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


def create_backup(full: bool = True):
    """Connect to the iOS device and create an encrypted backup."""
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service as MobileBackup2Service
    except ImportError:
        console.print("[red]pymobiledevice3 not installed. Run: pip install pymobiledevice3[/red]")
        return

    from db import store_backup

    BACKUPS_DIR.mkdir(exist_ok=True)

    console.print("[cyan]Connecting to iOS device over USB...[/cyan]")
    try:
        lockdown = create_using_usbmux()
    except Exception as e:
        console.print(f"[red]Failed to connect to device: {e}[/red]")
        console.print("[yellow]Ensure the device is connected via USB and has been trusted on this machine.[/yellow]")
        return

    device_udid = lockdown.identifier
    device_name = lockdown.get_value('', 'DeviceName')
    product_type = lockdown.get_value('', 'ProductType')
    ios_version = lockdown.get_value('', 'ProductVersion')

    console.print(f"[green]Connected:[/green] {device_name} ({product_type}) — iOS {ios_version}")
    console.print(f"[dim]UDID: {device_udid}[/dim]")

    # Create a timestamped session directory so each backup run is isolated
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    session_dir = BACKUPS_DIR / timestamp
    session_dir.mkdir(parents=True, exist_ok=True)

    password = generate_password()
    console.print(f"\n[cyan]Generated encryption password:[/cyan] {password}")
    console.print("[dim](Stored securely in iphonetools.db)[/dim]\n")

    # Step 1: check encryption state and enable it if needed (separate connection)
    try:
        with MobileBackup2Service(lockdown) as service:
            already_encrypted = service.will_encrypt
            console.print(f"[dim]Device backup encryption currently: {'on' if already_encrypted else 'off'}[/dim]")

            if not already_encrypted:
                console.print("[cyan]Enabling backup encryption on device...[/cyan]")
                service.change_password(backup_directory=str(session_dir), old='', new=password)
                console.print("[green]Encryption enabled.[/green]")
            else:
                console.print("[yellow]Device already has a backup password set.[/yellow]")
                console.print("[yellow]Using existing password — update the DB entry manually if needed.[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Warning: Could not enable encryption — {repr(e)}[/yellow]")
        console.print("[yellow]Backup will proceed; password in DB may not decrypt it.[/yellow]")

    # Step 2: fresh connection for the actual backup
    backup_type = "full" if full else "incremental"
    console.print(f"\n[cyan]Starting {backup_type} backup → {session_dir}[/cyan]")
    try:
        lockdown2 = create_using_usbmux()
        with MobileBackup2Service(lockdown2) as service:
            service.backup(full=full, backup_directory=str(session_dir))
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
