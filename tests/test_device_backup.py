import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import db
import device_backup


class PasswordPersistenceTests(unittest.TestCase):
    def test_disconnect_retains_password_and_retry_reuses_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(db, 'DB_PATH', root / 'test.db'), patch.object(device_backup, 'BACKUPS_DIR', root / 'backups'):
                db.init_db()
                lockdown = AsyncMock()
                lockdown.identifier = 'test-device'
                lockdown.get_value.side_effect = lambda domain, key: {'DeviceName': 'Test', 'ProductType': 'iPhone', 'ProductVersion': '18'}[key]
                service = AsyncMock()
                service.__aenter__.return_value = service
                service.get_will_encrypt.return_value = False

                async def enable(**kwargs):
                    # The credential must already be committed when the device receives it.
                    self.assertEqual(db.get_latest_password_for_device('test-device'), kwargs['new'])

                service.change_password.side_effect = enable
                service.backup.side_effect = ConnectionError('USB disconnected')
                with patch('pymobiledevice3.lockdown.create_using_usbmux', return_value=lockdown), patch('pymobiledevice3.services.mobilebackup2.Mobilebackup2Service', return_value=service):
                    with self.assertRaises(RuntimeError):
                        asyncio.run(device_backup._create_backup_async())
                    saved = db.get_latest_password_for_device('test-device')
                    self.assertEqual(len(saved), 32)
                    self.assertEqual(db.get_all_backups(), [])
                    service.get_will_encrypt.return_value = True
                    service.backup.side_effect = None
                    asyncio.run(device_backup._create_backup_async())
                    self.assertEqual(service.backup.call_args.kwargs['password'], saved)
                    self.assertEqual(service.change_password.await_count, 1)
                    self.assertEqual(db.get_all_backups()[0]['encryption_password'], saved)

    def test_failed_password_change_also_retains_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(db, 'DB_PATH', Path(folder) / 'test.db'):
                db.init_db()
                db.save_device_password('test-device', 'Test', 'candidate')
                db.init_db()
                self.assertEqual(db.get_saved_device_passwords()[0]['encryption_password'], 'candidate')
                self.assertEqual(db.get_latest_password_for_device('test-device'), 'candidate')

    def test_concurrent_devices_use_distinct_connections_paths_and_passwords(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lockowns = {}
            services = {}
            entered = set()
            for udid in ('a', 'b'):
                lockdown = AsyncMock()
                lockdown.identifier = udid
                lockdown.get_value.side_effect = lambda domain, key: {'DeviceName': 'Test', 'ProductType': 'iPhone', 'ProductVersion': '18'}[key]
                lockowns[udid] = lockdown
                service = AsyncMock()
                service.__aenter__.return_value = service
                service.get_will_encrypt.return_value = False
                services[udid] = service

            async def run():
                both_started = asyncio.Event()
                async def transfer(udid, **kwargs):
                    entered.add(udid)
                    if len(entered) == 2:
                        both_started.set()
                    await asyncio.wait_for(both_started.wait(), timeout=2)
                    self.assertEqual(kwargs['password'], db.get_latest_password_for_device(udid))
                    if udid == 'a':
                        raise ConnectionError('Disconnected')
                for udid in ('a', 'b'):
                    async def backup(device=udid, **kwargs):
                        await transfer(device, **kwargs)
                    services[udid].backup.side_effect = backup
                return await asyncio.gather(*(device_backup._create_backup_async(udid=udid) for udid in ('a', 'b')), return_exceptions=True)

            with patch.object(db, 'DB_PATH', root / 'test.db'), patch.object(device_backup, 'BACKUPS_DIR', root / 'backups'), patch('pymobiledevice3.lockdown.create_using_usbmux', side_effect=lambda serial, **kwargs: lockowns[serial]) as connect, patch('pymobiledevice3.services.mobilebackup2.Mobilebackup2Service', side_effect=lambda lockdown: services[lockdown.identifier]):
                db.init_db()
                results = asyncio.run(run())
                self.assertIsInstance(results[0], RuntimeError)
                self.assertIsNone(results[1])
                self.assertEqual({call.kwargs['serial'] for call in connect.call_args_list}, {'a', 'b'})
                paths = [services[udid].backup.call_args.kwargs['backup_directory'] for udid in ('a', 'b')]
                self.assertNotEqual(*paths)
                self.assertEqual(len(db.get_saved_device_passwords()), 2)
                self.assertNotEqual(db.get_latest_password_for_device('a'), db.get_latest_password_for_device('b'))
                self.assertEqual([row['device_udid'] for row in db.get_all_backups()], ['b'])
