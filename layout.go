package main

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/sha1"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"

	"github.com/spf13/cobra"
	"howett.net/plist"
	_ "modernc.org/sqlite"
)

// LayoutItem represents one slot on a home screen page.
// It is either a plain app (BundleID set) or an opaque non-app item
// (widget, smart stack, folder) preserved verbatim in RawItem.
type LayoutItem struct {
	BundleID string      `json:"bundleID,omitempty"`
	RawItem  interface{} `json:"raw,omitempty"`
}

func (li LayoutItem) MarshalJSON() ([]byte, error) {
	if li.BundleID != "" {
		return json.Marshal(li.BundleID)
	}
	return json.Marshal(li.RawItem)
}

func (li *LayoutItem) UnmarshalJSON(b []byte) error {
	var s string
	if json.Unmarshal(b, &s) == nil {
		li.BundleID = s
		return nil
	}
	return json.Unmarshal(b, &li.RawItem)
}

// Layout is the exported/imported home screen layout.
type Layout struct {
	Pages [][]LayoutItem `json:"pages"`
	Dock  []LayoutItem   `json:"dock"`
}

// ── Commands ──────────────────────────────────────────────────────────────────

func cmdShowLayout() *cobra.Command {
	return &cobra.Command{
		Use:   "show-layout <backup-id>",
		Short: "Display the home screen icon layout from a backup",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("invalid backup ID: %s", args[0])
			}
			layout, err := extractLayout(id)
			if err != nil {
				return err
			}
			printLayout(layout)
			return nil
		},
	}
}

func cmdExportLayout() *cobra.Command {
	return &cobra.Command{
		Use:   "export-layout <backup-id> <output.json>",
		Short: "Export home screen layout to a JSON file",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("invalid backup ID: %s", args[0])
			}
			layout, err := extractLayout(id)
			if err != nil {
				return err
			}
			data, err := json.MarshalIndent(layout, "", "  ")
			if err != nil {
				return err
			}
			if err := os.WriteFile(args[1], data, 0644); err != nil {
				return err
			}
			fmt.Printf("Layout exported to %s\n", args[1])
			fmt.Printf("  %d pages, %d dock items\n", len(layout.Pages), len(layout.Dock))
			return nil
		},
	}
}

func cmdImportLayout() *cobra.Command {
	return &cobra.Command{
		Use:   "import-layout <backup-id> <input.json>",
		Short: "Write a modified layout back into a backup (ready to restore)",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("invalid backup ID: %s", args[0])
			}
			data, err := os.ReadFile(args[1])
			if err != nil {
				return fmt.Errorf("cannot read %s: %w", args[1], err)
			}
			var layout Layout
			if err := json.Unmarshal(data, &layout); err != nil {
				return fmt.Errorf("invalid JSON: %w", err)
			}
			return writeLayout(id, layout)
		},
	}
}

// ── Extract ───────────────────────────────────────────────────────────────────

func extractLayout(backupID int) (*Layout, error) {
	db, err := openDB()
	if err != nil {
		return nil, err
	}
	defer db.Close()

	rec, err := getBackupByID(db, backupID)
	if err != nil {
		return nil, err
	}

	kb, manifestDbKey, err := openKeybag(rec)
	if err != nil {
		return nil, err
	}

	decMDB, err := decryptManifestDB(rec.BackupPath, manifestDbKey)
	if err != nil {
		return nil, err
	}

	iconData, _, _, err := extractBackupFile(rec.BackupPath, decMDB, "HomeDomain", "Library/SpringBoard/IconState.plist", kb)
	if err != nil {
		return nil, fmt.Errorf("cannot extract IconState.plist: %w", err)
	}

	var raw map[string]interface{}
	if _, err := plist.Unmarshal(stripPKCS7(iconData), &raw); err != nil {
		return nil, fmt.Errorf("cannot parse IconState.plist: %w", err)
	}

	return rawToLayout(raw), nil
}

