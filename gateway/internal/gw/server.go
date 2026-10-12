package gw

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"mlserving/gateway/internal/canary"
	"mlserving/gateway/internal/circuit"
	"mlserving/gateway/internal/route"
)

type predictRequest struct {
	Model     string    `json:"model"`
	UserID    int       `json:"user_id"`
	ItemID    int       `json:"item_id"`
	RowID     int       `json:"row_id"`
	RequestID int       `json:"request_id"`
	Features  []float64 `json:"features"`
	Text      string    `json:"text"`
}

type predictResponse struct {
	Score    float64 `json:"score"`
	Backend  string  `json:"backend"`
	Attempts int     `json:"attempts"`
	Model    string  `json:"model"`
	Error    string  `json:"error,omitempty"`
}

type callResult struct {
	Score    float64
	Backend  string
	Attempts int
	Status   int
	OK       bool
	Err      error
}

// Server is the prediction gateway.
type Server struct {
	cfg   Config
	authn interface {
		Check(string, string) (int, error)
	}
	limits   interface{ Allow(string, time.Time) bool }
	pool     *route.Pool
	canaries map[string]*canary.Controller
	cache    *Cache
	client   *http.Client
	mu       sync.Mutex
	rng      *rand.Rand
	requests atomic.Uint64
	httpSrv  *http.Server
}

func New(cfg Config) *Server {
	cfg.applyDefaults()
	transport := &http.Transport{
		DialContext: (&net.Dialer{
			Timeout:   150 * time.Millisecond,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		MaxIdleConns:        256,
		MaxIdleConnsPerHost: 64,
		IdleConnTimeout:     60 * time.Second,
		DisableCompression:  true,
		ForceAttemptHTTP2:   false,
	}
	pool, canaries := cfg.pool()
	return &Server{
		cfg:      cfg,
		authn:    cfg.authenticator(),
		limits:   cfg.limiter(time.Now()),
		pool:     pool,
		canaries: canaries,
		cache:    NewCache(time.Duration(cfg.FeatureCacheMs) * time.Millisecond),
		client:   &http.Client{Transport: transport},
		rng:      rand.New(rand.NewSource(1)),
	}
}

// Handler exposes the HTTP routes.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /metrics", s.handleMetrics)
	mux.HandleFunc("POST /v1/predict", s.handlePredict)
	mux.HandleFunc("GET /v1/stream", s.handleStream)
	mux.HandleFunc("POST /admin/backends", s.handleAddBackend)
	mux.HandleFunc("POST /admin/canary", s.handleCanary)
	return mux
}

func (s *Server) ListenAndServe() error {
	s.httpSrv = &http.Server{
		Addr:              s.cfg.Listen,
		Handler:           s.Handler(),
		ReadHeaderTimeout: 2 * time.Second,
	}
	err := s.httpSrv.ListenAndServe()
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func (s *Server) Shutdown(ctx context.Context) error {
	if s.httpSrv == nil {
		return nil
	}
	return s.httpSrv.Shutdown(ctx)
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleMetrics(w http.ResponseWriter, _ *http.Request) {
	ctrl := s.canaries["ranking"]
	var weight float64
	var rolled bool
	var armed, rollback int64
	if ctrl != nil {
		weight, rolled, armed, rollback = ctrl.Snapshot()
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"requests":             s.requests.Load(),
		"canary_weight":        weight,
		"canary_rolled_back":   rolled,
		"canary_armed_unix_ms": armed,
		"rollback_unix_ms":     rollback,
		"backends":             s.pool.Snaps("ranking"),
	})
}

func (s *Server) handleAddBackend(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Model  string `json:"model"`
		Name   string `json:"name"`
		URL    string `json:"url"`
		Canary bool   `json:"canary"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Model == "" || body.Name == "" || body.URL == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "bad backend"})
		return
	}
	cooldown := time.Duration(s.cfg.BreakerCooldownMs) * time.Millisecond
	s.pool.Add(body.Model, route.NewBackend(body.Name, body.URL, body.Canary, circuit.New(s.cfg.BreakerFailures, cooldown)))
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleCanary(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Model  string  `json:"model"`
		Weight float64 `json:"weight"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Model == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "bad canary"})
		return
	}
	ctrl := s.canaries[body.Model]
	if ctrl == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "unknown model"})
		return
	}
	ctrl.SetWeight(body.Weight, time.Now())
	weight, rolled, armed, rollback := ctrl.Snapshot()
	writeJSON(w, http.StatusOK, map[string]any{
		"canary_weight":        weight,
		"canary_rolled_back":   rolled,
		"canary_armed_unix_ms": armed,
		"rollback_unix_ms":     rollback,
	})
}

