//! Sharded in-memory feature store with TTL. HTTP is a thin wrapper.

use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::State;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug)]
struct Entry {
    values: Vec<f64>,
    expires_at_ms: u64,
}

/// FNV-1a shard index. The same key always lands on the same shard.
pub fn shard_for(key: &str, shards: usize) -> usize {
    let n = shards.max(1);
    let mut hash: u64 = 0xcbf29ce484222325;
    for byte in key.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    (hash as usize) % n
}

/// TTL of zero means the entry does not expire.
pub fn expiry(now_ms: u64, ttl_ms: u64) -> u64 {
    if ttl_ms == 0 {
        u64::MAX
    } else {
        now_ms.saturating_add(ttl_ms)
    }
}

pub fn compose_vector(user: &[f64], item: &[f64]) -> Vec<f64> {
    let mut out = Vec::with_capacity(user.len() + item.len());
    out.extend_from_slice(user);
    out.extend_from_slice(item);
    out
}

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[derive(Debug, Clone, Deserialize)]
pub struct FeatureEvent {
    pub key: String,
    pub values: Vec<f64>,
    pub ttl_ms: u64,
}

/// Process-local online feature store. Shards keep lookup latency low under concurrent load.
pub struct Store {
    shards: Vec<RwLock<HashMap<String, Entry>>>,
}

impl Store {
    pub fn new(shards: usize) -> Self {
        let n = shards.max(1);
        let mut shards = Vec::with_capacity(n);
        for _ in 0..n {
            shards.push(RwLock::new(HashMap::new()));
        }
        Self { shards }
    }

    fn shard(&self, key: &str) -> &RwLock<HashMap<String, Entry>> {
        &self.shards[shard_for(key, self.shards.len())]
    }

    pub fn upsert(&self, key: &str, values: Vec<f64>, expires_at_ms: u64) {
        let mut guard = self.shard(key).write().expect("shard lock");
        guard.insert(
            key.to_string(),
            Entry {
                values,
                expires_at_ms,
            },
        );
    }

    pub fn apply_event(&self, event: &FeatureEvent, now_ms: u64) {
        self.upsert(&event.key, event.values.clone(), expiry(now_ms, event.ttl_ms));
    }

    pub fn get(&self, key: &str, now_ms: u64) -> Option<Vec<f64>> {
        let mut guard = self.shard(key).write().expect("shard lock");
        match guard.get(key) {
            Some(entry) if now_ms < entry.expires_at_ms => Some(entry.values.clone()),
            Some(_) => {
                guard.remove(key);
                None
            }
            None => None,
        }
    }

    pub fn len(&self) -> usize {
        self.shards
            .iter()
            .map(|shard| shard.read().expect("shard lock").len())
            .sum()
    }

    pub fn vector_for(&self, user_id: i64, item_id: i64, now_ms: u64) -> Vec<f64> {
        let user = self
            .get(&format!("u:{user_id}"), now_ms)
            .or_else(|| self.get("u:default", now_ms))
            .unwrap_or_else(|| vec![0.0, 0.0, 0.0]);
        let item = self
            .get(&format!("i:{item_id}"), now_ms)
            .or_else(|| self.get("i:default", now_ms))
            .unwrap_or_else(|| vec![0.0, 0.0, 0.0]);
        compose_vector(&user, &item)
    }
}

#[derive(Deserialize)]
struct EventBatch {
    events: Vec<FeatureEvent>,
}

#[derive(Deserialize)]
struct VectorQuery {
    user_id: i64,
    item_id: i64,
}

#[derive(Serialize)]
struct VectorBody {
    features: Vec<f64>,
    found: bool,
}

pub fn router(store: Arc<Store>) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/v1/events", post(ingest))
        .route("/v1/vector", post(vector))
        .with_state(store)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

async fn ingest(State(store): State<Arc<Store>>, Json(batch): Json<EventBatch>) -> Json<serde_json::Value> {
    let now = now_ms();
    for event in &batch.events {
        store.apply_event(event, now);
    }
    Json(serde_json::json!({"stored": batch.events.len()}))
}

