package main

import (
	"fmt"
	"os"
	"strconv"

	"github.com/olekukonko/tablewriter"
	"github.com/spf13/cobra"
)

func main() {
	root := &cobra.Command{
		Use:   "mobilebkptools",
		Short: "iOS encrypted backup tool",
	}
	root.AddCommand(
		cmdBackup(),
		cmdListBackups(),
		cmdListApps(),
		cmdExtractTexts(),
		cmdShowLayout(),
		cmdExportLayout(),
		cmdImportLayout(),
		cmdListBackupApps(),
		cmdPruneApps(),
	)
	if err := root.Execute(); err != nil {
		os.Exit(1)
	}
}

func cmdListBackups() *cobra.Command {
	return &cobra.Command{
		Use:   "list-backups",
		Short: "List all stored backups",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runListBackups()
		},
	}
}

func runListBackups() error {
	db, err := openDB()
	if err != nil {
		return err
	}
	defer db.Close()

	records, err := getAllBackups(db)
	if err != nil {
		return err
	}

	if len(records) == 0 {
		fmt.Println("No backups found.")
		return nil
	}

	table := tablewriter.NewWriter(os.Stdout)
	table.SetHeader([]string{"ID", "Device", "Product", "iOS", "Created", "Path"})
	for _, r := range records {
		table.Append([]string{
			strconv.Itoa(r.ID),
			r.DeviceName,
			r.ProductType,
			r.IOSVersion,
			r.CreatedAt,
			r.BackupPath,
		})
	}
	table.Render()
	return nil
}
