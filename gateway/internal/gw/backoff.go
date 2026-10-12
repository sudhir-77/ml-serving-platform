package gw

import "time"

// Jitter is full jitter over an exponential backoff. unit is in [0, 1].
// attempt <= 0 returns zero so the first try is immediate.
func Jitter(attempt int, base, max time.Duration, unit float64) time.Duration {
	if attempt <= 0 || base <= 0 {
		return 0
	}
	if unit < 0 {
		unit = 0
	}
	if unit > 1 {
		unit = 1
	}
	shift := attempt - 1
	if shift > 6 {
		shift = 6
	}
	d := base * time.Duration(1<<shift)
	if max > 0 && d > max {
		d = max
	}
	if d <= 0 {
		return 0
	}
	return time.Duration(float64(d) * unit)
}
