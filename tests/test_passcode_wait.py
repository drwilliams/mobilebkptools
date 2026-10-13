import unittest
from unittest.mock import AsyncMock, patch

import device_backup


class PasscodeWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_beyond_original_ten_seconds(self):
        from pymobiledevice3.exceptions import AfcException
        from pymobiledevice3.services.afc import AFC_LOCK_EX, AFC_LOCK_UN, AfcError
        afc, notifications = AsyncMock(), AsyncMock()
        afc.fopen.return_value = 42
        attempts = 0

        async def lock(handle, operation):
            nonlocal attempts
            if operation == AFC_LOCK_EX:
                attempts += 1
                if attempts <= 75:
                    raise AfcException('Waiting for passcode', AfcError.OP_WOULD_BLOCK)

        afc.lock.side_effect = lock
        with patch.object(device_backup.asyncio, 'sleep', new_callable=AsyncMock):
            async with device_backup._backup_lock(afc, notifications):
                self.assertEqual(attempts, 76)
        afc.lock.assert_awaited_with(42, AFC_LOCK_UN)
        afc.fclose.assert_awaited_once_with(42)

    async def test_timeout_closes_handle_without_unlocking_unacquired_lock(self):
        from pymobiledevice3.exceptions import AfcException
        from pymobiledevice3.services.afc import AfcError
        afc, notifications = AsyncMock(), AsyncMock()
        afc.fopen.return_value = 42
        afc.lock.side_effect = AfcException('Waiting for passcode', AfcError.OP_WOULD_BLOCK)
        with patch.object(device_backup, 'DEVICE_PASSCODE_TIMEOUT', 0):
            with self.assertRaisesRegex(TimeoutError, 'enter its passcode'):
                async with device_backup._backup_lock(afc, notifications):
                    self.fail('A timed-out lock must not start a backup')
        self.assertEqual(afc.lock.await_count, 1)
        afc.fclose.assert_awaited_once_with(42)
