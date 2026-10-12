package ratelimit

import (
	"testing"
	"time"
)

func TestTokenBucketRefillsOnTheInjectedClock(t *testing.T) {
	start := time.Unix(0, 0)
	b := NewBucket(10, 2, start)
	if !b.Allow(start) || !b.Allow(start) {
		t.Fatal("burst of 2 should allow two immediate calls")
	}
	if b.Allow(start) {
		t.Fatal("third immediate call should be denied")
	}
	later := start.Add(100 * time.Millisecond)
	if !b.Allow(later) {
		t.Fatal("100ms at 10 tokens/s should refill one token")
	}
	if b.Allow(later) {
		t.Fatal("the same timestamp must not refill twice")
	}
}

func TestLimiterUnknownKey(t *testing.T) {
	l := NewLimiter(map[string][2]float64{"k": {1, 1}}, time.Unix(0, 0))
	if l.Allow("missing", time.Unix(0, 0)) {
		t.Fatal("unknown key must be denied")
	}
	if !l.Allow("k", time.Unix(0, 0)) {
		t.Fatal("known key should pass once")
	}
}
