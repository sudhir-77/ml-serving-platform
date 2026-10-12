package main

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"sync"
	"time"
)

type row struct {
	RequestID int       `json:"request_id"`
	RowID     int       `json:"row_id"`
	UserID    int       `json:"user_id"`
	ItemID    int       `json:"item_id"`
	Features  []float64 `json:"features"`
	Text      string    `json:"text"`
}

func perTick(rps, tickMs int) int {
	if rps <= 0 {
		return 1
	}
	n := rps * tickMs / 1000
	if n < 1 {
		return 1
	}
	return n
}

func main() {
	url := flag.String("url", "", "base URL")
	direct := flag.String("direct", "", "direct model URL for the overhead scenario")
	scenario := flag.String("scenario", "load", "scenario name")
	mode := flag.String("mode", "gateway", "gateway, naive, or direct")
	target := flag.String("target", "gateway", "gateway, model, or features")
	rowsPath := flag.String("rows", "", "workload json")
	outPath := flag.String("out", "", "csv path")
	apiKey := flag.String("api-key", "both-key", "X-API-Key")
	model := flag.String("model", "ranking", "model name")
	n := flag.Int("n", 100, "requests")
	rps := flag.Int("rps", 100, "offered requests per second; 0 means as fast as one worker")
	concurrency := flag.Int("concurrency", 8, "workers")
	warmup := flag.Int("warmup", 0, "overhead pairs to discard")
	flag.Parse()
	if *url == "" || *rowsPath == "" || *outPath == "" {
		fmt.Fprintln(os.Stderr, "url, rows, and out are required")
		os.Exit(2)
	}
	rows, err := loadRows(*rowsPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if len(rows) == 0 {
		fmt.Fprintln(os.Stderr, "empty workload")
		os.Exit(1)
	}
	client := newClient()
	if *scenario == "overhead" {
		if err := runOverhead(client, rows, *url, *direct, *scenario, *apiKey, *model, *n, *warmup, *outPath); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if err := runPaced(client, rows, opts{
		url: *url, scenario: *scenario, mode: *mode, target: *target, apiKey: *apiKey, model: *model,
		n: *n, rps: *rps, concurrency: *concurrency, warmup: *warmup, out: *outPath,
	}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

type opts struct {
	url, scenario, mode, target, apiKey, model, out string
	n, rps, concurrency, warmup                      int
}

func newClient() *http.Client {
	transport := &http.Transport{
		DialContext:         (&net.Dialer{Timeout: 300 * time.Millisecond, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConns:        256,
		MaxIdleConnsPerHost: 128,
		IdleConnTimeout:     30 * time.Second,
		DisableCompression:  true,
	}
	return &http.Client{Transport: transport, Timeout: 3 * time.Second}
}

func loadRows(path string) ([]row, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var rows []row
	if err := json.Unmarshal(raw, &rows); err != nil {
		return nil, err
	}
	return rows, nil
}

func runPaced(client *http.Client, rows []row, opt opts) error {
	file, writer, err := openCSV(opt.out)
	if err != nil {
		return err
	}
	defer file.Close()
	defer writer.Flush()
	jobs := make(chan job, opt.concurrency)
	var wg sync.WaitGroup
	var mu sync.Mutex
	workers := opt.concurrency
	if workers < 1 {
		workers = 1
	}
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for item := range jobs {
				record := doOne(client, rows[item.idx%len(rows)], item.idx, item.scheduled, opt)
				if item.idx < opt.warmup {
					continue
				}
				mu.Lock()
				_ = writer.Write(record)
				mu.Unlock()
			}
		}()
	}
	batch := perTick(opt.rps, 20)
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	sent := 0
	total := opt.n + opt.warmup
	for sent < total {
		<-ticker.C
		for i := 0; i < batch && sent < total; i++ {
			jobs <- job{idx: sent, scheduled: time.Now()}
			sent++
		}
	}
	close(jobs)
	wg.Wait()
	return writer.Error()
}

type job struct {
	idx       int
	scheduled time.Time
}

func runOverhead(client *http.Client, rows []row, gatewayURL, directURL, scenario, apiKey, model string, n, warmup int, outPath string) error {
	if directURL == "" {
		return fmt.Errorf("overhead requires -direct")
	}
	file, writer, err := openCSV(outPath)
	if err != nil {
		return err
	}
	defer file.Close()
	defer writer.Flush()
	total := warmup + n
	for i := 0; i < total; i++ {
		item := rows[i%len(rows)]
		directRec := doOne(client, item, i, time.Now(), opts{
			url: directURL, scenario: scenario, mode: "direct", target: "model", model: model,
		})
		gwRec := doOne(client, item, i, time.Now(), opts{
			url: gatewayURL, scenario: scenario, mode: "gateway", target: "gateway", apiKey: apiKey, model: model,
		})
		if i < warmup {
			continue
		}
		if err := writer.Write(gwRec); err != nil {
			return err
		}
		if err := writer.Write(directRec); err != nil {
			return err
		}
	}
	return writer.Error()
}

func doOne(client *http.Client, item row, requestID int, scheduled time.Time, opt opts) []string {
	body, endpoint := payload(item, requestID, opt)
	req, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(body))
	status := 0
	backend := ""
	attempts := 1
	start := time.Now()
	if err == nil {
		req.Header.Set("Content-Type", "application/json")
		if opt.apiKey != "" && opt.target == "gateway" {
			req.Header.Set("X-API-Key", opt.apiKey)
		}
		// Latency is the HTTP round trip. Queue time before a worker starts is not included.
		start = time.Now()
		resp, doErr := client.Do(req)
		if doErr != nil {
			err = doErr
		} else {
			status = resp.StatusCode
			raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
			resp.Body.Close()
			var parsed struct {
				Backend  string `json:"backend"`
				Attempts int    `json:"attempts"`
			}
			if json.Unmarshal(raw, &parsed) == nil {
				backend = parsed.Backend
				if parsed.Attempts > 0 {
					attempts = parsed.Attempts
				}
			}
		}
	}
	_ = scheduled
	latency := time.Since(start).Seconds() * 1000
	return []string{
		fmt.Sprintf("%d", requestID),
		opt.scenario,
		opt.mode,
		backend,
		fmt.Sprintf("%d", attempts),
		fmt.Sprintf("%d", status),
		fmt.Sprintf("%.4f", latency),
		fmt.Sprintf("%.3f", float64(time.Now().UnixMilli())/1000.0),
		fmt.Sprintf("%d", item.RowID),
	}
}

func payload(item row, requestID int, opt opts) ([]byte, string) {
	switch opt.target {
	case "features":
		body, _ := json.Marshal(map[string]int{"user_id": item.UserID, "item_id": item.ItemID})
		return body, opt.url + "/v1/vector"
	case "model":
		body, _ := json.Marshal(map[string]any{"features": item.Features})
		return body, opt.url + "/predict"
	default:
		body, _ := json.Marshal(map[string]any{
			"model":      opt.model,
			"user_id":    item.UserID,
			"item_id":    item.ItemID,
			"row_id":     item.RowID,
			"request_id": requestID,
			"features":   item.Features,
			"text":       item.Text,
		})
		return body, opt.url + "/v1/predict"
	}
}

func openCSV(path string) (*os.File, *csv.Writer, error) {
	file, err := os.Create(path)
	if err != nil {
		return nil, nil, err
	}
	writer := csv.NewWriter(file)
	if err := writer.Write([]string{"request_id", "scenario", "mode", "backend_final", "attempts", "status", "latency_ms", "ts", "row_id"}); err != nil {
		file.Close()
		return nil, nil, err
	}
	return file, writer, nil
}
