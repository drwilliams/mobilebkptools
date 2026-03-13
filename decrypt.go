package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/binary"
	"fmt"

	"golang.org/x/crypto/pbkdf2"
	"howett.net/plist"
)


// Keybag holds per-class AES keys derived from the backup password.
type Keybag struct {
	classKeys map[uint32][]byte
}

// ParseKeybag parses the BackupKeyBag blob from Manifest.plist and
// derives all class keys from the given password.
func ParseKeybag(data []byte, password string) (*Keybag, error) {
	type classEntry struct {
		class uint32
		wrap  uint32
		wpky  []byte
	}

	var (
		dpsl         []byte
		dpic         uint32
		salt         []byte
		iter         uint32
		entries      []*classEntry
		cur          *classEntry
		seenGlobalUUID bool
	)

	for i := 0; i+8 <= len(data); {
		tag := string(data[i : i+4])
		length := binary.BigEndian.Uint32(data[i+4 : i+8])
		i += 8
		if i+int(length) > len(data) {
			break
		}
		val := data[i : i+int(length)]
		i += int(length)

		switch tag {
		case "DPSL":
			dpsl = append([]byte(nil), val...)
		case "DPIC":
			if len(val) == 4 {
				dpic = binary.BigEndian.Uint32(val)
			}
		case "SALT":
			salt = append([]byte(nil), val...)
		case "ITER":
			if len(val) == 4 {
				iter = binary.BigEndian.Uint32(val)
			}
		case "UUID":
			if !seenGlobalUUID {
				// First UUID is the keybag's own UUID, not a class key entry
				seenGlobalUUID = true
			} else {
				if cur != nil {
					entries = append(entries, cur)
				}
				cur = &classEntry{}
			}
		case "CLAS":
			if cur != nil && len(val) == 4 {
				cur.class = binary.BigEndian.Uint32(val)
			}
		case "WRAP":
			if cur != nil && len(val) == 4 {
				cur.wrap = binary.BigEndian.Uint32(val)
			}
		case "WPKY":
			if cur != nil {
				cur.wpky = append([]byte(nil), val...)
			}
		}
	}
	if cur != nil {
		entries = append(entries, cur)
	}

	if len(dpsl) == 0 || dpic == 0 {
		return nil, fmt.Errorf("keybag missing DPSL or DPIC")
	}
	if len(salt) == 0 || iter == 0 {
		return nil, fmt.Errorf("keybag missing SALT or ITER")
	}

	// Two-step key derivation (matching the iphone-backup-decrypt Python library):
	// Step 1: PBKDF2-SHA256(passphrase, DPSL, DPIC) -> round1
	// Step 2: PBKDF2-SHA1(round1, SALT, ITER)       -> final key
	round1 := pbkdf2.Key([]byte(password), dpsl, int(dpic), 32, sha256.New)
	dk := pbkdf2.Key(round1, salt, int(iter), 32, sha1.New)

	kb := &Keybag{classKeys: make(map[uint32][]byte)}
	for _, e := range entries {
		if e.wrap&2 != 0 && len(e.wpky) > 0 {
			if unwrapped, err := aesKeyUnwrap(dk, e.wpky); err == nil {
				kb.classKeys[e.class] = unwrapped
			}
		}
	}
	return kb, nil
}

// UnwrapKey unwraps a file's wrapped encryption key using the appropriate class key.
func (kb *Keybag) UnwrapKey(class uint32, wrapped []byte) ([]byte, error) {
	classKey, ok := kb.classKeys[class]
	if !ok {
		return nil, fmt.Errorf("no class key for protection class %d", class)
	}
	return aesKeyUnwrap(classKey, wrapped)
}

// aesKeyUnwrap implements AES Key Unwrap per RFC 3394.
func aesKeyUnwrap(kek, data []byte) ([]byte, error) {
	if len(data) < 16 || len(data)%8 != 0 {
		return nil, fmt.Errorf("invalid wrapped key length %d", len(data))
	}
	n := len(data)/8 - 1
	r := make([][]byte, n+1)
	for i := 1; i <= n; i++ {
		r[i] = append([]byte(nil), data[i*8:(i+1)*8]...)
	}
	a := append([]byte(nil), data[:8]...)

	block, err := aes.NewCipher(kek)
	if err != nil {
		return nil, err
	}
	buf := make([]byte, 16)
	for j := 5; j >= 0; j-- {
		for i := n; i >= 1; i-- {
			t := uint64(n*j + i)
			av := binary.BigEndian.Uint64(a) ^ t
			binary.BigEndian.PutUint64(buf[:8], av)
			copy(buf[8:], r[i])
			block.Decrypt(buf, buf)
			copy(a, buf[:8])
			copy(r[i], buf[8:])
		}
	}

	expected := [8]byte{0xa6, 0xa6, 0xa6, 0xa6, 0xa6, 0xa6, 0xa6, 0xa6}
	for i, b := range a {
		if b != expected[i] {
			return nil, fmt.Errorf("AES key unwrap integrity check failed (wrong password?)")
		}
	}

	out := make([]byte, n*8)
	for i := 1; i <= n; i++ {
		copy(out[(i-1)*8:], r[i])
	}
	return out, nil
}