// rawToLayout converts the raw IconState plist into our Layout structure.
func rawToLayout(raw map[string]interface{}) *Layout {
	layout := &Layout{}

	if pages, ok := raw["iconLists"].([]interface{}); ok {
		for _, p := range pages {
			page, _ := p.([]interface{})
			var items []LayoutItem
			for _, item := range page {
				items = append(items, toLayoutItem(item))
			}
			layout.Pages = append(layout.Pages, items)
		}
	}

	if dock, ok := raw["buttonBar"].([]interface{}); ok {
		for _, item := range dock {
			layout.Dock = append(layout.Dock, toLayoutItem(item))
		}
	}

	return layout
}

func toLayoutItem(v interface{}) LayoutItem {
	if s, ok := v.(string); ok {
		return LayoutItem{BundleID: s}
	}
	return LayoutItem{RawItem: v}
}

// layoutToRaw converts our Layout back to the IconState plist map.
func layoutToRaw(layout Layout, original map[string]interface{}) map[string]interface{} {
	result := make(map[string]interface{})
	for k, v := range original {
		result[k] = v
	}

	var pages []interface{}
	for _, page := range layout.Pages {
		var items []interface{}
		for _, item := range page {
			if item.BundleID != "" {
				items = append(items, item.BundleID)
			} else {
				items = append(items, item.RawItem)
			}
		}
		pages = append(pages, items)
	}
	result["iconLists"] = pages

	var dock []interface{}
	for _, item := range layout.Dock {
		if item.BundleID != "" {
			dock = append(dock, item.BundleID)
		} else {
			dock = append(dock, item.RawItem)
		}
	}
	result["buttonBar"] = dock

	return result
}

// ── Write ─────────────────────────────────────────────────────────────────────

