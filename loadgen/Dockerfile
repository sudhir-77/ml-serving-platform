FROM golang:1.27-bookworm AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -o /out/loadgen .
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends tar ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/loadgen /usr/local/bin/loadgen
ENTRYPOINT ["loadgen"]
