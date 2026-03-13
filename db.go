package main

import (
	"database/sql"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

const dbFile = "iphonetools.db"

func openDB() (*sql.DB, error) {
	db, err := sql.Open("sqlite", dbFile)
	if err != nil {
		return nil, err
	}
	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS backups (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		device_udid TEXT,
		device_name TEXT,
		product_type TEXT,
		ios_version TEXT,
		backup_path TEXT,
		encryption_password TEXT,
		created_at TEXT
	)`)
	if err != nil {
		db.Close()
		return nil, err
	}
	return db, nil
}

type BackupRecord struct {
	ID          int
	DeviceUDID  string
	DeviceName  string
	ProductType string
	IOSVersion  string
	BackupPath  string
	Password    string
	CreatedAt   string
}

func insertBackup(db *sql.DB, b BackupRecord) (int64, error) {
	res, err := db.Exec(
		`INSERT INTO backups (device_udid, device_name, product_type, ios_version, backup_path, encryption_password, created_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?)`,
		b.DeviceUDID, b.DeviceName, b.ProductType, b.IOSVersion,
		b.BackupPath, b.Password, time.Now().Format("2006-01-02 15:04:05"),
	)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

func getAllBackups(db *sql.DB) ([]BackupRecord, error) {
	rows, err := db.Query(`SELECT id, device_udid, device_name, product_type, ios_version, backup_path, encryption_password, created_at FROM backups ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var records []BackupRecord
	for rows.Next() {
		var b BackupRecord
		if err := rows.Scan(&b.ID, &b.DeviceUDID, &b.DeviceName, &b.ProductType, &b.IOSVersion, &b.BackupPath, &b.Password, &b.CreatedAt); err != nil {
			return nil, err
		}
		records = append(records, b)
	}
	return records, nil
}

func getBackupByID(db *sql.DB, id int) (*BackupRecord, error) {
	var b BackupRecord
	err := db.QueryRow(
		`SELECT id, device_udid, device_name, product_type, ios_version, backup_path, encryption_password, created_at FROM backups WHERE id=?`, id,
	).Scan(&b.ID, &b.DeviceUDID, &b.DeviceName, &b.ProductType, &b.IOSVersion, &b.BackupPath, &b.Password, &b.CreatedAt)
	if err != nil {
		return nil, fmt.Errorf("backup ID %d not found", id)
	}
	return &b, nil
}