async fn vector(State(store): State<Arc<Store>>, Json(query): Json<VectorQuery>) -> Json<VectorBody> {
    let features = store.vector_for(query.user_id, query.item_id, now_ms());
    Json(VectorBody {
        features,
        found: true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::thread;

    #[test]
    fn shard_is_stable_and_in_range() {
        assert_eq!(shard_for("u:1", 8), shard_for("u:1", 8));
        assert!(shard_for("i:22", 8) < 8);
        assert_eq!(shard_for("k", 0), 0);
    }

    #[test]
    fn expiry_zero_does_not_expire() {
        assert_eq!(expiry(10, 0), u64::MAX);
        assert_eq!(expiry(u64::MAX - 5, 10), u64::MAX);
        assert_eq!(expiry(10, 5), 15);
    }

    #[test]
    fn compose_concatenates() {
        assert_eq!(compose_vector(&[1.0, 2.0], &[3.0]), vec![1.0, 2.0, 3.0]);
    }

    #[test]
    fn ttl_hides_expired_keys_and_defaults_fill_gaps() {
        let store = Store::new(4);
        store.apply_event(
            &FeatureEvent {
                key: "u:default".into(),
                values: vec![0.0, 0.0, 1.0],
                ttl_ms: 0,
            },
            1_000,
        );
        store.apply_event(
            &FeatureEvent {
                key: "i:default".into(),
                values: vec![0.0, 0.0, 2.0],
                ttl_ms: 0,
            },
            1_000,
        );
        store.apply_event(
            &FeatureEvent {
                key: "u:7".into(),
                values: vec![9.0, 8.0, 7.0],
                ttl_ms: 10,
            },
            1_000,
        );
        assert_eq!(store.get("u:7", 1_009), Some(vec![9.0, 8.0, 7.0]));
        assert_eq!(store.vector_for(7, 3, 1_009)[..3], [9.0, 8.0, 7.0]);
        assert_eq!(store.get("u:7", 1_010), None);
        let fallback = store.vector_for(7, 3, 1_010);
        assert_eq!(fallback, vec![0.0, 0.0, 1.0, 0.0, 0.0, 2.0]);
        assert!(store.len() >= 2);
    }

    #[test]
    fn upsert_and_len_are_public() {
        let store = Store::new(2);
        store.upsert("k", vec![1.0], 50);
        assert_eq!(store.len(), 1);
        assert_eq!(store.get("k", 49), Some(vec![1.0]));
    }

    #[test]
    fn concurrent_reads_and_writes() {
        let store = Arc::new(Store::new(8));
        let mut handles = Vec::new();
        for worker in 0..8 {
            let shared = Arc::clone(&store);
            handles.push(thread::spawn(move || {
                for i in 0..100 {
                    let key = format!("k:{worker}:{i}");
                    shared.upsert(&key, vec![i as f64], u64::MAX);
                    assert!(shared.get(&key, 0).is_some());
                }
            }));
        }
        for handle in handles {
            handle.join().expect("worker");
        }
        assert_eq!(store.len(), 800);
    }

    #[tokio::test]
    async fn http_roundtrip_reads_an_ingested_vector() {
        let store = Arc::new(Store::new(4));
        let app = router(store);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        let events = r#"{"events":[{"key":"u:1","values":[1.5,2.5,3.5],"ttl_ms":0},{"key":"i:4","values":[4.5,5.5,6.5],"ttl_ms":0}]}"#;
        let events_resp = raw_http(addr, "POST", "/v1/events", events).await;
        assert!(events_resp.contains("200"), "{events_resp}");
        let vector_resp = raw_http(addr, "POST", "/v1/vector", r#"{"user_id":1,"item_id":4}"#).await;
        assert!(vector_resp.contains("200"), "{vector_resp}");
        assert!(vector_resp.contains("1.5"), "{vector_resp}");
        assert!(vector_resp.contains("6.5"), "{vector_resp}");
        let health = raw_http(addr, "GET", "/health", "").await;
        assert!(health.contains("ok"), "{health}");
    }

    async fn raw_http(addr: std::net::SocketAddr, method: &str, path: &str, body: &str) -> String {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let mut stream = tokio::net::TcpStream::connect(addr).await.unwrap();
        let req = if body.is_empty() {
            format!("{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        } else {
            format!(
                "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
        };
        stream.write_all(req.as_bytes()).await.unwrap();
        let mut buf = Vec::new();
        stream.read_to_end(&mut buf).await.unwrap();
        String::from_utf8(buf).unwrap()
    }
}
