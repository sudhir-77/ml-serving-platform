package route

import (
	"testing"
	"time"

	"mlserving/gateway/internal/canary"
	"mlserving/gateway/internal/circuit"
)

func TestPickPrefersLowEWMAAndSkipsOpenBreakers(t *testing.T) {
	now := time.Unix(0, 0)
	slow := NewBackend("rank-a", "http://a", false, circuit.New(2, time.Second))
	fast := NewBackend("rank-b", "http://b", false, circuit.New(2, time.Second))
	slow.Observe(40)
	fast.Observe(4)
	pool := NewPool()
	pool.Add("ranking", slow)
	pool.Add("ranking", fast)
	primary, hedge := pool.Pick("ranking", 0.9, now, nil, nil)
	if primary.Name != "rank-b" || hedge.Name != "rank-a" {
		t.Fatalf("got primary=%s hedge=%s", primary.Name, hedge.Name)
	}
	slow.Breaker.Record(false, now)
	slow.Breaker.Record(false, now)
	primary, hedge = pool.Pick("ranking", 0.9, now, nil, nil)
	if primary.Name != "rank-b" || hedge != nil {
		t.Fatalf("open breaker should leave only the fast replica, primary=%s hedge=%v", primary.Name, hedge)
	}
	pool.Add("ranking", fast)
	if got := len(pool.Snaps("ranking")); got != 2 {
		t.Fatalf("duplicate add should be ignored, snaps=%d", got)
	}
}

func TestCanaryDrawAndNaiveOnly(t *testing.T) {
	now := time.Unix(0, 0)
	primary := NewBackend("rank-a", "http://a", false, circuit.New(3, time.Second))
	other := NewBackend("rank-b", "http://b", false, circuit.New(3, time.Second))
	can := NewBackend("rank-v2", "http://v2", true, circuit.New(3, time.Second))
	pool := NewPool()
	pool.Add("ranking", primary)
	pool.Add("ranking", other)
	pool.Add("ranking", can)
	ctrl := canary.New(0.1, 0.5, 10, time.Second)
	got, hedge := pool.Pick("ranking", 0.05, now, ctrl, nil)
	if got.Name != "rank-v2" || hedge.Name != "rank-a" {
		t.Fatalf("low draw should select the canary, got %s hedge %v", got.Name, hedge)
	}
	got, _ = pool.Pick("ranking", 0.5, now, ctrl, map[string]bool{"rank-a": true})
	if got.Name != "rank-b" {
		t.Fatalf("exclude should skip rank-a, got %s", got.Name)
	}
	if pool.Only("ranking").Name != "rank-a" {
		t.Fatal("naive target is the first backend")
	}
	if pool.Only("missing") != nil {
		t.Fatal("missing model should be nil")
	}
}

func TestEqualEWMABreaksTiesByName(t *testing.T) {
	pool := NewPool()
	pool.Add("ranking", NewBackend("rank-b", "http://b", false, circuit.New(1, time.Second)))
	pool.Add("ranking", NewBackend("rank-a", "http://a", false, circuit.New(1, time.Second)))
	got, hedge := pool.Pick("ranking", 1, time.Unix(0, 0), nil, nil)
	if got.Name != "rank-a" || hedge.Name != "rank-b" {
		t.Fatalf("tie break failed: %s %s", got.Name, hedge.Name)
	}
}
