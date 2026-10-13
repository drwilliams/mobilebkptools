import asyncio
import inspect
import secrets
import plistlib
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime
from rich.console import Console

console = Console()

BACKUPS_DIR = Path(__file__).parent / 'backups'
DEVICE_PASSCODE_TIMEOUT = 300


@asynccontextmanager
async def _backup_lock(afc, notification_proxy):
    """Allow time for the device passcode instead of the library's 10 seconds."""
    from pymobiledevice3.exceptions import AfcException
    from pymobiledevice3.services.afc import AFC_LOCK_EX, AFC_LOCK_UN, AfcError
    from pymobiledevice3.services.mobilebackup2 import (
        NP_SYNC_WILL_START, NP_SYNC_LOCK_REQUEST, NP_SYNC_DID_START,
        NP_SYNC_DID_FINISH,
    )

    await notification_proxy.notify_post(NP_SYNC_WILL_START)
    lockfile = await afc.fopen('/com.apple.itunes.lock_sync', 'r+')
    acquired = False
    try:
        if lockfile:
            await notification_proxy.notify_post(NP_SYNC_LOCK_REQUEST)
            console.print('[cyan]Unlock the device and enter its passcode if prompted. Waiting up to 5 minutes...[/cyan]')
            loop = asyncio.get_running_loop()
            deadline = loop.time() + DEVICE_PASSCODE_TIMEOUT
            while True:
                try:
                    await afc.lock(lockfile, AFC_LOCK_EX)
                except AfcException as error:
                    if error.status != AfcError.OP_WOULD_BLOCK:
                        raise
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError('The device did not allow backup within 5 minutes. Unlock it, enter its passcode, and retry.') from error
                    await asyncio.sleep(min(0.2, remaining))
                else:
                    acquired = True
                    await notification_proxy.notify_post(NP_SYNC_DID_START)
                    break
        yield
    finally:
        try:
            if acquired:
                await afc.lock(lockfile, AFC_LOCK_UN)
        finally:
            if lockfile:
                await afc.fclose(lockfile)
            await notification_proxy.notify_post(NP_SYNC_DID_FINISH)


def generate_password() -> str:
    """Generate a cryptographically secure random backup password."""
    return secrets.token_hex(16)


async def _maybe_await(value):
    """Support both older sync and newer async pymobiledevice3 APIs."""
    if inspect.isawaitable(value):
        return await value
    return value


async def list_connected_devices():
    """Discover USB devices without prompting for pairing."""
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux
    devices = {}
    for device in await _maybe_await(list_devices()):
        if device.connection_type != 'USB' or device.serial in devices:
            continue
        row = {'udid': device.serial, 'name': 'iOS device', 'ios': '', 'status': 'Connected'}
        lockdown = None
        try:
            lockdown = await asyncio.wait_for(_maybe_await(create_using_usbmux(
                serial=device.serial, connection_type='USB', autopair=False)), timeout=5)
            row['name'] = await _maybe_await(lockdown.get_value('', 'DeviceName'))
            row['ios'] = await _maybe_await(lockdown.get_value('', 'ProductVersion'))
        except Exception:
            row['status'] = 'Unlock and trust this computer'
        finally:
            if lockdown is not None:
                await _maybe_await(lockdown.close())
        devices[device.serial] = row
    return list(devices.values())


async def _create_backup_async(full: bool = True, udid: str | None = None):
    """Connect to the iOS device and create an encrypted backup."""
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service as MobileBackup2Service
    except ImportError:
        console.print("[red]pymobiledevice3 not installed. Run: pip install pymobiledevice3[/red]")
        raise RuntimeError('pymobiledevice3 is not installed.')

    from db import get_latest_password_for_device, store_backup, save_device_password

    BACKUPS_DIR.mkdir(exist_ok=True)

    console.print("[cyan]Connecting to iOS device over USB...[/cyan]")
    try:
        lockdown = await _maybe_await(create_using_usbmux(serial=udid, connection_type='USB'))
    except Exception as e:
        console.print(f"[red]Failed to connect to device: {e}[/red]")
        console.print("[yellow]Ensure the device is connected via USB and has been trusted on this machine.[/yellow]")
        raise RuntimeError('Unable to connect to the device.') from e

    device_udid = getattr(lockdown, 'identifier', None) or getattr(lockdown, 'udid', None)
    device_name = await _maybe_await(lockdown.get_value('', 'DeviceName'))
    product_type = await _maybe_await(lockdown.get_value('', 'ProductType'))
    ios_version = await _maybe_await(lockdown.get_value('', 'ProductVersion'))

    console.print(f"[green]Connected:[/green] {device_name} ({product_type}) — iOS {ios_version}")
    console.print(f"[dim]UDID: {device_udid}[/dim]")

    # Create a timestamped session directory so each backup run is isolated
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:8]
    session_dir = BACKUPS_DIR / timestamp
    session_dir.mkdir(parents=True, exist_ok=True)

    password = ''
    backup_password = ''

    # Check encryption state, then run the backup over the same service
    # connection. Newer pymobiledevice3/iOS combinations can time out when
    # reopening MobileBackup2 immediately after checking encryption.
    try:
        async with MobileBackup2Service(lockdown) as service:
            # Apply to this backup only; leave other services and jobs alone.
            service._backup_lock = _backup_lock
            already_encrypted = await _maybe_await(
                service.get_will_encrypt() if hasattr(service, 'get_will_encrypt') else service.will_encrypt
            )
            console.print(f"[dim]Device backup encryption currently: {'on' if already_encrypted else 'off'}[/dim]")

            if not already_encrypted:
                password = generate_password()
                backup_password = password
                save_device_password(device_udid, device_name, password)
                console.print(f"[cyan]Generated encryption password:[/cyan] {password}")
                console.print("[dim]Password saved in iphonetools.db before enabling encryption.[/dim]")
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
                    save_device_password(device_udid, device_name, password)
                else:
                    console.print("[yellow]No stored password found. This backup cannot be decrypted until the existing password is supplied.[/yellow]")
                    backup_password = ''

            backup_type = "full" if full else "incremental"
            console.print(f"\n[cyan]Starting {backup_type} backup → {session_dir}[/cyan]")
            def progress(value):
                print('BACKUP_PROGRESS ' + json.dumps(float(value)), flush=True)

            await _maybe_await(service.backup(full=full, backup_directory=str(session_dir),
                                            password=backup_password, progress_callback=progress))
    except Exception as e:
        console.print(f"[red]Backup failed: {repr(e)}[/red]")
        raise RuntimeError('Backup failed; see the error above. Any generated password remains saved.') from e

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


def create_backup(full: bool = True, udid: str | None = None):
    """Connect to the iOS device and create an encrypted backup."""
    asyncio.run(_create_backup_async(full=full, udid=udid))
