#![allow(dead_code)]
// NOTE: This module mirrors the NCP spec schema. Many fields are intentionally
// not read yet in Phase 2, but must deserialize for forward compatibility.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::Deserialize;

// ── Brick Manifest ──────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct BrickManifest {
    pub brick_id: String,
    pub version: String,
    pub ncp_protocol_range: String,
    #[serde(default)]
    pub required_runtime_features: Vec<String>,
    pub status: String,
    #[serde(default)]
    pub successor: Option<String>,
    #[serde(default)]
    pub confidence_threshold: Option<f64>,
    pub artifact: Artifact,
    pub schemas: Schemas,
    pub limits: Limits,
    #[serde(default)]
    pub capabilities: Vec<String>,
    pub determinism: Determinism,
    pub time_model: String,
    pub carry_state_class: String,
    pub carry_state_transport: String,
    pub carry_state_max_bytes: u64,
    #[serde(default)]
    pub carry_state_side_effects_schema: Option<String>,
    #[serde(default)]
    pub graph_ref_slots: Vec<GraphRefSlot>,
}

#[derive(Debug, Deserialize)]
pub struct Artifact {
    pub format: String,
    pub entrypoint: String,
    pub digest: String,
    pub size_bytes: u64,
    #[serde(default)]
    pub path: Option<PathBuf>,
}

#[derive(Debug, Deserialize)]
pub struct Schemas {
    pub input: String,
    pub output: String,
    pub carry_state: Option<String>,
    #[serde(default)]
    pub metadata: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct Limits {
    pub max_ms: u64,
    pub max_mem_mb: u64,
    pub max_output_bytes: u64,
    #[serde(default)]
    pub max_input_bytes: Option<u64>,
    #[serde(default)]
    pub estimated_cost_per_invoke_usd: Option<f64>,
}

#[derive(Debug, Deserialize)]
pub struct Determinism {
    pub mode: String,
    #[serde(default)]
    pub epsilon: Option<f64>,
    #[serde(default)]
    pub seed_source: Option<String>,
    #[serde(default)]
    pub model_pin: Option<String>,
    #[serde(default)]
    pub reproducibility_level: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct GraphRefSlot {
    pub slot: String,
    #[serde(rename = "type")]
    pub slot_type: String,
    pub access: String,
    pub ref_consistency: String,
}

// ── Graph Manifest ──────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct GraphManifest {
    pub graph_id: String,
    pub graph_version: String,
    pub synaptic_state_version: String,
    pub nodes: Vec<Node>,
    pub edges: Vec<Edge>,
}

#[derive(Debug, Deserialize)]
pub struct Node {
    pub node_id: String,
    pub brick: NodeBrick,
    #[serde(default)]
    pub activation: Option<Activation>,
    #[serde(default)]
    pub bindings: Bindings,
    #[serde(default)]
    pub carry_state_lifecycle: Option<CarryStateLifecycle>,
}

#[derive(Debug, Deserialize)]
pub struct NodeBrick {
    pub brick_id: String,
    pub version_or_range: String,
}

#[derive(Debug, Deserialize)]
pub struct Activation {
    pub mode: String,
    #[serde(default)]
    pub timeout_ms: Option<u64>,
    #[serde(default)]
    pub allow_partial: Option<bool>,
    #[serde(default)]
    pub required_fields: Option<Vec<String>>,
    #[serde(default)]
    pub defaults: Option<HashMap<String, serde_yaml::Value>>,
    #[serde(default)]
    pub conflict_resolution: Option<String>,
    #[serde(default)]
    pub quorum_n: Option<u64>,
    #[serde(default)]
    pub quorum_m: Option<u64>,
}

#[derive(Debug, Default, Deserialize)]
pub struct Bindings {
    #[serde(default)]
    pub graph_ref_slot_values: HashMap<String, serde_yaml::Value>,
}

#[derive(Debug, Deserialize)]
pub struct CarryStateLifecycle {
    #[serde(default)]
    pub init: Option<String>,
    #[serde(default)]
    pub ttl_seconds: Option<u64>,
    #[serde(default)]
    pub on_graph_version_change: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct Edge {
    pub edge_id: String,
    pub source_node: String,
    pub target_node: String,
    #[serde(default)]
    pub mapping: Vec<FieldMapping>,
    #[serde(default)]
    pub on_success: Option<OnSuccess>,
    #[serde(default)]
    pub on_error: Option<HashMap<String, Vec<String>>>,
    #[serde(default)]
    pub priority: Option<i64>,
}

#[derive(Debug, Deserialize)]
pub struct FieldMapping {
    pub from: String,
    pub to: String,
}

#[derive(Debug, Deserialize)]
pub struct OnSuccess {
    #[serde(default)]
    pub weight: Option<f64>,
    #[serde(default)]
    pub threshold: Option<f64>,
}

// ── Loading ─────────────────────────────────────────────────────────

pub fn load_graph(path: &Path) -> Result<GraphManifest> {
    let contents = std::fs::read_to_string(path)
        .with_context(|| format!("reading graph manifest '{}'", path.display()))?;
    let graph: GraphManifest = serde_yaml::from_str(&contents)
        .with_context(|| format!("parsing graph manifest '{}'", path.display()))?;
    Ok(graph)
}

pub fn load_brick(path: &Path) -> Result<BrickManifest> {
    let contents = std::fs::read_to_string(path)
        .with_context(|| format!("reading brick manifest '{}'", path.display()))?;
    let brick: BrickManifest = serde_yaml::from_str(&contents)
        .with_context(|| format!("parsing brick manifest '{}'", path.display()))?;
    Ok(brick)
}
