Reorganize the iOS home screen icon layout in a backup using AI.

## Workflow

1. Run `./mobilebkptools list-backups` to show available backups. If the user didn't specify a backup ID, ask which one to use.

2. Export the current layout:
   ```
   ./mobilebkptools export-layout <id> /tmp/ios-layout.json
   ```

3. Read `/tmp/ios-layout.json` carefully. Understand the structure:
   - `pages`: array of pages, each page is a flat list of items
   - `dock`: array of items at the bottom (max 6 on iPad, 4 on iPhone)
   - String items are app bundle IDs — these can be freely moved
   - Object items (widgets, folders, smart stacks) should be preserved in-place unless the user asks to move them

4. Show the user a summary of what's currently on each page (just the app bundle IDs, not the verbose widget objects). Ask what reorganization they want, or use your judgment to suggest a clean layout if they said "just organize it" or similar.

5. Apply the reorganization to the JSON:
   - Keep all object items (widgets/folders/stacks) in their original positions on their original pages
   - Move string (app) items between pages and dock as requested or as makes sense
   - Do not add or remove items — only reorder/move existing ones
   - Do not change the structure of any object items

6. Write the modified layout back:
   ```
   ./mobilebkptools import-layout <id> /tmp/ios-layout.json
   ```

7. Tell the user the backup is ready and give them the exact restore command:
   ```
   idevicebackup2 restore --system backups/<session-dir>/<udid>
   ```
   The backup path is shown in `list-backups` output — strip the UDID suffix to get the session dir, then pass the full path shown.

## Notes

- The backup must already exist (created with `./mobilebkptools backup`)
- The device should be connected via USB when restoring
- Restoring overwrites the device's current home screen layout
- If `import-layout` prints a checksum warning, the restore may still work — try it
