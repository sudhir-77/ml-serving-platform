package main

import (
	"log"
	"os"

	"mlserving/gateway/internal/gw"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		log.Fatal(err)
	}
}

func run(args []string) error {
	path := "gateway.json"
	if len(args) > 0 {
		path = args[0]
	}
	cfg, err := gw.Load(path)
	if err != nil {
		return err
	}
	return gw.New(cfg).ListenAndServe()
}
