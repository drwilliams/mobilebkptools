package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"

	"github.com/olekukonko/tablewriter"
	"github.com/spf13/cobra"
	"howett.net/plist"
)

func cmdListApps() *cobra.Command {
	return &cobra.Command{
		Use:   "list-apps <backup-id>",
		Short: "List installed apps from a backup",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("invalid backup ID: %s", args[0])
			}
			return runListApps(id)
		},
	}
}

func runListApps(id int) error {
	db, err := openDB()
	if err != nil {
		return err
	}
	defer db.Close()

	rec, err := getBackupByID(db, id)
	if err != nil {
		return err
	}

	data, err := os.ReadFile(filepath.Join(rec.BackupPath, "Manifest.plist"))
	if err != nil {
		return fmt.Errorf("cannot read Manifest.plist: %w", err)
	}

	var manifest map[string]interface{}
	if _, err := plist.Unmarshal(data, &manifest); err != nil {
		return fmt.Errorf("cannot parse Manifest.plist: %w", err)
	}

	apps, ok := manifest["Applications"].(map[string]interface{})
	if !ok || len(apps) == 0 {
		return fmt.Errorf("no Applications key found in Manifest.plist")
	}

	type AppInfo struct {
		BundleID string
		Name     string
		Version  string
	}

	var list []AppInfo
	for bundleID, v := range apps {
		info, _ := v.(map[string]interface{})
		name := ""
		if s, ok := info["CFBundleDisplayName"].(string); ok {
			name = s
		} else if s, ok := info["CFBundleName"].(string); ok {
			name = s
		}
		version := ""
		if s, ok := info["CFBundleShortVersionString"].(string); ok {
			version = s
		}
		list = append(list, AppInfo{BundleID: bundleID, Name: name, Version: version})
	}

	sort.Slice(list, func(i, j int) bool {
		if list[i].Name != list[j].Name {
			return list[i].Name < list[j].Name
		}
		return list[i].BundleID < list[j].BundleID
	})

	table := tablewriter.NewWriter(os.Stdout)
	table.SetHeader([]string{"Bundle ID", "Name", "Version"})
	for _, a := range list {
		table.Append([]string{a.BundleID, a.Name, a.Version})
	}
	table.Render()
	fmt.Printf("\n%d apps\n", len(list))
	return nil
}
