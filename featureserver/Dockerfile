FROM rust:1.99-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release
FROM debian:bookworm-slim
COPY --from=build /src/target/release/featureserver /usr/local/bin/featureserver
ENV HOST=0.0.0.0
ENV PORT=8080
EXPOSE 8080
CMD ["featureserver"]
