package circuit

import (
	"sync"
	"time"
)

type state int

const (
	closed state = iota
	open
	halfOpen
)

// Breaker stops calling a backend after repeated failures and lets one probe through later.
type Breaker struct {
	mu        sync.Mutex
	threshold int
	cooldown  time.Duration
	state     state
	failures  int
	openUntil time.Time
	probeOut  bool
}

func New(threshold int, cooldown time.Duration) *Breaker {
	if threshold < 1 {
		threshold = 1
	}
	return &Breaker{threshold: threshold, cooldown: cooldown, state: closed}
}

// Allow reports whether a call may be sent at time now.
func (b *Breaker) Allow(now time.Time) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	switch b.state {
	case closed:
		return true
	case open:
		if now.Before(b.openUntil) {
			return false
		}
		b.state = halfOpen
		b.probeOut = true
		return true
	default:
		if b.probeOut {
			return false
		}
		b.probeOut = true
		return true
	}
}

// Record updates the breaker from the outcome of a call that was allowed to run.
func (b *Breaker) Record(success bool, now time.Time) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if success {
		b.failures = 0
		b.state = closed
		b.probeOut = false
		return
	}
	b.probeOut = false
	b.failures++
	if b.failures >= b.threshold || b.state == halfOpen {
		b.state = open
		b.openUntil = now.Add(b.cooldown)
	}
}

func (b *Breaker) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	switch b.state {
	case open:
		return "open"
	case halfOpen:
		return "half-open"
	default:
		return "closed"
	}
}
