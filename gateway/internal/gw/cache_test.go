package gw

import (
	"testing"
	"time"
)

func TestCacheHitAndExpiry(t *testing.T) {
	now := time.Unix(0, 0)
	c := NewCache(time.Second)
	if _, ok := c.Get("a", now); ok {
		t.Fatal("empty cache should miss")
	}
	c.Set("a", []float64{1, 2}, now)
	got, ok := c.Get("a", now.Add(500*time.Millisecond))
	if !ok || len(got) != 2 || got[0] != 1 {
		t.Fatalf("expected hit, got %v %v", got, ok)
	}
	got[0] = 9
	again, _ := c.Get("a", now.Add(500*time.Millisecond))
	if again[0] != 1 {
		t.Fatal("cache must not alias the caller slice")
	}
	if _, ok := c.Get("a", now.Add(time.Second)); ok {
		t.Fatal("expired entry should miss")
	}
	disabled := NewCache(0)
	disabled.Set("a", []float64{1}, now)
	if _, ok := disabled.Get("a", now); ok {
		t.Fatal("zero ttl disables the cache")
	}
}