func writeLayout(backupID int, layout Layout) error {
	db, err := openDB()
	if err != nil {
		return err
	}
	defer db.Close()

	rec, err := getBackupByID(db, backupID)
	if err != nil {
		return err
	}

	kb, manifestDbKey, err := openKeybag(rec)
	if err != nil {
		return err
	}

	decMDB, err := decryptManifestDB(rec.BackupPath, manifestDbKey)
	if err != nil {
		return err
	}

	// Extract original IconState.plist to preserve non-layout keys (widgets, etc.)
	origData, fileID, fileMeta, err := extractBackupFile(rec.BackupPath, decMDB, "HomeDomain", "Library/SpringBoard/IconState.plist", kb)
	if err != nil {
		return fmt.Errorf("cannot extract IconState.plist: %w", err)
	}
	origContent := stripPKCS7(origData)

	var origRaw map[string]interface{}
	if _, err := plist.Unmarshal(origContent, &origRaw); err != nil {
		return fmt.Errorf("cannot parse original IconState.plist: %w", err)
	}

	// Merge new layout into original (preserves keys we don't touch)
	newRaw := layoutToRaw(layout, origRaw)

	// Serialize to binary plist
	newContent, err := plist.Marshal(newRaw, plist.BinaryFormat)
	if err != nil {
		return fmt.Errorf("cannot serialize new layout: %w", err)
	}

	// Compute SHA1 of new content (for Manifest.db Digest update)
	h := sha1.New()
	h.Write(newContent)
	newDigest := h.Sum(nil)
	newSize := uint64(len(newContent))

	// Get the original file's encryption key
	class, wrapped, err := parseFileEncryptionKey(fileMeta)
	if err != nil {
		return fmt.Errorf("cannot parse file encryption key: %w", err)
	}
	fileKey, err := kb.UnwrapKey(class, wrapped)
	if err != nil {
		return fmt.Errorf("cannot unwrap file key: %w", err)
	}

	// Encrypt new content (PKCS#7 pad, then AES-CBC zero IV)
	padded := pkcs7Pad(newContent, 16)
	encrypted, err := encryptAESCBC(fileKey, padded)
	if err != nil {
		return fmt.Errorf("cannot encrypt new IconState.plist: %w", err)
	}

	// Write encrypted file back to backup directory
	encPath := filepath.Join(rec.BackupPath, fileID[:2], fileID)
	if err := os.WriteFile(encPath, encrypted, 0600); err != nil {
		return fmt.Errorf("cannot write encrypted file: %w", err)
	}
	fmt.Printf("Wrote encrypted IconState.plist (%d bytes)\n", len(encrypted))

	// Update Manifest.db: patch Digest and Size in file metadata
	updatedMeta, err := patchFileMetadata(fileMeta, newSize, newDigest)
	if err != nil {
		fmt.Printf("Warning: could not patch file metadata: %v\n", err)
		fmt.Println("Restore may fail due to checksum mismatch.")
	} else {
		decMDB, err = updateManifestDB(decMDB, fileID, updatedMeta)
		if err != nil {
			return fmt.Errorf("cannot update Manifest.db: %w", err)
		}
		fmt.Println("Updated Manifest.db with new digest and size.")
	}

	// Re-encrypt Manifest.db
	paddedMDB := pkcs7Pad(decMDB, 16)
	encMDB, err := encryptAESCBC(manifestDbKey, paddedMDB)
	if err != nil {
		return fmt.Errorf("cannot re-encrypt Manifest.db: %w", err)
	}
	if err := os.WriteFile(filepath.Join(rec.BackupPath, "Manifest.db"), encMDB, 0600); err != nil {
		return fmt.Errorf("cannot write Manifest.db: %w", err)
	}
	fmt.Println("Re-encrypted Manifest.db.")
	fmt.Println("\nDone. Run: idevicebackup2 restore --system <backup-dir>")
	return nil
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// openKeybag opens the backup's keybag and returns the Keybag and ManifestDb key.
func openKeybag(rec *BackupRecord) (*Keybag, []byte, error) {
	manifestData, err := os.ReadFile(filepath.Join(rec.BackupPath, "Manifest.plist"))
	if err != nil {
		return nil, nil, fmt.Errorf("cannot read Manifest.plist: %w", err)
	}
	var manifest map[string]interface{}
	if _, err := plist.Unmarshal(manifestData, &manifest); err != nil {
		return nil, nil, fmt.Errorf("cannot parse Manifest.plist: %w", err)
	}
	keybagData, ok := manifest["BackupKeyBag"].([]byte)
	if !ok {
		return nil, nil, fmt.Errorf("no BackupKeyBag in Manifest.plist")
	}
	manifestKeyRaw, ok := manifest["ManifestKey"].([]byte)
	if !ok {
		return nil, nil, fmt.Errorf("no ManifestKey in Manifest.plist")
	}
	kb, err := ParseKeybag(keybagData, rec.Password)
	if err != nil {
		return nil, nil, fmt.Errorf("keybag error: %w", err)
	}
	manifestDbKey, err := decryptManifestKey(manifestKeyRaw, kb)
	if err != nil {
		return nil, nil, fmt.Errorf("cannot decrypt ManifestKey: %w", err)
	}
	return kb, manifestDbKey, nil
}

// decryptManifestDB decrypts Manifest.db and returns the raw bytes.
func decryptManifestDB(backupPath string, manifestDbKey []byte) ([]byte, error) {
	enc, err := os.ReadFile(filepath.Join(backupPath, "Manifest.db"))
	if err != nil {
		return nil, fmt.Errorf("cannot read Manifest.db: %w", err)
	}
	dec, err := decryptAESCBC(manifestDbKey, enc)
	if err != nil {
		return nil, fmt.Errorf("cannot decrypt Manifest.db: %w", err)
	}
	return dec, nil
}

// extractBackupFile decrypts a single file from the backup.
// Returns: decrypted content, fileID, raw file metadata plist bytes, error.
func extractBackupFile(backupPath string, decMDB []byte, domain, relativePath string, kb *Keybag) ([]byte, string, []byte, error) {
	tmp, err := os.CreateTemp("", "manifest-*.db")
	if err != nil {
		return nil, "", nil, err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(decMDB); err != nil {
		return nil, "", nil, err
	}
	tmp.Close()

	mdb, err := sql.Open("sqlite", tmp.Name())
	if err != nil {
		return nil, "", nil, err
	}
	defer mdb.Close()

	var fileID string
	var fileMeta []byte
	err = mdb.QueryRow(
		"SELECT fileID, file FROM Files WHERE domain=? AND relativePath=?",
		domain, relativePath,
	).Scan(&fileID, &fileMeta)
	if err != nil {
		return nil, "", nil, fmt.Errorf("file not found in Manifest.db (%s / %s): %w", domain, relativePath, err)
	}

	class, wrapped, err := parseFileEncryptionKey(fileMeta)
	if err != nil {
		return nil, fileID, fileMeta, fmt.Errorf("cannot parse encryption key: %w", err)
	}
	if wrapped == nil {
		// Not encrypted — read raw
		raw, err := os.ReadFile(filepath.Join(backupPath, fileID[:2], fileID))
		return raw, fileID, fileMeta, err
	}

	fileKey, err := kb.UnwrapKey(class, wrapped)
	if err != nil {
		return nil, fileID, fileMeta, fmt.Errorf("cannot unwrap file key: %w", err)
	}

	enc, err := os.ReadFile(filepath.Join(backupPath, fileID[:2], fileID))
	if err != nil {
		return nil, fileID, fileMeta, err
	}

	dec, err := decryptAESCBC(fileKey, enc)
	return dec, fileID, fileMeta, err
}

// updateManifestDB updates the `file` column for a given fileID and returns
// the modified Manifest.db bytes.
func updateManifestDB(decMDB []byte, fileID string, newMeta []byte) ([]byte, error) {
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
	_, err = mdb.Exec("UPDATE Files SET file=? WHERE fileID=?", newMeta, fileID)
	mdb.Close()
	if err != nil {
		return nil, err
	}

	return os.ReadFile(tmp.Name())
}

// pkcs7Pad pads data to a multiple of blockSize using PKCS#7.
func pkcs7Pad(data []byte, blockSize int) []byte {
	pad := blockSize - (len(data) % blockSize)
	return append(append([]byte(nil), data...), bytes.Repeat([]byte{byte(pad)}, pad)...)
}

// encryptAESCBC encrypts data with AES-CBC using a zero IV.
func encryptAESCBC(key, data []byte) ([]byte, error) {
	if len(data)%16 != 0 {
		return nil, fmt.Errorf("data length %d is not a multiple of 16", len(data))
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	out := make([]byte, len(data))
	iv := make([]byte, 16)
	cipher.NewCBCEncrypter(block, iv).CryptBlocks(out, data)
	return out, nil
}

// patchFileMetadata updates the Digest and Size fields in a NSKeyedArchiver
// binary plist (the `file` column from Manifest.db). Returns patched bytes.
func patchFileMetadata(data []byte, newSize uint64, newDigest []byte) ([]byte, error) {
	// Parse with howett.net/plist to navigate the NSKeyedArchiver structure
	var root interface{}
	if _, err := plist.Unmarshal(data, &root); err != nil {
		return nil, fmt.Errorf("cannot parse file metadata plist: %w", err)
	}
	rootMap, ok := root.(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("root is not a dict")
	}
	objects, ok := rootMap["$objects"].([]interface{})
	if !ok {
		return nil, fmt.Errorf("no $objects")
	}
	top, ok := rootMap["$top"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("no $top")
	}
	rootUID := int(top["root"].(plist.UID))

	// Parse the binary plist offset table
	offsets, refSize, err := parseBplistOffsets(data)
	if err != nil {
		return nil, err
	}

	result := append([]byte(nil), data...)

	// Navigate the raw binary plist dict at $objects[rootUID] to find
	// the object indices for "Digest" and "Size".
	dictKeyValIdx, err := readBplistDictRefs(data, offsets[rootUID], refSize)
	if err != nil {
		return nil, fmt.Errorf("cannot read root dict: %w", err)
	}

	// Resolve each key to find Digest and Size value object indices
	for keyIdx, valIdx := range dictKeyValIdx {
		keyStr, err := readBplistString(data, offsets[keyIdx])
		if err != nil {
			continue
		}
		switch keyStr {
		case "Digest":
			if err := patchBplistDataInPlace(result, offsets[valIdx], newDigest); err != nil {
				return nil, fmt.Errorf("Digest patch: %w", err)
			}
		case "Size":
			if err := patchBplistUint64InPlace(result, offsets[valIdx], newSize); err != nil {
				return nil, fmt.Errorf("Size patch: %w", err)
			}
		}
	}

	_ = objects
	return result, nil
}

// parseBplistOffsets parses the binary plist offset table from the trailer.
func parseBplistOffsets(data []byte) (offsets []uint64, refSize int, err error) {
	if len(data) < 40 || string(data[:8]) != "bplist00" {
		return nil, 0, fmt.Errorf("not a binary plist")
	}
	trailer := data[len(data)-32:]
	offsetIntSize := int(trailer[6])
	refSize = int(trailer[7])
	numObjects := binary.BigEndian.Uint64(trailer[8:16])
	offsetTableOffset := binary.BigEndian.Uint64(trailer[24:32])

	offsets = make([]uint64, numObjects)
	for i := uint64(0); i < numObjects; i++ {
		off := offsetTableOffset + i*uint64(offsetIntSize)
		switch offsetIntSize {
		case 1:
			offsets[i] = uint64(data[off])
		case 2:
			offsets[i] = uint64(binary.BigEndian.Uint16(data[off : off+2]))
		case 4:
			offsets[i] = uint64(binary.BigEndian.Uint32(data[off : off+4]))
		case 8:
			offsets[i] = binary.BigEndian.Uint64(data[off : off+8])
		}
	}
	return offsets, refSize, nil
}

// readBplistRef reads a single object reference at pos.
func readBplistRef(data []byte, pos int, refSize int) int {
	switch refSize {
	case 1:
		return int(data[pos])
	case 2:
		return int(binary.BigEndian.Uint16(data[pos : pos+2]))
	default:
		return int(binary.BigEndian.Uint32(data[pos : pos+4]))
	}
}

// readBplistDictRefs reads a binary plist dict and returns a map of keyObjIdx -> valObjIdx.
func readBplistDictRefs(data []byte, offset uint64, refSize int) (map[int]int, error) {
	marker := data[offset]
	if marker>>4 != 0xD {
		return nil, fmt.Errorf("expected dict marker, got 0x%02x", marker)
	}
	pos := int(offset) + 1
	count := int(marker & 0x0F)
	if count == 0x0F {
		// Count stored in following integer object
		intMarker := data[pos]
		pos++
		intSize := 1 << (intMarker & 0x0F)
		switch intSize {
		case 1:
			count = int(data[pos])
			pos++
		case 2:
			count = int(binary.BigEndian.Uint16(data[pos : pos+2]))
			pos += 2
		case 4:
			count = int(binary.BigEndian.Uint32(data[pos : pos+4]))
			pos += 4
		case 8:
			count = int(binary.BigEndian.Uint64(data[pos : pos+8]))
			pos += 8
		}
	}
	keyRefs := make([]int, count)
	valRefs := make([]int, count)
	for i := 0; i < count; i++ {
		keyRefs[i] = readBplistRef(data, pos, refSize)
		pos += refSize
	}
	for i := 0; i < count; i++ {
		valRefs[i] = readBplistRef(data, pos, refSize)
		pos += refSize
	}
	result := make(map[int]int, count)
	for i := 0; i < count; i++ {
		result[keyRefs[i]] = valRefs[i]
	}
	return result, nil
}

// readBplistString reads an ASCII or short UTF-16 string object from the binary plist.
func readBplistString(data []byte, offset uint64) (string, error) {
	marker := data[offset]
	typ := marker >> 4
	if typ != 0x5 && typ != 0x6 {
		return "", fmt.Errorf("not a string (0x%02x)", marker)
	}
	pos := int(offset) + 1
	length := int(marker & 0x0F)
	if length == 0x0F {
		intMarker := data[pos]
		pos++
		intSize := 1 << (intMarker & 0x0F)
		switch intSize {
		case 1:
			length = int(data[pos])
			pos++
		case 2:
			length = int(binary.BigEndian.Uint16(data[pos : pos+2]))
			pos += 2
		}
	}
	if typ == 0x5 {
		return string(data[pos : pos+length]), nil
	}
	// UTF-16BE
	runes := make([]rune, length)
	for i := 0; i < length; i++ {
		runes[i] = rune(binary.BigEndian.Uint16(data[pos+i*2 : pos+i*2+2]))
	}
	return string(runes), nil
}

// patchBplistDataInPlace replaces the bytes of a binary data object in-place.
// Fails if the new data length differs from the existing object's data length.
func patchBplistDataInPlace(data []byte, offset uint64, newBytes []byte) error {
	marker := data[offset]
	if marker>>4 != 0x4 {
		return fmt.Errorf("expected data object (0x4x), got 0x%02x", marker)
	}
	pos := int(offset) + 1
	length := int(marker & 0x0F)
	if length == 0x0F {
		intMarker := data[pos]
		pos++
		intSize := 1 << (intMarker & 0x0F)
		switch intSize {
		case 1:
			length = int(data[pos])
			pos++
		case 2:
			length = int(binary.BigEndian.Uint16(data[pos : pos+2]))
			pos += 2
		}
	}
	if length != len(newBytes) {
		return fmt.Errorf("length mismatch: object has %d bytes, new data has %d", length, len(newBytes))
	}
	copy(data[pos:pos+length], newBytes)
	return nil
}

// patchBplistUint64InPlace replaces the value of an 8-byte integer object in-place.
func patchBplistUint64InPlace(data []byte, offset uint64, newVal uint64) error {
	marker := data[offset]
	if marker>>4 != 0x1 {
		return fmt.Errorf("expected integer object (0x1x), got 0x%02x", marker)
	}
	intSize := 1 << (marker & 0x0F)
	if intSize != 8 {
		// Smaller integer: can only patch if new value fits
		switch intSize {
		case 1:
			if newVal > 0xFF {
				return fmt.Errorf("new value %d too large for 1-byte integer", newVal)
			}
			data[offset+1] = byte(newVal)
		case 2:
			if newVal > 0xFFFF {
				return fmt.Errorf("new value %d too large for 2-byte integer", newVal)
			}
			binary.BigEndian.PutUint16(data[offset+1:offset+3], uint16(newVal))
		case 4:
			if newVal > 0xFFFFFFFF {
				return fmt.Errorf("new value %d too large for 4-byte integer", newVal)
			}
			binary.BigEndian.PutUint32(data[offset+1:offset+5], uint32(newVal))
		}
		return nil
	}
	binary.BigEndian.PutUint64(data[offset+1:offset+9], newVal)
	return nil
}

// ── Display ───────────────────────────────────────────────────────────────────

func printLayout(layout *Layout) {
	fmt.Printf("Dock (%d items):\n", len(layout.Dock))
	for i, item := range layout.Dock {
		fmt.Printf("  %d. %s\n", i+1, describeItem(item))
	}
	fmt.Println()
	for p, page := range layout.Pages {
		appCount := 0
		for _, item := range page {
			if item.BundleID != "" {
				appCount++
			}
		}
		fmt.Printf("Page %d (%d items, %d apps):\n", p+1, len(page), appCount)
		for i, item := range page {
			fmt.Printf("  %d. %s\n", i+1, describeItem(item))
		}
		fmt.Println()
	}
}

func describeItem(item LayoutItem) string {
	if item.BundleID != "" {
		return item.BundleID
	}
	if m, ok := item.RawItem.(map[string]interface{}); ok {
		iconType, _ := m["iconType"].(string)
		if iconType == "custom" {
			gridSize, _ := m["gridSize"].(string)
			if elems, ok := m["elements"].([]interface{}); ok && len(elems) > 0 {
				return fmt.Sprintf("[SmartStack:%s, %d widgets]", gridSize, len(elems))
			}
			return fmt.Sprintf("[Widget:%s]", gridSize)
		}
		if name, ok := m["displayName"].(string); ok && name != "" && name != "Folder" {
			return fmt.Sprintf("[Folder: %s]", name)
		}
		return "[Widget/Folder]"
	}
	return "[unknown]"
}
