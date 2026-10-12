package gw

import (
	"encoding/json"
	"os"
	"time"

	"mlserving/gateway/internal/auth"
	"mlserving/gateway/internal/canary"
	"mlserving/gateway/internal/circuit"
	"mlserving/gateway/internal/ratelimit"
	"mlserving/gateway/internal/route"
)

type keyConfig struct {
	Secret  string   `json:"secret"`
	Scopes  []string `json:"scopes"`
	RateRPS float64  `json:"rate_rps"`
	Burst   float64  `json:"burst"`
}

type backendConfig struct {
	Name   string `json:"name"`
	URL    string `json:"url"`
	Canary bool   `json:"canary"`
}

type modelConfig struct {
	Backends             []backendConfig `json:"backends"`
	CanaryWeight         float64         `json:"canary_weight"`
	CanaryErrorThreshold float64         `json:"canary_error_threshold"`
	CanaryMinSamples     int             `json:"canary_min_samples"`
	CanaryWindowMs       int             `json:"canary_window_ms"`
}

// Config is the on-disk gateway configuration.
type Config struct {
	Listen            string                 `json:"listen"`
	Mode              string                 `json:"mode"`
	FeatureURL        string                 `json:"feature_url"`
	FetchFeatures     bool                   `json:"fetch_features"`
	APIKeys           []keyConfig            `json:"api_keys"`
	Models            map[string]modelConfig `json:"models"`
	HedgeAfterMs      int                    `json:"hedge_after_ms"`
	RequestTimeoutMs  int                    `json:"request_timeout_ms"`
	MaxAttempts       int                    `json:"max_attempts"`
	BreakerFailures   int                    `json:"breaker_failures"`
	BreakerCooldownMs int                    `json:"breaker_cooldown_ms"`
	FeatureCacheMs    int                    `json:"feature_cache_ms"`
	RetryBaseMs       int                    `json:"retry_base_ms"`
	RetryMaxMs        int                    `json:"retry_max_ms"`
}

func Load(path string) (Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Config{}, err
	}
	var cfg Config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return Config{}, err
	}
	cfg.applyDefaults()
	return cfg, nil
}

func (c *Config) applyDefaults() {
	if c.Mode == "" {
		c.Mode = "gateway"
	}
	if c.RequestTimeoutMs <= 0 {
		c.RequestTimeoutMs = 2000
	}
	if c.MaxAttempts <= 0 {
		if c.Mode == "naive" {
			c.MaxAttempts = 1
		} else {
			c.MaxAttempts = 3
		}
	}
	if c.BreakerFailures <= 0 {
		c.BreakerFailures = 3
	}
	if c.BreakerCooldownMs <= 0 {
		c.BreakerCooldownMs = 800
	}
	if c.RetryMaxMs <= 0 {
		c.RetryMaxMs = 15
	}
	if c.Mode == "naive" {
		c.HedgeAfterMs = 0
		c.FetchFeatures = false
		c.MaxAttempts = 1
	}
}

func (c Config) authenticator() *auth.Authenticator {
	keys := make([]auth.Principal, 0, len(c.APIKeys))
	for _, key := range c.APIKeys {
		scopes := make(map[string]struct{}, len(key.Scopes))
		for _, scope := range key.Scopes {
			scopes[scope] = struct{}{}
		}
		keys = append(keys, auth.Principal{Secret: key.Secret, Scopes: scopes})
	}
	return auth.New(keys)
}

func (c Config) limiter(now time.Time) *ratelimit.Limiter {
	rates := make(map[string][2]float64, len(c.APIKeys))
	for _, key := range c.APIKeys {
		rate, burst := key.RateRPS, key.Burst
		if rate <= 0 {
			rate = 1
		}
		if burst <= 0 {
			burst = 1
		}
		rates[key.Secret] = [2]float64{rate, burst}
	}
	return ratelimit.NewLimiter(rates, now)
}

func (c Config) pool() (*route.Pool, map[string]*canary.Controller) {
	pool := route.NewPool()
	controllers := map[string]*canary.Controller{}
	cooldown := time.Duration(c.BreakerCooldownMs) * time.Millisecond
	for name, model := range c.Models {
		ctrl := canary.New(
			model.CanaryWeight,
			model.CanaryErrorThreshold,
			model.CanaryMinSamples,
			time.Duration(model.CanaryWindowMs)*time.Millisecond,
		)
		if model.CanaryWeight > 0 {
			ctrl.SetWeight(model.CanaryWeight, time.Now())
		}
		controllers[name] = ctrl
		for _, backend := range model.Backends {
			pool.Add(name, route.NewBackend(
				backend.Name,
				backend.URL,
				backend.Canary,
				circuit.New(c.BreakerFailures, cooldown),
			))
		}
	}
	return pool, controllers
}
