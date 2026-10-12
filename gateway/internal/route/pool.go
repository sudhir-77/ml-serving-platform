package route

import (
	"sort"
	"sync"
	"time"

	"mlserving/gateway/internal/canary"
	"mlserving/gateway/internal/circuit"
)

// Backend is one model replica. EWMA is the latency estimate in milliseconds.
type Backend struct {
	mu      sync.Mutex
	Name    string
	URL     string
	Canary  bool
	ewma    float64
	samples int
	Breaker *circuit.Breaker
}

func NewBackend(name, url string, canary bool, breaker *circuit.Breaker) *Backend {
	return &Backend{Name: name, URL: url, Canary: canary, Breaker: breaker}
}

func (b *Backend) Allow(now time.Time) bool {
	if b.Breaker == nil {
		return true
	}
	return b.Breaker.Allow(now)
}

func (b *Backend) EWMA() float64 {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.ewma
}

// Observe folds a latency sample into the EWMA. Lower is preferred.
func (b *Backend) Observe(latencyMs float64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	const alpha = 0.3
	if b.samples == 0 {
		b.ewma = latencyMs
	} else {
		b.ewma = alpha*latencyMs + (1-alpha)*b.ewma
	}
	b.samples++
}

// Snap is a read-only view for the metrics endpoint.
type Snap struct {
	Name    string  `json:"name"`
	URL     string  `json:"url"`
	Canary  bool    `json:"canary"`
	EWMA    float64 `json:"ewma_ms"`
	Breaker string  `json:"breaker"`
}

func (b *Backend) Snap() Snap {
	state := "closed"
	if b.Breaker != nil {
		state = b.Breaker.String()
	}
	return Snap{Name: b.Name, URL: b.URL, Canary: b.Canary, EWMA: b.EWMA(), Breaker: state}
}

// Pool holds replicas for each model name.
type Pool struct {
	mu      sync.Mutex
	byModel map[string][]*Backend
}

func NewPool() *Pool {
	return &Pool{byModel: map[string][]*Backend{}}
}

func (p *Pool) Add(model string, b *Backend) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, existing := range p.byModel[model] {
		if existing.Name == b.Name {
			return
		}
	}
	p.byModel[model] = append(p.byModel[model], b)
}

// Only returns the first backend. The naive proxy uses this and ignores health.
func (p *Pool) Only(model string) *Backend {
	p.mu.Lock()
	defer p.mu.Unlock()
	list := p.byModel[model]
	if len(list) == 0 {
		return nil
	}
	return list[0]
}

// Pick chooses a primary and an optional hedge target.
// Stable replicas are ordered by EWMA, then by name. Open breakers are skipped.
func (p *Pool) Pick(model string, draw float64, now time.Time, ctrl *canary.Controller, exclude map[string]bool) (*Backend, *Backend) {
	p.mu.Lock()
	list := append([]*Backend(nil), p.byModel[model]...)
	p.mu.Unlock()
	var stable []*Backend
	var can *Backend
	for _, b := range list {
		if exclude[b.Name] || !b.Allow(now) {
			continue
		}
		if b.Canary {
			can = b
			continue
		}
		stable = append(stable, b)
	}
	sort.Slice(stable, func(i, j int) bool {
		ei, ej := stable[i].EWMA(), stable[j].EWMA()
		if ei == ej {
			return stable[i].Name < stable[j].Name
		}
		return ei < ej
	})
	if can != nil && ctrl != nil && ctrl.PickCanary(draw) {
		var hedge *Backend
		if len(stable) > 0 {
			hedge = stable[0]
		}
		return can, hedge
	}
	if len(stable) == 0 {
		return nil, nil
	}
	var hedge *Backend
	if len(stable) > 1 {
		hedge = stable[1]
	}
	return stable[0], hedge
}

func (p *Pool) Snaps(model string) []Snap {
	p.mu.Lock()
	list := append([]*Backend(nil), p.byModel[model]...)
	p.mu.Unlock()
	out := make([]Snap, 0, len(list))
	for _, b := range list {
		out = append(out, b.Snap())
	}
	return out
}
