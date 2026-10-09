import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db


class ImportBackupTests(unittest.TestCase):
    def test_import_metadata_and_preserve_password_on_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backup = root / 'device'
            backup.mkdir()
            info = {'Device Name': 'Test iPad', 'Target Identifier': 'test-udid',
                    'Product Type': 'iPad13,1', 'Product Version': '18.0'}
            (backup / 'Info.plist').write_bytes(plistlib.dumps(info))
            (backup / 'Manifest.plist').write_bytes(plistlib.dumps({'IsEncrypted': True}))
            (backup / 'Manifest.db').touch()
            with patch.object(db, 'DB_PATH', root / 'test.db'):
                db.init_db()
                backup_id, created = db.import_backup(backup)
                self.assertTrue(created)
                row = db.get_backup_by_id(backup_id)
                self.assertEqual(row['device_udid'], 'test-udid')
                self.assertEqual(row['device_name'], 'Test iPad')
                self.assertEqual(row['encryption_password'], '')
                self.assertEqual(row['backup_path'], str(backup.resolve()))
                db.update_backup_password(backup_id, 'existing-password')
                self.assertEqual(db.import_backup(backup / '.'), (backup_id, False))
                self.assertEqual(len(db.get_all_backups()), 1)
                self.assertEqual(db.get_backup_by_id(backup_id)['encryption_password'], 'existing-password')
                (backup / 'Manifest.db').unlink()
                with self.assertRaises(ValueError):
                    db.import_backup(backup)
                self.assertEqual(len(db.get_all_backups()), 1)


if __name__ == '__main__':
    unittest.main()
