package main

import (
	"database/sql"
	"encoding/csv"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/olekukonko/tablewriter"
	"github.com/spf13/cobra"
	"howett.net/plist"
	_ "modernc.org/sqlite"
)

func cmdExtractTexts() *cobra.Command {
	var limit int
	var output string

	cmd := &cobra.Command{
		Use:   "extract-texts <backup-id>",
		Short: "Extract SMS/iMessage texts from a backup",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("invalid backup ID: %s", args[0])
			}
			return runExtractTexts(id, limit, output)
		},
	}
	cmd.Flags().IntVar(&limit, "limit", 0, "limit number of messages (0 = all)")
	cmd.Flags().StringVar(&output, "output", "", "save results to CSV file")
	return cmd
}

func runExtractTexts(id, limit int, output string) error {
	db, err := openDB()
	if err != nil {
		return err
	}
	defer db.Close()

	rec, err := getBackupByID(db, id)
	if err != nil {
		return err
	}

	fmt.Println("Reading Manifest.plist...")
	manifestData, err := os.ReadFile(filepath.Join(rec.BackupPath, "Manifest.plist"))
	if err != nil {
		return fmt.Errorf("cannot read Manifest.plist: %w", err)
	}

	var manifest map[string]interface{}
	if _, err := plist.Unmarshal(manifestData, &manifest); err != nil {
		return fmt.Errorf("cannot parse Manifest.plist: %w", err)
	}

	keybagData, ok := manifest["BackupKeyBag"].([]byte)
	if !ok {
		return fmt.Errorf("no BackupKeyBag in Manifest.plist")
	}
	manifestKeyRaw, ok := manifest["ManifestKey"].([]byte)
	if !ok {
		return fmt.Errorf("no ManifestKey in Manifest.plist — is this an encrypted backup?")
	}

	fmt.Println("Deriving keys from password...")
	kb, err := ParseKeybag(keybagData, rec.Password)
	if err != nil {
		return fmt.Errorf("keybag error: %w", err)
	}

	manifestDbKey, err := decryptManifestKey(manifestKeyRaw, kb)
	if err != nil {
		return fmt.Errorf("cannot decrypt ManifestKey: %w", err)
	}

	fmt.Println("Decrypting Manifest.db...")
	encManifestDB, err := os.ReadFile(filepath.Join(rec.BackupPath, "Manifest.db"))
	if err != nil {
		return fmt.Errorf("cannot read Manifest.db: %w", err)
	}
	decManifestDB, err := decryptAESCBC(manifestDbKey, encManifestDB)
	if err != nil {
		return fmt.Errorf("cannot decrypt Manifest.db: %w", err)
	}

	tmpManifest, err := os.CreateTemp("", "manifest-*.db")
	if err != nil {
		return err
	}
	defer os.Remove(tmpManifest.Name())
	if _, err := tmpManifest.Write(decManifestDB); err != nil {
		return err
	}
	tmpManifest.Close()

	mdb, err := sql.Open("sqlite", tmpManifest.Name())
	if err != nil {
		return fmt.Errorf("cannot open decrypted Manifest.db: %w", err)
	}
	defer mdb.Close()

	var fileID string
	var fileBplist []byte
	err = mdb.QueryRow(
		"SELECT fileID, file FROM Files WHERE domain='HomeDomain' AND relativePath='Library/SMS/sms.db'",
	).Scan(&fileID, &fileBplist)
	if err != nil {
		return fmt.Errorf("sms.db not found in Manifest.db: %w", err)
	}

	class, wrappedKey, err := parseFileEncryptionKey(fileBplist)
	if err != nil {
		return fmt.Errorf("cannot parse sms.db encryption key: %w", err)
	}
	if wrappedKey == nil {
		return fmt.Errorf("sms.db appears to be unencrypted — unexpected")
	}

	smsKey, err := kb.UnwrapKey(class, wrappedKey)
	if err != nil {
		return fmt.Errorf("cannot unwrap sms.db key: %w", err)
	}

	encSMSPath := filepath.Join(rec.BackupPath, fileID[:2], fileID)
	encSMSData, err := os.ReadFile(encSMSPath)
	if err != nil {
		return fmt.Errorf("cannot read encrypted sms.db (%s): %w", encSMSPath, err)
	}

	decSMSData, err := decryptAESCBC(smsKey, encSMSData)
	if err != nil {
		return fmt.Errorf("cannot decrypt sms.db: %w", err)
	}

	tmpSMS, err := os.CreateTemp("", "sms-*.db")
	if err != nil {
		return err
	}
	defer os.Remove(tmpSMS.Name())
	if _, err := tmpSMS.Write(decSMSData); err != nil {
		return err
	}
	tmpSMS.Close()

	smsDB, err := sql.Open("sqlite", tmpSMS.Name())
	if err != nil {
		return fmt.Errorf("cannot open sms.db: %w", err)
	}
	defer smsDB.Close()

	query := `
		SELECT m.ROWID, m.date, m.is_from_me, COALESCE(m.text, ''), COALESCE(h.id, '')
		FROM message m
		LEFT JOIN handle h ON m.handle_id = h.ROWID
		ORDER BY m.date DESC`
	if limit > 0 {
		query += fmt.Sprintf(" LIMIT %d", limit)
	}

	rows, err := smsDB.Query(query)
	if err != nil {
		return fmt.Errorf("SMS query failed: %w", err)
	}
	defer rows.Close()

	type Message struct {
		ID        int
		Date      string
		Direction string
		Text      string
		Contact   string
	}

	var messages []Message
	for rows.Next() {
		var msgID int
		var rawDate int64
		var fromMe int
		var text, contact string
		if err := rows.Scan(&msgID, &rawDate, &fromMe, &text, &contact); err != nil {
			continue
		}
		// Apple timestamp: nanoseconds since 2001-01-01
		var t time.Time
		const appleEpoch = int64(978307200)
		if rawDate > 1_000_000_000_000 {
			t = time.Unix(rawDate/1_000_000_000+appleEpoch, rawDate%1_000_000_000)
		} else {
			t = time.Unix(rawDate+appleEpoch, 0)
		}
		dir := "Received"
		if fromMe == 1 {
			dir = "Sent"
		}
		messages = append(messages, Message{
			ID:        msgID,
			Date:      t.Format("2006-01-02 15:04:05"),
			Direction: dir,
			Text:      text,
			Contact:   contact,
		})
	}

	fmt.Printf("Found %d messages\n\n", len(messages))

	if output != "" {
		f, err := os.Create(output)
		if err != nil {
			return fmt.Errorf("cannot create output file: %w", err)
		}
		defer f.Close()
		w := csv.NewWriter(f)
		_ = w.Write([]string{"ID", "Date", "Direction", "Contact", "Text"})
		for _, m := range messages {
			_ = w.Write([]string{strconv.Itoa(m.ID), m.Date, m.Direction, m.Contact, m.Text})
		}
		w.Flush()
		fmt.Printf("Saved to %s\n", output)
	} else {
		table := tablewriter.NewWriter(os.Stdout)
		table.SetHeader([]string{"Date", "Direction", "Contact", "Text"})
		table.SetColWidth(60)
		for _, m := range messages {
			text := m.Text
			if len(text) > 80 {
				text = text[:77] + "..."
			}
			table.Append([]string{m.Date, m.Direction, m.Contact, text})
		}
		table.Render()
	}

	return nil
}
