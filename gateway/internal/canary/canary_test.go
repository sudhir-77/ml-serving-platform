package canary

import (
	"testing"
	"time"
)

func TestWeightsAndRollback(t *testing.T) {
	now := time.Unix(1_700_000_000, 0)
	c := New(0, 0.5, 4, time.Second)
	if c.PickCanary(0.0) {
		t.Fatal("weight 0 should not pick the canary")
	}
	c.SetWeight(0.10, now)
	if !c.PickCanary(0.05) {
		t.Fatal("draw 0.05 should hit a 0.10 canary")
	}
	if c.PickCanary(0.50) {
		t.Fatal("draw 0.50 should stay on primary")
	}
	for i := 0; i < 3; i++ {
		c.Record(true, true, now.Add(time.Duration(i)*time.Millisecond))
	}
	if _, rolled, _, _ := c.Snapshot(); rolled {
		t.Fatal("should wait for min samples")
	}
	c.Record(true, true, now.Add(10*time.Millisecond))
	weight, rolled, armed, rollback := c.Snapshot()
	if !rolled || weight != 0 {
		t.Fatalf("expected rollback to weight 0, got weight=%v rolled=%v", weight, rolled)
	}
	if rollback < armed {
		t.Fatal("rollback time should follow arm time")
	}
	c.SetWeight(0.5, now.Add(time.Second))
	if c.PickCanary(0.0) {
		t.Fatal("rolled back canary must stay at weight 0")
	}
	c.Record(false, true, now)
	if _, rolled2, _, _ := c.Snapshot(); !rolled2 {
		t.Fatal("primary failures must not clear rollback")
	}
}

func TestWindowReset(t *testing.T) {
	now := time.Unix(0, 0)
	c := New(0.2, 0.5, 4, 100*time.Millisecond)
	c.SetWeight(0.2, now)
	for i := 0; i < 3; i++ {
		c.Record(true, true, now)
	}
	c.Record(true, false, now.Add(200*time.Millisecond))
	if _, rolled, _, _ := c.Snapshot(); rolled {
		t.Fatal("a new window should drop the old error count")
	}
}
