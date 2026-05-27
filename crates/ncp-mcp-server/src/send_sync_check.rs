//! Compile-time assertions that the NCP runtime types we depend on satisfy
//! the bounds required by `tokio::task::spawn_blocking` (see
//! docs/MCP_ADAPTER.md §11).
//!
//! The async-to-sync bridge in PR C will use:
//!
//! ```text
//! tokio::task::spawn_blocking(move || -> anyhow::Result<ExecutionReport> {
//!     ctx.execute(...)  // ctx: Arc<RuntimeContext>
//! })
//! ```
//!
//! `spawn_blocking<F, R>(f: F) -> JoinHandle<R>` requires:
//!
//! - `F: FnOnce() -> R + Send + 'static`: every captured value must be Send. For `Arc<T>` this requires `T: Send + Sync`.
//! - `R: Send + 'static`: `anyhow::Result<ExecutionReport>` must be Send.
//!
//! If either assertion below fails to compile, PR C cannot use the
//! `spawn_blocking` pattern. The architecture must pivot to the
//! worker-thread fallback (single owner, channel-fed jobs) per §11.
//!
//! Catching this at PR B compile time avoids discovering it inside PR C's
//! protocol code, where the architecture change would be much more
//! disruptive.

fn assert_send_sync<T: Send + Sync>() {}
fn assert_send<T: Send>() {}

#[allow(dead_code)]
fn _runtime_context_must_be_send_sync() {
    // Required because the closure captures `Arc<RuntimeContext>`; Arc is Send
    // iff its inner type is Send + Sync.
    assert_send_sync::<ncp_runtime::RuntimeContext>();
}

#[allow(dead_code)]
fn _execute_result_must_be_send() {
    // Required because the closure returns `anyhow::Result<ExecutionReport>`
    // and spawn_blocking's JoinHandle<R> bound requires R: Send.
    // `Result<T, E>: Send` iff `T: Send` AND `E: Send`; this exercises both.
    assert_send::<anyhow::Result<ncp_runtime::ExecutionReport>>();
}
