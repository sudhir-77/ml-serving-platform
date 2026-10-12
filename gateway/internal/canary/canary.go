package canary

import (
	"sync"
	"time"
)

// Controller shifts a fraction of traffic to a canary and drops that weight after errors.
type Controller struct {
	mu          sync.Mutex
	weight      float64
	threshold   float64
	minSamples  int
	window      time.Duration
	errors      int
	total       int
	windowStart time.Time
	rolledBack  bool
	armed       bool
	armedAt     time.Time
	rollbackAt  time.Time
}

func New(weight, threshold float64, minSamples int, window time.Duration) *Controller {
	if threshold <= 0 {
		threshold = 0.4
	}
	if minSamples < 1 {
		minSamples = 1
	}
	if window <= 0 {
		window = 2 * time.Second
	}
	return &Controller{weight: weight, threshold: threshold, minSamples: minSamples, window: window}
}

// SetWeight arms the controller the first time the weight becomes positive.
// A rolled-back controller ignores later attempts to raise the weight.
func (c *Controller) SetWeight(weight float64, now time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.rolledBack {
		c.weight = 0
		return
	}
	if weight > 0 && !c.armed {
		c.armed = true
		c.armedAt = now
	}
	if weight < 0 {
		weight = 0
	}
	if weight > 1 {
		weight = 1
	}
	c.weight = weight
}

// PickCanary is true when draw falls inside the current weight.
func (c *Controller) PickCanary(draw float64) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return !c.rolledBack && c.weight > 0 && draw < c.weight
}

// Record counts canary outcomes and rolls the weight back to zero past the error threshold.
func (c *Controller) Record(isCanary, failed bool, now time.Time) {
	if !isCanary {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.rolledBack {
		return
	}
	if c.windowStart.IsZero() || now.Sub(c.windowStart) > c.window {
		c.windowStart = now
		c.errors = 0
		c.total = 0
	}
	c.total++
	if failed {
		c.errors++
	}
	if c.total >= c.minSamples && float64(c.errors)/float64(c.total) >= c.threshold {
		c.weight = 0
		c.rolledBack = true
		c.rollbackAt = now
		if !c.armed {
			c.armed = true
			c.armedAt = now
		}
	}
}

// Snapshot returns the weight, rollback flag, and unix milliseconds of arm and rollback.
func (c *Controller) Snapshot() (float64, bool, int64, int64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	var armed, rolled int64
	if c.armed {
		armed = c.armedAt.UnixMilli()
	}
	if c.rolledBack {
		rolled = c.rollbackAt.UnixMilli()
	}
	return c.weight, c.rolledBack, armed, rolled
}
