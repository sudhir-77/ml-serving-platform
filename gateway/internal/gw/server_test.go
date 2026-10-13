package gw

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func writeScore(w http.ResponseWriter, score float64) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]float64{"score": score})
}

func TestGatewayUsesFeatureServerNotClientFeatures(t *testing.T) {
	var hits atomic.Int32
	features := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{"features": []float64{42, 0, 0, 0, 0, 0}})
	}))
	defer features.Close()
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Features []float64 `json:"features"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		writeScore(w, body.Features[0])
	}))
	defer model.Close()
	cfg := baseConfig(features.URL, model.URL, model.URL)
	server := httptest.NewServer(New(cfg).Handler())
	defer server.Close()

	for i := 0; i < 2; i++ {
		res := postPredict(t, server.URL, "rank-key", `{"model":"ranking","user_id":1,"item_id":2,"features":[1,2,3,4,5,6]}`)
		if res.StatusCode != 200 {
			body, _ := io.ReadAll(res.Body)
			t.Fatalf("status %d body %s", res.StatusCode, body)
		}
		var got predictResponse
		_ = json.NewDecoder(res.Body).Decode(&got)
		res.Body.Close()
		if got.Score != 42 {
			t.Fatalf("gateway should serve fetched features, score=%v", got.Score)
		}
	}
	if hits.Load() != 1 {
		t.Fatalf("feature cache should collapse two lookups into one, hits=%d", hits.Load())
	}
}

func TestCanaryFailureIsNotRetried(t *testing.T) {
	var stableHits atomic.Int32
	canarySrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "injected", http.StatusInternalServerError)
	}))
	defer canarySrv.Close()
	stable := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		stableHits.Add(1)
		writeScore(w, 1)
	}))
	defer stable.Close()
	cfg := baseConfig(featuresURL(t), stable.URL, stable.URL)
	cfg.FetchFeatures = false
	cfg.HedgeAfterMs = 0
	cfg.MaxAttempts = 3
	cfg.Models["ranking"] = modelConfig{
		Backends: []backendConfig{
			{Name: "rank-b", URL: stable.URL},
			{Name: "ranking-v2", URL: canarySrv.URL, Canary: true},
		},
		CanaryWeight:         1,
		CanaryErrorThreshold: 0.99,
		CanaryMinSamples:     50,
		CanaryWindowMs:       60000,
	}
	gw := httptest.NewServer(New(cfg).Handler())
	defer gw.Close()
	res := postPredict(t, gw.URL, "rank-key", `{"model":"ranking","user_id":1,"item_id":1,"features":[1,2,3,4,5,6]}`)
	body, _ := io.ReadAll(res.Body)
	res.Body.Close()
	if res.StatusCode != http.StatusInternalServerError {
		t.Fatalf("canary error should surface, status %d body %s", res.StatusCode, body)
	}
	var got predictResponse
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	if got.Backend != "ranking-v2" {
		t.Fatalf("backend = %s, want ranking-v2", got.Backend)
	}
	if stableHits.Load() != 0 {
		t.Fatalf("stable replica was called %d times", stableHits.Load())
	}
}

func TestHedgePrefersTheFastReplica(t *testing.T) {
	features := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"features": []float64{1, 0, 0, 0, 0, 0}})
	}))
	defer features.Close()
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(300 * time.Millisecond)
		writeScore(w, 1)
	}))
	defer slow.Close()
	fast := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeScore(w, 2)
	}))
	defer fast.Close()
	cfg := baseConfig(features.URL, slow.URL, fast.URL)
	cfg.HedgeAfterMs = 25
	cfg.MaxAttempts = 1
	gw := httptest.NewServer(New(cfg).Handler())
	defer gw.Close()
	start := time.Now()
	res := postPredict(t, gw.URL, "rank-key", `{"model":"ranking","user_id":3,"item_id":4}`)
	elapsed := time.Since(start)
	var got predictResponse
	_ = json.NewDecoder(res.Body).Decode(&got)
	res.Body.Close()
	if res.StatusCode != 200 || got.Backend != "rank-b" || got.Score != 2 {
		t.Fatalf("status=%d backend=%s score=%v", res.StatusCode, got.Backend, got.Score)
	}
	if elapsed > 200*time.Millisecond {
		t.Fatalf("hedged request took %s", elapsed)
	}
}

func TestAuthScopesAndRateLimit(t *testing.T) {
	features := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"features": []float64{1}})
	}))
	defer features.Close()
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeScore(w, 1)
	}))
	defer model.Close()
	cfg := baseConfig(features.URL, model.URL, model.URL)
	cfg.APIKeys = append(cfg.APIKeys, keyConfig{Secret: "limited", Scopes: []string{"ranking"}, RateRPS: 0.01, Burst: 1})
	gw := httptest.NewServer(New(cfg).Handler())
	defer gw.Close()

	if postPredict(t, gw.URL, "", `{"model":"ranking","user_id":1,"item_id":1}`).StatusCode != 401 {
		t.Fatal("missing key")
	}
	if postPredict(t, gw.URL, "nope", `{"model":"ranking","user_id":1,"item_id":1}`).StatusCode != 401 {
		t.Fatal("unknown key")
	}
	if postPredict(t, gw.URL, "text-key", `{"model":"ranking","user_id":1,"item_id":1}`).StatusCode != 403 {
		t.Fatal("out of scope")
	}
	if postPredict(t, gw.URL, "limited", `{"model":"ranking","user_id":1,"item_id":1}`).StatusCode != 200 {
		t.Fatal("first limited call should pass")
	}
	if postPredict(t, gw.URL, "limited", `{"model":"ranking","user_id":1,"item_id":1}`).StatusCode != 429 {
		t.Fatal("second limited call should be rate limited")
	}
	res := postRaw(t, gw.URL+"/v1/predict", "rank-key", "{")
	if res.StatusCode != 400 {
		t.Fatalf("bad json: %d", res.StatusCode)
	}
}

func TestCanaryRollsBackAfterErrors(t *testing.T) {
	features := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"features": []float64{1}})
	}))
	defer features.Close()
	stable := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeScore(w, 3)
	}))
	defer stable.Close()
	bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "bad", http.StatusInternalServerError)
	}))
	defer bad.Close()
	cfg := baseConfig(features.URL, stable.URL, stable.URL)
	cfg.BreakerFailures = 100
	cfg.Models["ranking"] = modelConfig{
		Backends: []backendConfig{
			{Name: "rank-a", URL: stable.URL},
			{Name: "rank-v2", URL: bad.URL, Canary: true},
		},
		CanaryWeight:         0,
		CanaryErrorThreshold: 0.5,
		CanaryMinSamples:     4,
		CanaryWindowMs:       5000,
	}
	handler := New(cfg).Handler()
	gw := httptest.NewServer(handler)
	defer gw.Close()
	admin, err := http.Post(gw.URL+"/admin/canary", "application/json", strings.NewReader(`{"model":"ranking","weight":1}`))
	if err != nil || admin.StatusCode != 200 {
		t.Fatalf("arm canary: %v %v", err, admin)
	}
	admin.Body.Close()
	for i := 0; i < 4; i++ {
		res := postPredict(t, gw.URL, "rank-key", `{"model":"ranking","user_id":1,"item_id":1}`)
		if res.StatusCode != 200 {
			t.Fatalf("failover should hide canary errors, status %d", res.StatusCode)
		}
		res.Body.Close()
	}
	metrics := getJSON(t, gw.URL+"/metrics")
	if metrics["canary_rolled_back"] != true {
		t.Fatalf("metrics: %#v", metrics)
	}
	if metrics["canary_weight"].(float64) != 0 {
		t.Fatalf("weight: %#v", metrics["canary_weight"])
	}
}

func TestNaiveForwardsFeaturesWithoutTheFeatureServer(t *testing.T) {
	var hits atomic.Int32
	features := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{"features": []float64{9}})
	}))
	defer features.Close()
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Features []float64 `json:"features"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		writeScore(w, body.Features[0])
	}))
	defer model.Close()
	cfg := baseConfig(features.URL, model.URL, model.URL)
	cfg.Mode = "naive"
	cfg.FetchFeatures = true
	gw := httptest.NewServer(New(cfg).Handler())
	defer gw.Close()
	res := postPredict(t, gw.URL, "", `{"model":"ranking","features":[7,1,1,1,1,1]}`)
	var got predictResponse
	_ = json.NewDecoder(res.Body).Decode(&got)
	res.Body.Close()
	if res.StatusCode != 200 || got.Score != 7 || got.Attempts != 1 {
		t.Fatalf("naive status=%d score=%v attempts=%d", res.StatusCode, got.Score, got.Attempts)
	}
	if hits.Load() != 0 {
		t.Fatal("naive proxy must not call the feature server")
	}
}

