package gw

import (
	"testing"
	"time"
)

func TestJitterBounds(t *testing.T) {
	base := 10 * time.Millisecond
	max := 25 * time.Millisecond
	if Jitter(0, base, max, 1) != 0 {
		t.Fatal("first attempt has no delay")
	}
	if got := Jitter(1, base, max, 0.5); got != 5*time.Millisecond {
		t.Fatalf("attempt 1: got %s", got)
	}
	if got := Jitter(2, base, max, 0.5); got != 10*time.Millisecond {
		t.Fatalf("attempt 2: got %s", got)
	}
	if got := Jitter(8, base, max, 1); got != max {
		t.Fatalf("cap: got %s", got)
	}
}
