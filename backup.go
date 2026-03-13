package main

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

func cmdBackup() *cobra.Command {
	return &cobra.Command{
		Use:   "backup",
		Short: "Create an encrypted backup of a connected iOS device",
		RunE: func(cmd *cobra.Command, args []string) error {
			return runBackup()
		},
	}
}

func runBackup() error {
	out, err := exec.Command("idevice_id", "-l").Output()
	if err != nil {
		return fmt.Errorf("failed to list devices: %w", err)
	}

	var udids []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if l := strings.TrimSpace(line); l != "" {
			udids = append(udids, l)
		}
	}
	if len(udids) == 0 {
		return fmt.Errorf("no iOS device connected")
	}
	udid := udids[0]
	fmt.Printf("Found device: %s\n", udid)

	name := deviceInfo(udid, "DeviceName")
	product := deviceInfo(udid, "ProductType")
	ios := deviceInfo(udid, "ProductVersion")
	fmt.Printf("Device: %s (%s) iOS %s\n", name, product, ios)

	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return fmt.Errorf("failed to generate password: %w", err)
	}
	password := hex.EncodeToString(raw)

	sessionDir := filepath.Join("backups", time.Now().Format("20060102_150405"))
	if err := os.MkdirAll(sessionDir, 0700); err != nil {
		return fmt.Errorf("failed to create backup dir: %w", err)
	}

	fmt.Println("Enabling backup encryption...")
	encCmd := exec.Command("idevicebackup2", "-u", udid, "encryption", "on", password, sessionDir)
	encCmd.Stdout = os.Stdout
	encCmd.Stderr = os.Stderr
	if err := encCmd.Run(); err != nil {
		fmt.Printf("Warning: encryption setup returned error (may already be enabled): %v\n", err)
	}

	fmt.Println("Starting backup (this may take a while)...")
	bkpCmd := exec.Command("idevicebackup2", "-u", udid, "backup", "--full", sessionDir)
	bkpCmd.Stdout = os.Stdout
	bkpCmd.Stderr = os.Stderr
	if err := bkpCmd.Run(); err != nil {
		return fmt.Errorf("backup failed: %w", err)
	}

	backupPath := filepath.Join(sessionDir, udid)

	db, err := openDB()
	if err != nil {
		return err
	}
	defer db.Close()

	id, err := insertBackup(db, BackupRecord{
		DeviceUDID:  udid,
		DeviceName:  name,
		ProductType: product,
		IOSVersion:  ios,
		BackupPath:  backupPath,
		Password:    password,
	})
	if err != nil {
		return fmt.Errorf("failed to save backup record: %w", err)
	}

	fmt.Printf("\nBackup complete! ID: %d, Path: %s\n", id, backupPath)
	return nil
}

func deviceInfo(udid, key string) string {
	out, err := exec.Command("ideviceinfo", "-u", udid, "-k", key).Output()
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(out))
}
