pub mod engine;
pub mod envelope;
pub mod manifest;
pub mod mapping;
pub mod resolver;
pub mod result;
pub mod router;
pub mod trace;

use std::collections::HashMap;
use std::path::Path;

use anyhow::{bail, Context, Result};

use result::CborValue;

pub const RUNTIME_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const WASMTIME_MAJOR: &str = "43";

pub fn now_rfc3339() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

// ── Execute types ──────────────────────────────────────────────────

/// Per-invoke metric emitted to hooks.
pub struct InvokeMetric {
    pub step: u64,
    pub node_id: String,
    pub brick_id: String,
    pub result_type: String,
    pub latency_ms: f64,
    pub envelope_bytes: usize,
    pub result_bytes: Option<usize>,
}

/// Hooks for streaming execution events.
#[derive(Default)]
pub struct ExecuteHooks<'a> {
    pub on_invoke: Option<&'a mut dyn FnMut(InvokeMetric)>,
}

/// Options for a single execute() call.
pub struct ExecuteOptions {
    pub trace_id: Option<String>,
    pub session_id: Option<String>,
    pub max_steps: Option<u64>,
    pub max_queued: u64,
    pub all_terminals: bool,
    pub verbose: bool,
}

impl Default for ExecuteOptions {
    fn default() -> Self {
        Self {
            trace_id: None,
            session_id: None,
            max_steps: None,
            max_queued: 10_000,
            all_terminals: false,
            verbose: false,
        }
    }
}

/// A terminal result from graph execution.
pub struct TerminalResult {
    pub node_id: String,
    pub brick_id: String,
    pub step: u64,
    pub result: result::BrickResult,
}

/// Execution result counts.
pub struct ResultCounts {
    pub success: u64,
    pub low_confidence: u64,
    pub failure: u64,
}

/// Report from a single graph execution.
pub struct ExecutionReport {
    pub terminals: Vec<TerminalResult>,
    pub total_steps: u64,
    pub counts: ResultCounts,
}

/// Info about a resolved brick, for caller-side logging.
pub struct ResolvedBrickInfo {
    pub brick_id: String,
    pub version: String,
    pub wasm_bytes: usize,
    pub digest: String,
}

// ── RuntimeContext ──────────────────────────────────────────────────

/// Loaded + compiled graph context. Reusable across multiple execute() calls.
pub struct RuntimeContext {
    graph: manifest::GraphManifest,
    compiled_bricks: HashMap<(String, String), engine::CompiledBrick>,
    brick_manifests: HashMap<(String, String), manifest::BrickManifest>,
    node_brick_key: HashMap<String, (String, String)>,
    edges_by_source: HashMap<String, Vec<usize>>,
    edge_by_id: HashMap<String, usize>,
    entry_node_id: String,
    resolved_info: Vec<ResolvedBrickInfo>,
}

/// A task in the FIFO queue.
struct Task {
    node_id: String,
    input_json: serde_json::Value,
    trigger_source_node_id: String,
    trigger_source_step: u64,
    trigger_edge_id: String,
    trigger_routing_reason: String,
}

impl RuntimeContext {
    /// Load from file paths (CLI convenience).
    pub fn load(graph_path: &Path, brick_dir: &Path, brick_map: Option<&Path>) -> Result<Self> {
        let graph = manifest::load_graph(graph_path)?;
        let brick_map = brick_map.map(resolver::load_brick_map).transpose()?;
        Self::from_graph(graph, brick_dir, &brick_map)
    }

