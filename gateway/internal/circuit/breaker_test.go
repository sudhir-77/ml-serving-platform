package circuit

import (
	"testing"
	"time"
)

func TestBreakerOpensAndProbes(t *testing.T) {
	start := time.Unix(0, 0)
	b := New(2, 100*time.Millisecond)
	if !b.Allow(start) {
		t.Fatal("closed breaker should allow")
	}
	b.Record(false, start)
	if b.String() != "closed" {
		t.Fatalf("one failure should stay closed, got %s", b.String())
	}
	b.Record(false, start)
	if b.String() != "open" {
		t.Fatalf("threshold should open the breaker, got %s", b.String())
	}
	if b.Allow(start.Add(50 * time.Millisecond)) {
		t.Fatal("open breaker should deny before cooldown")
	}
	if !b.Allow(start.Add(100 * time.Millisecond)) {
		t.Fatal("cooldown expiry should allow one probe")
	}
	if b.Allow(start.Add(100 * time.Millisecond)) {
		t.Fatal("half-open should allow only one probe")
	}
	b.Record(false, start.Add(100*time.Millisecond))
	if b.String() != "open" {
		t.Fatal("failed probe should re-open")
	}
	if !b.Allow(start.Add(250 * time.Millisecond)) {
		t.Fatal("second cooldown should allow another probe")
	}
	b.Record(true, start.Add(250*time.Millisecond))
	if b.String() != "closed" {
		t.Fatal("successful probe should close")
	}
}
