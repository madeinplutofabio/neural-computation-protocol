#![deny(clippy::print_stdout)]

//! # ncp-mcp-server
//!
//! A stdio [Model Context Protocol] adapter for [NCP] — exposes auditable
//! WASM Brick graphs as MCP tools that any MCP-compatible host can call.
//!
//! One graph = one MCP tool. Configure your MCP-compatible host to launch
//! `ncp-mcp-server --graph graph.yaml`, and that graph becomes a callable
//! tool with no glue code.
//!
//! ## Install
//!
//! ```text
//! cargo install ncp-mcp-server --locked
//! ```
//!
//! Then point your MCP-compatible host's tool config at the binary. See the
//! [adapter README] for source-install and crates.io-install instructions,
//! and the [design doc] for binding architectural decisions.
//!
//! ## Stability
//!
//! `v0.1.0` targets MCP spec 2025-11-25 over stdio transport only.
//! Streamable HTTP transport is deferred to a future release.
//!
//! The CLI is the stable interface. The Rust module API exposed by this crate
//! is treated as reference-implementation internals until adopter feedback
//! shapes a stable public surface. Treat module re-exports as private to the
//! binary.
//!
//! ## Links
//!
//! - [Design doc](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/MCP_ADAPTER.md)
//! - [Project README](https://github.com/madeinplutofabio/neural-computation-protocol)
//! - [Install guide](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/INSTALL.md)
//! - [Adoption guide](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/ADOPTION_GUIDE.md)
//! - [NCP protocol spec](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/spec/ncp-v0.2.3.md)
//! - [MCP spec](https://modelcontextprotocol.io/specification/2025-11-25)
//!
//! [Model Context Protocol]: https://modelcontextprotocol.io
//! [NCP]: https://github.com/madeinplutofabio/neural-computation-protocol
//! [adapter README]: https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/crates/ncp-mcp-server/README.md
//! [design doc]: https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/MCP_ADAPTER.md

#[doc(hidden)]
pub mod cli;

#[doc(hidden)]
pub mod naming;

#[doc(hidden)]
pub mod server;

mod send_sync_check;