    /// Build from a pre-parsed GraphManifest (for embedding / Phase 3A).
    pub fn from_graph(
        graph: manifest::GraphManifest,
        brick_dir: &Path,
        brick_map: &Option<resolver::BrickMap>,
    ) -> Result<Self> {
        if graph.nodes.is_empty() {
            bail!("graph validation failed: graph has no nodes");
        }

        let mut compiled_bricks: HashMap<(String, String), engine::CompiledBrick> = HashMap::new();
        let mut brick_manifests: HashMap<(String, String), manifest::BrickManifest> =
            HashMap::new();
        let mut node_brick_key: HashMap<String, (String, String)> = HashMap::new();
        let mut resolved_info: Vec<ResolvedBrickInfo> = Vec::new();

        for node in &graph.nodes {
            let resolved = resolver::resolve_brick(
                &node.brick.brick_id,
                &node.brick.version_or_range,
                brick_dir,
                brick_map,
            )?;
            let key = (
                node.brick.brick_id.clone(),
                resolved.manifest.version.clone(),
            );

            if node_brick_key
                .insert(node.node_id.clone(), key.clone())
                .is_some()
            {
                bail!(
                    "graph validation failed: duplicate node_id '{}'",
                    node.node_id
                );
            }

            if let std::collections::hash_map::Entry::Vacant(entry) =
                compiled_bricks.entry(key.clone())
            {
                resolved_info.push(ResolvedBrickInfo {
                    brick_id: key.0.clone(),
                    version: key.1.clone(),
                    wasm_bytes: resolved.wasm_bytes.len(),
                    digest: resolved.manifest.artifact.digest.clone(),
                });
                let compiled = engine::CompiledBrick::new(&resolved.wasm_bytes)?;
                entry.insert(compiled);
                brick_manifests.insert(key, resolved.manifest);
            }
        }

        // Detect entry node: not the target of any edge
        let target_nodes: std::collections::HashSet<&str> =
            graph.edges.iter().map(|e| e.target_node.as_str()).collect();

        let entry_nodes: Vec<&str> = graph
            .nodes
            .iter()
            .map(|n| n.node_id.as_str())
            .filter(|id| !target_nodes.contains(id))
            .collect();

        match entry_nodes.len() {
            0 => bail!("graph validation failed: no entry node found (all nodes are edge targets — cycle-only graph)"),
            1 => {}
            _ => bail!("graph validation failed: multiple entry nodes found: {:?}", entry_nodes),
        }
        let entry_node_id = entry_nodes[0].to_string();

        // Pre-index edges by source node and by edge ID
        let mut edges_by_source: HashMap<String, Vec<usize>> = HashMap::new();
        let mut edge_by_id: HashMap<String, usize> = HashMap::new();
        for (i, edge) in graph.edges.iter().enumerate() {
            edges_by_source
                .entry(edge.source_node.clone())
                .or_default()
                .push(i);
            edge_by_id.insert(edge.edge_id.clone(), i);
        }

        Ok(Self {
            graph,
            compiled_bricks,
            brick_manifests,
            node_brick_key,
            edges_by_source,
            edge_by_id,
            entry_node_id,
            resolved_info,
        })
    }

    /// Emit the runtime_info trace record.
    pub fn emit_runtime_info(&self, tracer: &mut dyn trace::TraceSink) {
        if tracer.enabled() {
            tracer.emit_runtime_info(RUNTIME_VERSION, WASMTIME_MAJOR, &now_rfc3339());
        }
    }

    pub fn graph_id(&self) -> &str {
        &self.graph.graph_id
    }
    pub fn graph_version(&self) -> &str {
        &self.graph.graph_version
    }
    pub fn node_count(&self) -> usize {
        self.graph.nodes.len()
    }
    pub fn edge_count(&self) -> usize {
        self.graph.edges.len()
    }
    pub fn entry_node_id(&self) -> &str {
        &self.entry_node_id
    }
    pub fn resolved_bricks(&self) -> &[ResolvedBrickInfo] {
        &self.resolved_info
    }

