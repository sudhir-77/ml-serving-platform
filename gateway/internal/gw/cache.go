package gw

import (
	"sync"
	"time"
)

type cacheItem struct {
	values []float64
	exp    time.Time
}

// Cache stores feature vectors for a short TTL so a warm gateway stays inside its overhead budget.
type Cache struct {
	mu  sync.Mutex
	ttl time.Duration
	m   map[string]cacheItem
}

func NewCache(ttl time.Duration) *Cache {
	return &Cache{ttl: ttl, m: map[string]cacheItem{}}
}

func (c *Cache) Get(key string, now time.Time) ([]float64, bool) {
	if c == nil || c.ttl <= 0 {
		return nil, false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	item, ok := c.m[key]
	if !ok || !now.Before(item.exp) {
		delete(c.m, key)
		return nil, false
	}
	out := make([]float64, len(item.values))
	copy(out, item.values)
	return out, true
}

func (c *Cache) Set(key string, values []float64, now time.Time) {
	if c == nil || c.ttl <= 0 {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	copied := make([]float64, len(values))
	copy(copied, values)
	c.m[key] = cacheItem{values: copied, exp: now.Add(c.ttl)}
}
