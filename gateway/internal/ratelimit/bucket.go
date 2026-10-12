package ratelimit

import (
	"sync"
	"time"
)

// Bucket is a token bucket. Allow is called with an explicit clock so tests do not sleep.
type Bucket struct {
	rate   float64
	burst  float64
	tokens float64
	last   time.Time
}

func NewBucket(rate, burst float64, now time.Time) *Bucket {
	if burst < 1 {
		burst = 1
	}
	return &Bucket{rate: rate, burst: burst, tokens: burst, last: now}
}

// Allow spends one token if any are available at time now.
func (b *Bucket) Allow(now time.Time) bool {
	elapsed := now.Sub(b.last).Seconds()
	if elapsed > 0 {
		b.tokens += elapsed * b.rate
		if b.tokens > b.burst {
			b.tokens = b.burst
		}
		b.last = now
	} else if elapsed < 0 {
		b.last = now
	}
	if b.tokens < 1 {
		return false
	}
	b.tokens -= 1
	return true
}

// Limiter is one bucket per API key.
type Limiter struct {
	mu      sync.Mutex
	buckets map[string]*Bucket
}

func NewLimiter(rates map[string][2]float64, now time.Time) *Limiter {
	buckets := make(map[string]*Bucket, len(rates))
	for secret, pair := range rates {
		buckets[secret] = NewBucket(pair[0], pair[1], now)
	}
	return &Limiter{buckets: buckets}
}

func (l *Limiter) Allow(secret string, now time.Time) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	bucket, ok := l.buckets[secret]
	if !ok {
		return false
	}
	return bucket.Allow(now)
}