    /// Execute the graph with the given input.
    pub fn execute(
        &self,
        json_input: &serde_json::Value,
        tracer: &mut dyn trace::TraceSink,
        hooks: &mut ExecuteHooks<'_>,
        opts: &ExecuteOptions,
    ) -> Result<ExecutionReport> {
        let trace_id = opts
            .trace_id
            .as_deref()
            .map(str::to_owned)
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        let session_id = opts
            .session_id
            .as_deref()
            .map(str::to_owned)
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

        let mut queue: std::collections::VecDeque<Task> = std::collections::VecDeque::new();
        let mut step: u64 = 0;
        let mut terminals: Vec<TerminalResult> = Vec::new();
        let mut counts = ResultCounts {
            success: 0,
            low_confidence: 0,
            failure: 0,
        };
        let verbose = opts.verbose;
        let tracing = tracer.enabled();

        // Push entry task
        queue.push_back(Task {
            node_id: self.entry_node_id.clone(),
            input_json: json_input.clone(),
            trigger_source_node_id: envelope::ROOT_TRIGGER.source_node_id.to_string(),
            trigger_source_step: envelope::ROOT_TRIGGER.source_step,
            trigger_edge_id: envelope::ROOT_TRIGGER.edge_id.to_string(),
            trigger_routing_reason: "entry".to_string(),
        });

        while let Some(task) = queue.pop_front() {
            // Safety budget: max_steps
            if let Some(max) = opts.max_steps {
                if step >= max {
                    let msg = format!("max_steps budget ({max}) exhausted");
                    if verbose {
                        eprintln!("Safety budget: {msg}");
                    }
                    counts.failure += 1;
                    terminals.push(TerminalResult {
                        node_id: task.node_id.clone(),
                        brick_id: self
                            .node_brick_key
                            .get(&task.node_id)
                            .map(|k| k.0.clone())
                            .unwrap_or_else(|| "__runtime__".to_string()),
                        step,
                        result: result::trap_failure("RESOURCE_EXCEEDED", msg),
                    });
                    break;
                }
            }

            let brick_key = self
                .node_brick_key
                .get(&task.node_id)
                .with_context(|| format!("no brick key recorded for node '{}'", task.node_id))?;
            let compiled = self
                .compiled_bricks
                .get(brick_key)
                .with_context(|| format!("no compiled brick for key {:?}", brick_key))?;
            let brick_manifest = self
                .brick_manifests
                .get(brick_key)
                .with_context(|| format!("no manifest for key {:?}", brick_key))?;

            let trigger = envelope::Trigger {
                source_node_id: &task.trigger_source_node_id,
                source_step: task.trigger_source_step,
                edge_id: &task.trigger_edge_id,
            };

            let env = envelope::build_envelope(
                &task.input_json,
                &self.graph.graph_id,
                &self.graph.graph_version,
                &task.node_id,
                &trace_id,
                &session_id,
                step,
                &trigger,
            )?;

            // Check max_input_bytes — soft failure (routes via on_error, doesn't abort run)
            let mut pre_invoke_failure: Option<result::BrickResult> = None;
            if let Some(max_input) = brick_manifest.limits.max_input_bytes {
                if env.len() as u64 > max_input {
                    let msg =
                        format!(
                        "envelope too large for brick '{}': {} bytes > limits.max_input_bytes {}",
                        brick_key.0, env.len(), max_input,
                    );
                    if verbose {
                        eprintln!("  {msg}");
                    }
                    pre_invoke_failure = Some(result::trap_failure("INVALID_INPUT", msg));
                }
            }

            if verbose {
                if pre_invoke_failure.is_some() {
                    eprintln!(
                        "[step {}] Skipping invoke for brick '{}' node '{}' (pre-invoke failure)",
                        step, brick_key.0, task.node_id,
                    );
                } else {
                    eprintln!(
                        "[step {}] Invoking brick '{}' node '{}' ({} byte envelope)",
                        step,
                        brick_key.0,
                        task.node_id,
                        env.len(),
                    );
                }
            }

            // Invoke brick (or use pre-invoke failure)
            let (brick_result, raw_result_bytes, latency_ms) = if let Some(br) = pre_invoke_failure
            {
                (br, None, 0.0)
            } else {
                let start = std::time::Instant::now();
                let invoke_result = compiled.invoke(
                    &env,
                    brick_manifest.limits.max_mem_mb,
                    brick_manifest.limits.max_output_bytes,
                );
                let latency = start.elapsed().as_secs_f64() * 1000.0;

                match invoke_result {
                    Ok(result_bytes) => {
                        if verbose {
                            eprintln!("  Got {} byte result", result_bytes.len());
                        }
                        let decoded = match result::decode_result(&result_bytes) {
                            Ok(r) => r,
                            Err(e) => {
                                let msg = format!("{e:#}");
                                if verbose {
                                    eprintln!("  Result rejected: {msg}");
                                }
                                result::trap_failure("RUNTIME_REJECTED", msg)
                            }
                        };
                        (decoded, Some(result_bytes), latency)
                    }
                    Err(e) => {
                        let msg = format!("{e:#}");
                        let m = msg.to_lowercase();
                        let error_class = if m.contains("alloc returned 0")
                            || m.contains("oom")
                            || m.contains("result too large")
                            || m.contains("memory limit")
                            || m.contains("fuel")
                            || m.contains("resource_exceeded")
                        {
                            "RESOURCE_EXCEEDED"
                        } else {
                            "COMPUTATION_ERROR"
                        };
                        if verbose {
                            eprintln!("  Brick trap: {msg}");
                        }
                        (result::trap_failure(error_class, msg), None, latency)
                    }
                }
            };

            // Emit invoke trace record (skip entirely when tracing disabled)
            if tracing {
                tracer.emit_invoke(
                    &trace_id,
                    &session_id,
                    step,
                    &self.graph.graph_id,
                    &self.graph.graph_version,
                    &brick_key.0,
                    &brick_key.1,
                    &brick_manifest.artifact.digest,
                    &task.node_id,
                    &env,
                    &task.trigger_source_node_id,
                    task.trigger_source_step,
                    &task.trigger_edge_id,
                    &task.trigger_routing_reason,
                    &brick_result,
                    raw_result_bytes.as_deref(),
                    latency_ms,
                    &now_rfc3339(),
                );
            }

            // Update counts
            match &brick_result {
                result::BrickResult::Success { .. } => counts.success += 1,
                result::BrickResult::LowConfidence { .. } => counts.low_confidence += 1,
                result::BrickResult::Failure { .. } => counts.failure += 1,
            }

            // Fire hook
            if let Some(ref mut on_invoke) = hooks.on_invoke {
                on_invoke(InvokeMetric {
                    step,
                    node_id: task.node_id.clone(),
                    brick_id: brick_key.0.clone(),
                    result_type: brick_result.result_type().to_string(),
                    latency_ms,
                    envelope_bytes: env.len(),
                    result_bytes: raw_result_bytes.as_ref().map(|b| b.len()),
                });
            }

            if verbose {
                eprintln!("  Result type: {}", brick_result.result_type());
            }

            // ── Routing ────────────────────────────────────────────
            let outbound_indices = self
                .edges_by_source
                .get(task.node_id.as_str())
                .cloned()
                .unwrap_or_default();
            let outbound: Vec<&manifest::Edge> = outbound_indices
                .iter()
                .map(|&i| &self.graph.edges[i])
                .collect();

            let output_confidence = brick_result.output().and_then(mapping::extract_confidence);

            let routed = router::route(&outbound, &brick_result, output_confidence);

            if routed.is_empty() {
                if verbose {
                    eprintln!("  Terminal node (no outbound edges dispatched)");
                }
                terminals.push(TerminalResult {
                    node_id: task.node_id.clone(),
                    brick_id: brick_key.0.clone(),
                    step,
                    result: brick_result,
                });
            } else {
                for routed_edge in &routed {
                    let edge_idx = self
                        .edge_by_id
                        .get(routed_edge.edge_id.as_str())
                        .with_context(|| {
                            format!("routed edge '{}' not found in graph", routed_edge.edge_id)
                        })?;
                    let edge_def = &self.graph.edges[*edge_idx];

                    let mapped_input = if edge_def.mapping.is_empty() {
                        match brick_result.output() {
                            Some(output) => {
                                serde_json::json!({ "input": mapping::cbor_to_json(output) })
                            }
                            None => serde_json::json!({ "input": null }),
                        }
                    } else {
                        let source_root = match brick_result.output() {
                            Some(output) => CborValue::Map(vec![(
                                CborValue::Text("output".to_string()),
                                output.clone(),
                            )]),
                            None => CborValue::Map(vec![]),
                        };

                        let mut target_cbor = CborValue::Map(vec![]);
                        for fm in &edge_def.mapping {
                            let resolved = mapping::resolve_path(&source_root, &fm.from)
                                .with_context(|| {
                                    format!(
                                        "mapping '{}' → '{}': source path '{}' not found in output",
                                        fm.from, fm.to, fm.from,
                                    )
                                })?;
                            let overlay = mapping::set_path(&fm.to, resolved);
                            target_cbor = mapping::merge_maps(target_cbor, overlay);
                        }

                        mapping::cbor_to_json(&target_cbor)
                    };

                    if verbose {
                        eprintln!(
                            "  Route: edge '{}' → node '{}'",
                            routed_edge.edge_id, routed_edge.target_node,
                        );
                    }

                    // Safety budget: max_queued
                    if queue.len() as u64 >= opts.max_queued {
                        bail!(
                            "safety budget exceeded: queue size {} >= max_queued {}",
                            queue.len(),
                            opts.max_queued,
                        );
                    }

                    queue.push_back(Task {
                        node_id: routed_edge.target_node.clone(),
                        input_json: mapped_input,
                        trigger_source_node_id: task.node_id.clone(),
                        trigger_source_step: step,
                        trigger_edge_id: routed_edge.edge_id.clone(),
                        trigger_routing_reason: "routed".to_string(),
                    });
                }
            }

            step += 1;
        }

        Ok(ExecutionReport {
            terminals,
            total_steps: step,
            counts,
        })
    }
}