func (s *Server) handlePredict(w http.ResponseWriter, r *http.Request) {
	s.requests.Add(1)
	var body predictRequest
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Model == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "bad request"})
		return
	}
	if s.cfg.Mode != "naive" {
		if code, err := s.authorize(r, body.Model); err != nil {
			writeJSON(w, code, map[string]string{"error": err.Error()})
			return
		}
	}
	features, code, err := s.featuresFor(r.Context(), body)
	if err != nil {
		writeJSON(w, code, map[string]string{"error": err.Error()})
		return
	}
	result := s.dispatch(r.Context(), body.Model, features, body.Text)
	status := http.StatusOK
	resp := predictResponse{Score: result.Score, Backend: result.Backend, Attempts: result.Attempts, Model: body.Model}
	if !result.OK {
		status = result.Status
		if status < 400 {
			status = http.StatusBadGateway
		}
		if result.Err != nil {
			resp.Error = result.Err.Error()
		}
	}
	writeJSON(w, status, resp)
}

func (s *Server) handleStream(w http.ResponseWriter, r *http.Request) {
	s.requests.Add(1)
	model := r.URL.Query().Get("model")
	if model == "" {
		model = "text"
	}
	if s.cfg.Mode != "naive" {
		if code, err := s.authorize(r, model); err != nil {
			writeJSON(w, code, map[string]string{"error": err.Error()})
			return
		}
	}
	text := r.URL.Query().Get("text")
	result := s.dispatch(r.Context(), model, nil, text)
	if !result.OK {
		status := result.Status
		if status < 400 {
			status = http.StatusBadGateway
		}
		writeJSON(w, status, map[string]string{"error": "upstream"})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.WriteHeader(http.StatusOK)
	flusher, _ := w.(http.Flusher)
	parts := strings.Fields(text)
	if len(parts) == 0 {
		parts = []string{""}
	}
	for i, part := range parts {
		fmt.Fprintf(w, "data: {\"token\":%q,\"i\":%d}\n\n", part, i)
		if flusher != nil {
			flusher.Flush()
		}
	}
	fmt.Fprintf(w, "data: {\"score\":%g,\"backend\":%q}\n\n", result.Score, result.Backend)
	if flusher != nil {
		flusher.Flush()
	}
}

func (s *Server) authorize(r *http.Request, model string) (int, error) {
	code, err := s.authn.Check(r.Header.Get("X-API-Key"), model)
	if err != nil {
		return code, err
	}
	if !s.limits.Allow(r.Header.Get("X-API-Key"), time.Now()) {
		return http.StatusTooManyRequests, errors.New("rate limit")
	}
	return http.StatusOK, nil
}

func (s *Server) featuresFor(ctx context.Context, body predictRequest) ([]float64, int, error) {
	if body.Model == "text" || !s.cfg.FetchFeatures {
		return body.Features, http.StatusOK, nil
	}
	key := fmt.Sprintf("%d:%d", body.UserID, body.ItemID)
	if cached, ok := s.cache.Get(key, time.Now()); ok {
		return cached, http.StatusOK, nil
	}
	ctx, cancel := context.WithTimeout(ctx, 200*time.Millisecond)
	defer cancel()
	var out struct {
		Features []float64 `json:"features"`
	}
	status, err := s.postJSON(ctx, s.cfg.FeatureURL+"/v1/vector", map[string]int{
		"user_id": body.UserID,
		"item_id": body.ItemID,
	}, &out)
	if err != nil || status != http.StatusOK || len(out.Features) == 0 {
		if err == nil {
			err = errors.New("empty features")
		}
		return nil, http.StatusBadGateway, err
	}
	s.cache.Set(key, out.Features, time.Now())
	return out.Features, http.StatusOK, nil
}

func (s *Server) dispatch(ctx context.Context, model string, features []float64, text string) callResult {
	if s.cfg.Mode == "naive" {
		backend := s.pool.Only(model)
		if backend == nil {
			return callResult{Status: http.StatusServiceUnavailable, Err: errors.New("no backend"), Attempts: 0}
		}
		result := s.callBackend(ctx, backend, model, features, text)
		result.Attempts = 1
		return result
	}
	exclude := map[string]bool{}
	var last callResult
	attempts := 0
	for attempts < s.cfg.MaxAttempts {
		primary, hedge := s.pool.Pick(model, s.draw(), time.Now(), s.canaries[model], exclude)
		if primary == nil {
			break
		}
		var wave callResult
		var n int
		if hedge == nil || s.cfg.HedgeAfterMs <= 0 {
			wave = s.callBackend(ctx, primary, model, features, text)
			n = 1
		} else {
			wave, n = s.race(ctx, primary, hedge, model, features, text)
		}
		attempts += n
		wave.Attempts = attempts
		last = wave
		if wave.OK {
			return last
		}
		exclude[primary.Name] = true
		if hedge != nil {
			exclude[hedge.Name] = true
		}
		if attempts < s.cfg.MaxAttempts {
			time.Sleep(s.backoff(attempts))
		}
	}
	if last.Status == 0 {
		last.Status = http.StatusServiceUnavailable
	}
	last.Attempts = attempts
	if last.Err == nil {
		last.Err = errors.New("no healthy backend")
	}
	return last
}

func (s *Server) race(parent context.Context, primary, hedge *route.Backend, model string, features []float64, text string) (callResult, int) {
	timeout := time.Duration(s.cfg.RequestTimeoutMs) * time.Millisecond
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	ch := make(chan callResult, 2)
	go func() {
		ch <- s.callBackend(ctx, primary, model, features, text)
	}()
	timer := time.NewTimer(time.Duration(s.cfg.HedgeAfterMs) * time.Millisecond)
	defer timer.Stop()
	select {
	case result := <-ch:
		if result.OK {
			return result, 1
		}
		second := s.callBackend(ctx, hedge, model, features, text)
		if second.OK {
			return second, 2
		}
		return result, 2
	case <-timer.C:
		go func() {
			ch <- s.callBackend(ctx, hedge, model, features, text)
		}()
		first := <-ch
		if first.OK {
			if first.Backend == hedge.Name {
				primary.Observe(500)
			}
			return first, 2
		}
		second := <-ch
		if second.OK {
			if second.Backend == hedge.Name {
				primary.Observe(500)
			}
			return second, 2
		}
		return first, 2
	case <-ctx.Done():
		return callResult{Status: http.StatusGatewayTimeout, Err: ctx.Err()}, 1
	}
}

func (s *Server) callBackend(ctx context.Context, backend *route.Backend, model string, features []float64, text string) callResult {
	if backend == nil {
		return callResult{Status: http.StatusServiceUnavailable, Err: errors.New("nil backend")}
	}
	payload := map[string]any{}
	if model == "text" {
		payload["text"] = text
	} else {
		payload["features"] = features
	}
	var out struct {
		Score float64 `json:"score"`
	}
	start := time.Now()
	status, err := s.postJSON(ctx, strings.TrimRight(backend.URL, "/")+"/predict", payload, &out)
	latency := time.Since(start).Seconds() * 1000
	if errors.Is(err, context.Canceled) {
		return callResult{Backend: backend.Name, Status: 499, Err: err, OK: false}
	}
	ok := err == nil && status == http.StatusOK
	backend.Observe(latency)
	if backend.Breaker != nil && !errors.Is(err, context.Canceled) {
		backend.Breaker.Record(ok, time.Now())
	}
	if ctrl := s.canaries[model]; ctrl != nil {
		ctrl.Record(backend.Canary, !ok, time.Now())
	}
	if !ok && status == 0 {
		status = http.StatusBadGateway
	}
	return callResult{Score: out.Score, Backend: backend.Name, Status: status, OK: ok, Err: err}
}

func (s *Server) postJSON(ctx context.Context, url string, body any, dest any) (int, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return 0, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(raw))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := s.client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return resp.StatusCode, err
	}
	if resp.StatusCode != http.StatusOK {
		return resp.StatusCode, fmt.Errorf("status %d", resp.StatusCode)
	}
	if dest != nil && len(data) > 0 {
		if err := json.Unmarshal(data, dest); err != nil {
			return resp.StatusCode, err
		}
	}
	return resp.StatusCode, nil
}

func (s *Server) draw() float64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.rng.Float64()
}

func (s *Server) backoff(attempt int) time.Duration {
	s.mu.Lock()
	unit := s.rng.Float64()
	s.mu.Unlock()
	return Jitter(
		attempt,
		time.Duration(s.cfg.RetryBaseMs)*time.Millisecond,
		time.Duration(s.cfg.RetryMaxMs)*time.Millisecond,
		unit,
	)
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
