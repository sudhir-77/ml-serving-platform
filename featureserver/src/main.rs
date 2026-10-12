use std::net::SocketAddr;
use std::sync::Arc;

use featureserver::{router, Store};

fn main() {
    let workers: usize = std::env::var("TOKIO_WORKER_THREADS")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(4);
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(workers.max(1))
        .enable_all()
        .build()
        .expect("runtime");
    runtime.block_on(async {
        let host = std::env::var("HOST").unwrap_or_else(|_| "127.0.0.1".into());
        let port = std::env::var("PORT").unwrap_or_else(|_| "18200".into());
        let addr: SocketAddr = format!("{host}:{port}").parse().expect("listen address");
        let store = Arc::new(Store::new(16));
        let app = router(store);
        let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
        println!("featureserver listening on {addr}");
        axum::serve(listener, app).await.expect("serve");
    });
}