func TestStreamAddBackendAndLoadConfig(t *testing.T) {
	text := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeScore(w, 4)
	}))
	defer text.Close()
	cfg := baseConfig("http://127.0.0.1:1", text.URL, text.URL)
	cfg.Models["text"] = modelConfig{Backends: []backendConfig{{Name: "text-a", URL: text.URL}}}
	dir := t.TempDir()
	path := filepath.Join(dir, "gw.json")
	raw, _ := json.Marshal(cfg)
	if err := writeFile(path, raw); err != nil {
		t.Fatal(err)
	}
	loaded, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	gw := httptest.NewServer(New(loaded).Handler())
	defer gw.Close()
	res, err := http.NewRequest(http.MethodGet, gw.URL+"/v1/stream?model=text&text=hello+world", nil)
	if err != nil {
		t.Fatal(err)
	}
	res.Header.Set("X-API-Key", "text-key")
	resp, err := http.DefaultClient.Do(res)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 || !strings.Contains(string(body), "data:") || !strings.Contains(string(body), "hello") {
		t.Fatalf("stream status=%d body=%s", resp.StatusCode, body)
	}
	health, err := http.Get(gw.URL + "/health")
	if err != nil || health.StatusCode != 200 {
		t.Fatalf("health: %v", err)
	}
	health.Body.Close()

	empty := baseConfig("http://127.0.0.1:9", "http://127.0.0.1:9", "http://127.0.0.1:9")
	empty.Models["ranking"] = modelConfig{Backends: nil, CanaryMinSamples: 1, CanaryWindowMs: 1000}
	empty.FetchFeatures = false
	bare := httptest.NewServer(New(empty).Handler())
	defer bare.Close()
	missing := postPredict(t, bare.URL, "rank-key", `{"model":"ranking","features":[1,2,3]}`)
	if missing.StatusCode != 503 {
		t.Fatalf("expected 503 with no backends, got %d", missing.StatusCode)
	}
	missing.Body.Close()
	add, err := http.Post(bare.URL+"/admin/backends", "application/json", strings.NewReader(`{"model":"ranking","name":"rank-a","url":"`+text.URL+`"}`))
	if err != nil || add.StatusCode != 200 {
		t.Fatalf("add backend %v", err)
	}
	add.Body.Close()
	fixed := postPredict(t, bare.URL, "rank-key", `{"model":"ranking","features":[1,2,3]}`)
	if fixed.StatusCode != 200 {
		b, _ := io.ReadAll(fixed.Body)
		t.Fatalf("after add: %d %s", fixed.StatusCode, b)
	}
	fixed.Body.Close()
	if New(cfg).Shutdown(context.Background()) != nil {
		t.Fatal("shutdown before listen should be nil")
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := ln.Addr().(*net.TCPAddr).Port
	ln.Close()
	liveCfg := baseConfig(featuresURL(t), text.URL, text.URL)
	liveCfg.Listen = fmt.Sprintf("127.0.0.1:%d", port)
	liveCfg.FetchFeatures = false
	live := New(liveCfg)
	done := make(chan error, 1)
	go func() { done <- live.ListenAndServe() }()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		resp, err := http.Get(fmt.Sprintf("http://127.0.0.1:%d/health", port))
		if err == nil {
			resp.Body.Close()
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err := live.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("server did not stop")
	}
}

