package main

import "testing"

func TestPerTick(t *testing.T) {
	if perTick(2000, 20) != 40 {
		t.Fatal(perTick(2000, 20))
	}
	if perTick(10, 20) != 1 {
		t.Fatal(perTick(10, 20))
	}
	if perTick(0, 20) != 1 {
		t.Fatal("zero rps still emits one per tick when the caller asks")
	}
}