// stripPKCS7 removes PKCS#7 padding from decrypted data.
func stripPKCS7(data []byte) []byte {
	if len(data) == 0 {
		return data
	}
	pad := int(data[len(data)-1])
	if pad == 0 || pad > aes.BlockSize || pad > len(data) {
		return data
	}
	for _, b := range data[len(data)-pad:] {
		if int(b) != pad {
			return data
		}
	}
	return data[:len(data)-pad]
}

// decryptAESCBC decrypts ciphertext with AES-CBC using a zero IV.
func decryptAESCBC(key, data []byte) ([]byte, error) {
	if len(data)%aes.BlockSize != 0 {
		return nil, fmt.Errorf("ciphertext length %d is not a multiple of block size", len(data))
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	iv := make([]byte, aes.BlockSize)
	out := make([]byte, len(data))
	cipher.NewCBCDecrypter(block, iv).CryptBlocks(out, data)
	return out, nil
}

// decryptManifestKey derives the AES key for Manifest.db from the raw ManifestKey bytes.
func decryptManifestKey(manifestKey []byte, kb *Keybag) ([]byte, error) {
	if len(manifestKey) < 4 {
		return nil, fmt.Errorf("ManifestKey too short")
	}
	// Protection class is little-endian in ManifestKey/EncryptionKey fields,
	// unlike the keybag CLAS tags which are big-endian.
	class := binary.LittleEndian.Uint32(manifestKey[:4])
	return kb.UnwrapKey(class, manifestKey[4:])
}

// parseFileEncryptionKey extracts the protection class and wrapped key from
// the NSKeyedArchiver binary plist stored in Manifest.db's `file` column.
// Returns nil wrappedKey if the file is not encrypted.
func parseFileEncryptionKey(data []byte) (class uint32, wrappedKey []byte, err error) {
	var root interface{}
	if _, err = plist.Unmarshal(data, &root); err != nil {
		return 0, nil, fmt.Errorf("plist parse: %w", err)
	}

	rootMap, ok := root.(map[string]interface{})
	if !ok {
		return 0, nil, fmt.Errorf("root is not a dict")
	}
	objects, ok := rootMap["$objects"].([]interface{})
	if !ok {
		return 0, nil, fmt.Errorf("no $objects in plist")
	}
	top, ok := rootMap["$top"].(map[string]interface{})
	if !ok {
		return 0, nil, fmt.Errorf("no $top in plist")
	}
	rootUID, ok := top["root"].(plist.UID)
	if !ok {
		return 0, nil, fmt.Errorf("$top.root is not a UID")
	}
	if int(rootUID) >= len(objects) {
		return 0, nil, fmt.Errorf("root UID %d out of range", rootUID)
	}
	rootObj, ok := objects[rootUID].(map[string]interface{})
	if !ok {
		return 0, nil, fmt.Errorf("root object is not a dict")
	}

	encRef, ok := rootObj["EncryptionKey"]
	if !ok {
		return 0, nil, nil // not encrypted
	}
	encUID, ok := encRef.(plist.UID)
	if !ok {
		return 0, nil, fmt.Errorf("EncryptionKey ref is not a UID")
	}
	if int(encUID) >= len(objects) {
		return 0, nil, fmt.Errorf("EncryptionKey UID %d out of range", encUID)
	}

	var encData []byte
	switch v := objects[encUID].(type) {
	case []byte:
		encData = v
	case map[string]interface{}:
		if ns, ok := v["NS.data"].([]byte); ok {
			encData = ns
		} else {
			return 0, nil, fmt.Errorf("unexpected EncryptionKey dict format")
		}
	default:
		return 0, nil, fmt.Errorf("unexpected EncryptionKey type %T", objects[encUID])
	}

	if len(encData) < 4 {
		return 0, nil, fmt.Errorf("EncryptionKey data too short (%d bytes)", len(encData))
	}
	class = binary.LittleEndian.Uint32(encData[:4])
	wrappedKey = encData[4:]
	return class, wrappedKey, nil
}
