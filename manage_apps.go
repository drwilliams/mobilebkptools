package main

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	survey "github.com/AlecAivazis/survey/v2"
	"github.com/olekukonko/tablewriter"
	"github.com/spf13/cobra"
	"howett.net/plist"
)

// appEntry represents an app found in Manifest.db.
type appEntry struct {
	BundleID  string
	Name      string
	Domain    string
	FileCount int
}

func cmdListBackupApps() *cobra.Command {
	return &cobra.Command{
		Use:   "list-backup-apps <backup-id>",
		Short: "List apps with data in a backup (from Manifest.db)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("invalid backup ID: %s", args[0])
			}
			return runListBackupApps(id)
		},
	}
}

func cmdPruneApps() *cobra.Command {
	var keepFlags []string
	cmd := &cobra.Command{
		Use:   "prune-apps <backup-id>",
		Short: "Interactively select apps to keep; remove the rest from the backup",
		Long: `Presents a checkbox list of every app that has data in the backup.
Uncheck apps you want to remove, then confirm. Their files are deleted
from disk and their entries are removed from Manifest.db.

Use --keep <bundle-id> (repeatable) to skip the interactive prompt.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("invalid backup ID: %s", args[0])
			}
			return runPruneApps(id, keepFlags)
		},
	}
	cmd.Flags().StringArrayVar(&keepFlags, "keep", nil,
		"Bundle ID to keep (repeatable). If any --keep flags are set, interactive selection is skipped.")
	return cmd
}

// ── List ──────────────────────────────────────────────────────────────────────

func runListBackupApps(id int) error {
	db, err := openDB()
	if err != nil {
		return err
	}
	defer db.Close()

	rec, err := getBackupByID(db, id)
	if err != nil {
		return err
	}

	_, manifestDbKey, err := openKeybag(rec)
	if err != nil {
		return err
	}
	decMDB, err := decryptManifestDB(rec.BackupPath, manifestDbKey)
	if err != nil {
		return err
	}

	apps, err := listAppsFromMDB(rec.BackupPath, decMDB)
	if err != nil {
		return err
	}

	table := tablewriter.NewWriter(os.Stdout)
	table.SetHeader([]string{"Bundle ID", "Name", "Files"})
	for _, a := range apps {
		table.Append([]string{a.BundleID, a.Name, strconv.Itoa(a.FileCount)})
	}
	table.Render()
	fmt.Printf("\n%d apps\n", len(apps))
	return nil
}

// ── Prune ─────────────────────────────────────────────────────────────────────

func runPruneApps(id int, keepList []string) error {
	db, err := openDB()
	if err != nil {
		return err
	}
	defer db.Close()

	rec, err := getBackupByID(db, id)
	if err != nil {
		return err
	}

	_, manifestDbKey, err := openKeybag(rec)
	if err != nil {
		return err
	}
	decMDB, err := decryptManifestDB(rec.BackupPath, manifestDbKey)
	if err != nil {
		return err
	}

	apps, err := listAppsFromMDB(rec.BackupPath, decMDB)
	if err != nil {
		return err
	}
	if len(apps) == 0 {
		fmt.Println("No app data found in backup.")
		return nil
	}

	var toRemove []appEntry

	if len(keepList) > 0 {
		keepSet := make(map[string]bool, len(keepList))
		for _, k := range keepList {
			keepSet[k] = true
		}
		for _, a := range apps {
			if !keepSet[a.BundleID] {
				toRemove = append(toRemove, a)
			}
		}
		if len(toRemove) == 0 {
			fmt.Println("Nothing to remove.")
			return nil
		}
		fmt.Printf("Removing %d apps (non-interactive).\n", len(toRemove))
	} else {
		// Build option labels: "Name (bundle.id)" or just "bundle.id"
		options := make([]string, len(apps))
		for i, a := range apps {
			if a.Name != "" {
				options[i] = fmt.Sprintf("%s (%s)", a.Name, a.BundleID)
			} else {
				options[i] = a.BundleID
			}
		}

		var selected []string
		prompt := &survey.MultiSelect{
			Message:  "Select apps to KEEP (uncheck to remove):",
			Options:  options,
			Default:  options, // all checked by default
			PageSize: 20,
		}
		if err := survey.AskOne(prompt, &selected); err != nil {
			return fmt.Errorf("selection cancelled: %w", err)
		}

		keepSet := make(map[string]bool, len(selected))
		for _, s := range selected {
			keepSet[s] = true
		}
		for i, a := range apps {
			if !keepSet[options[i]] {
				toRemove = append(toRemove, a)
			}
		}
		if len(toRemove) == 0 {
			fmt.Println("Nothing to remove.")
			return nil
		}
	}

	// Summary + confirmation
	fmt.Printf("\nApps to remove (%d):\n", len(toRemove))
	totalFiles := 0
	for _, a := range toRemove {
		label := a.BundleID
		if a.Name != "" {
			label = fmt.Sprintf("%s (%s)", a.Name, a.BundleID)
		}
		fmt.Printf("  - %s  [%d files]\n", label, a.FileCount)
		totalFiles += a.FileCount
	}
	fmt.Printf("\nTotal: %d files will be deleted.\n", totalFiles)

	ok := false
	if err := survey.AskOne(&survey.Confirm{Message: "Proceed?", Default: false}, &ok); err != nil || !ok {
		fmt.Println("Aborted.")
		return nil
	}

	return applyPrune(rec, toRemove, decMDB, manifestDbKey)
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// listAppsFromMDB groups Manifest.db AppDomain-* entries by bundle ID.
func listAppsFromMDB(backupPath string, decMDB []byte) ([]appEntry, error) {
	tmp, err := os.CreateTemp("", "manifest-*.db")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(decMDB); err != nil {
		return nil, err
	}
	tmp.Close()

	mdb, err := sql.Open("sqlite", tmp.Name())
	if err != nil {
		return nil, err
	}
	defer mdb.Close()

	rows, err := mdb.Query(
		`SELECT domain, COUNT(*) FROM Files WHERE domain LIKE 'AppDomain-%' GROUP BY domain ORDER BY domain`,
	)
	if err != nil {
		return nil, fmt.Errorf("cannot query Manifest.db: %w", err)
	}
	defer rows.Close()

	nameMap := loadAppNamesFromPlist(backupPath)

	var apps []appEntry
	for rows.Next() {
		var domain string
		var cnt int
		if err := rows.Scan(&domain, &cnt); err != nil {
			return nil, err
		}
		bundleID := strings.TrimPrefix(domain, "AppDomain-")
		apps = append(apps, appEntry{
			BundleID:  bundleID,
			Name:      nameMap[bundleID],
			Domain:    domain,
			FileCount: cnt,
		})
	}

	sort.Slice(apps, func(i, j int) bool {
		ni := apps[i].Name
		if ni == "" {
			ni = apps[i].BundleID
		}
		nj := apps[j].Name
		if nj == "" {
			nj = apps[j].BundleID
		}
		return strings.ToLower(ni) < strings.ToLower(nj)
	})
	return apps, nil
}

// loadAppNamesFromPlist returns a bundleID->displayName map from Manifest.plist.
func loadAppNamesFromPlist(backupPath string) map[string]string {
	data, err := os.ReadFile(filepath.Join(backupPath, "Manifest.plist"))
	if err != nil {
		return nil
	}
	var manifest map[string]interface{}
	if _, err := plist.Unmarshal(data, &manifest); err != nil {
		return nil
	}
	applications, _ := manifest["Applications"].(map[string]interface{})
	result := make(map[string]string, len(applications))
	for bundleID, v := range applications {
		info, _ := v.(map[string]interface{})
		if name, ok := info["CFBundleDisplayName"].(string); ok && name != "" {
			result[bundleID] = name
		} else if name, ok := info["CFBundleName"].(string); ok && name != "" {
			result[bundleID] = name
		}
	}
	return result
}

// applyPrune deletes app files from disk and removes their rows from Manifest.db,
// then re-encrypts and writes Manifest.db back.
func applyPrune(rec *BackupRecord, toRemove []appEntry, decMDB []byte, manifestDbKey []byte) error {
	tmp, err := os.CreateTemp("", "manifest-*.db")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(decMDB); err != nil {
		return err
	}
	tmp.Close()

	mdb, err := sql.Open("sqlite", tmp.Name())
	if err != nil {
		return err
	}

	removedFiles := 0
	for _, app := range toRemove {
		// Collect fileIDs before deleting rows
		rows, err := mdb.Query("SELECT fileID FROM Files WHERE domain=?", app.Domain)
		if err != nil {
			mdb.Close()
			return fmt.Errorf("cannot query files for %s: %w", app.Domain, err)
		}
		var fileIDs []string
		for rows.Next() {
			var fid string
			if err := rows.Scan(&fid); err == nil && len(fid) >= 2 {
				fileIDs = append(fileIDs, fid)
			}
		}
		rows.Close()

		// Delete physical files
		for _, fid := range fileIDs {
			path := filepath.Join(rec.BackupPath, fid[:2], fid)
			if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
				fmt.Printf("  warning: %s: %v\n", path, err)
			} else {
				removedFiles++
			}
		}

		// Delete Manifest.db rows
		if _, err := mdb.Exec("DELETE FROM Files WHERE domain=?", app.Domain); err != nil {
			mdb.Close()
			return fmt.Errorf("cannot delete rows for %s: %w", app.Domain, err)
		}

		fmt.Printf("  Removed %s (%d files)\n", app.BundleID, len(fileIDs))
	}

	mdb.Close()

	// Read back the modified SQLite file
	newDecMDB, err := os.ReadFile(tmp.Name())
	if err != nil {
		return fmt.Errorf("cannot read modified Manifest.db: %w", err)
	}

	// Re-encrypt and write Manifest.db
	paddedMDB := pkcs7Pad(newDecMDB, 16)
	encMDB, err := encryptAESCBC(manifestDbKey, paddedMDB)
	if err != nil {
		return fmt.Errorf("cannot re-encrypt Manifest.db: %w", err)
	}
	if err := os.WriteFile(filepath.Join(rec.BackupPath, "Manifest.db"), encMDB, 0600); err != nil {
		return fmt.Errorf("cannot write Manifest.db: %w", err)
	}

	fmt.Printf("\nDone. Deleted %d files, re-encrypted Manifest.db.\n", removedFiles)
	return nil
}
