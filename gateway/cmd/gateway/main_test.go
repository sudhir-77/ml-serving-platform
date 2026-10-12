package main

import "testing"

func TestRunRejectsAMissingConfig(t *testing.T) {
	if err := run([]string{"does-not-exist.json"}); err == nil {
		t.Fatal("missing config should fail")
	}
}