func featuresURL(t *testing.T) string {
	t.Helper()
	return "http://127.0.0.1:9"
}

func baseConfig(featureURL, a, b string) Config {
	return Config{
		Mode:          "gateway",
		Listen:        "127.0.0.1:0",
		FeatureURL:    featureURL,
		FetchFeatures: true,
		APIKeys: []keyConfig{
			{Secret: "rank-key", Scopes: []string{"ranking"}, RateRPS: 1000, Burst: 1000},
			{Secret: "text-key", Scopes: []string{"text"}, RateRPS: 1000, Burst: 1000},
		},
		Models: map[string]modelConfig{
			"ranking": {
				Backends: []backendConfig{
					{Name: "rank-a", URL: a},
					{Name: "rank-b", URL: b},
				},
				CanaryMinSamples: 20,
				CanaryWindowMs:   2000,
			},
			"text": {Backends: []backendConfig{{Name: "text-a", URL: b}}},
		},
		HedgeAfterMs:      30,
		RequestTimeoutMs:  1000,
		MaxAttempts:       3,
		BreakerFailures:   4,
		BreakerCooldownMs: 200,
		FeatureCacheMs:    5000,
		RetryBaseMs:       0,
		RetryMaxMs:        1,
	}
}

func postPredict(t *testing.T, base, key, body string) *http.Response {
	t.Helper()
	return postRaw(t, base+"/v1/predict", key, body)
}

func postRaw(t *testing.T, url, key, body string) *http.Response {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, url, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("X-API-Key", key)
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return res
}

func getJSON(t *testing.T, url string) map[string]any {
	t.Helper()
	res, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var out map[string]any
	if err := json.NewDecoder(res.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	return out
}

func writeFile(path string, raw []byte) error {
	return osWrite(path, raw)
}
