package gw

import "os"

func osWrite(path string, raw []byte) error {
	return os.WriteFile(path, raw, 0o644)
}
