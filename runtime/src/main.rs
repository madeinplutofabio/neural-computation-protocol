mod cli;
mod engine;
mod envelope;
mod manifest;
mod mapping;
mod resolver;
mod result;
mod router;
mod trace;

use std::collections::HashMap;
use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use clap::Parser;

use cli::{Cli, Command};
use result::CborValue;

const RUNTIME_VERSION: &str = env!("CARGO_PKG_VERSION");
const WASMTIME_MAJOR: &str = "43";

fn now_rfc3339() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Run {
            graph,
            input,
            brick_dir,
            brick_map,
            trace,
            all_terminals,
            max_steps,
            max_queued,
        } => run_graph(graph, input, brick_dir, brick_map, trace, all_terminals, max_steps, max_queued),
    }
}

/// A task in the FIFO queue.
struct Task {
    node_id: String,
    input_json: serde_json::Value,
    /// Trigger provenance — owned strings so no lifetime issues.
    trigger_source_node_id: String,
    trigger_source_step: u64,
    trigger_edge_id: String,
    trigger_routing_reason: String,
}

/// Terminal result captured for final output.
struct TerminalResult {
    node_id: String,
    brick_id: String,
    step: u64,
    result: result::BrickResult,
}

fn run_graph(
    graph: PathBuf,
    input: PathBuf,
    brick_dir: PathBuf,
    brick_map: Option<PathBuf>,
    trace_file: Option<PathBuf>,
    all_terminals: bool,
    max_steps: Option<u64>,
    max_queued: u64,
) -> Result<()> {
    // Set up trace writer
    let mut tracer = match &trace_file {
        Some(path) => trace::TraceWriter::file(path)
            .with_context(|| format!("opening trace file '{}'", path.display()))?,
        None => trace::TraceWriter::stderr(),
    };
    tracer.emit_runtime_info(RUNTIME_VERSION, WASMTIME_MAJOR, &now_rfc3339());

    // Load graph
    let graph_manifest = manifest::load_graph(&graph)?;
    eprintln!(
        "Loaded graph '{}' with {} nodes, {} edges",
        graph_manifest.graph_id,
        graph_manifest.nodes.len(),
        graph_manifest.edges.len(),
    );

    if graph_manifest.nodes.is_empty() {
        bail!("graph validation failed: graph has no nodes");
    }

    // Load input JSON
    let input_str = std::fs::read_to_string(&input)
        .with_context(|| format!("reading input file '{}'", input.display()))?;
    let json_input: serde_json::Value = serde_json::from_str(&input_str)
        .with_context(|| format!("parsing input JSON '{}'", input.display()))?;

    // Load brick-map if provided
    let brick_map = brick_map
        .map(|p| resolver::load_brick_map(&p))
        .transpose()?;

    // Resolve all bricks referenced by graph nodes (deduplicated by brick_id + resolved version)
    let mut compiled_bricks: HashMap<(String, String), engine::CompiledBrick> = HashMap::new();
    let mut brick_manifests: HashMap<(String, String), manifest::BrickManifest> = HashMap::new();
    let mut node_brick_key: HashMap<String, (String, String)> = HashMap::new();

    for node in &graph_manifest.nodes {
        let resolved = resolver::resolve_brick(
            &node.brick.brick_id,
            &node.brick.version_or_range,
            &brick_dir,
            &brick_map,
        )?;
        let key = (node.brick.brick_id.clone(), resolved.manifest.version.clone());

        if node_brick_key.insert(node.node_id.clone(), key.clone()).is_some() {
            bail!("graph validation failed: duplicate node_id '{}'", node.node_id);
        }

        if let std::collections::hash_map::Entry::Vacant(entry) = compiled_bricks.entry(key.clone()) {
            eprintln!(
                "Resolved brick '{}' v{} ({} bytes)",
                key.0,
                key.1,
                resolved.wasm_bytes.len(),
            );
            eprintln!("  manifest: {}", resolved.manifest_path.display());
            eprintln!("  wasm: {}", resolved.wasm_path.display());

            let compiled = engine::CompiledBrick::new(&resolved.wasm_bytes)?;
            entry.insert(compiled);
            brick_manifests.insert(key, resolved.manifest);
        }
    }

    // Detect entry node: a node that is NOT the target of any edge
    let target_nodes: std::collections::HashSet<&str> = graph_manifest
        .edges
        .iter()
        .map(|e| e.target_node.as_str())
        .collect();

    let entry_nodes: Vec<&str> = graph_manifest
        .nodes
        .iter()
        .map(|n| n.node_id.as_str())
        .filter(|id| !target_nodes.contains(id))
        .collect();

    match entry_nodes.len() {
        0 => bail!("graph validation failed: no entry node found (all nodes are edge targets — cycle-only graph)"),
        1 => eprintln!("Entry node: '{}'", entry_nodes[0]),
        _ => bail!(
            "graph validation failed: multiple entry nodes found: {:?}",
            entry_nodes
        ),
    }
    let entry_node_id = entry_nodes[0].to_string();

    // Pre-index edges by source_node for O(1) lookup during routing
    let mut edges_by_source: HashMap<&str, Vec<&manifest::Edge>> = HashMap::new();
    // Also index edges by edge_id for O(1) lookup during mapping
    let mut edge_by_id: HashMap<&str, &manifest::Edge> = HashMap::new();
    for edge in &graph_manifest.edges {
        edges_by_source
            .entry(edge.source_node.as_str())
            .or_default()
            .push(edge);
        edge_by_id.insert(edge.edge_id.as_str(), edge);
    }

    // Generate trace/session IDs
    let trace_id = uuid::Uuid::new_v4().to_string();
    let session_id = uuid::Uuid::new_v4().to_string();

    // ── FIFO Queue Orchestration ───────────────────────────────────
    let mut queue: std::collections::VecDeque<Task> = std::collections::VecDeque::new();
    let mut step: u64 = 0;
    let mut terminals: Vec<TerminalResult> = Vec::new();

    // Push entry task
    queue.push_back(Task {
        node_id: entry_node_id,
        input_json: json_input,
        trigger_source_node_id: envelope::ROOT_TRIGGER.source_node_id.to_string(),
        trigger_source_step: envelope::ROOT_TRIGGER.source_step,
        trigger_edge_id: envelope::ROOT_TRIGGER.edge_id.to_string(),
        trigger_routing_reason: "entry".to_string(),
    });

    while let Some(task) = queue.pop_front() {
        // Safety budget: max_steps — emit structured failure, don't silently break
        if let Some(max) = max_steps {
            if step >= max {
                let msg = format!("max_steps budget ({max}) exhausted");
                eprintln!("Safety budget: {msg}");
                terminals.push(TerminalResult {
                    node_id: task.node_id.clone(),
                    brick_id: node_brick_key
                        .get(&task.node_id)
                        .map(|k| k.0.clone())
                        .unwrap_or_else(|| "__runtime__".to_string()),
                    step,
                    result: result::trap_failure("RESOURCE_EXCEEDED", msg),
                });
                break;
            }
        }

        let brick_key = node_brick_key
            .get(&task.node_id)
            .with_context(|| format!("no brick key recorded for node '{}'", task.node_id))?;
        let compiled = compiled_bricks
            .get(brick_key)
            .with_context(|| format!("no compiled brick for key {:?}", brick_key))?;
        let brick_manifest = brick_manifests
            .get(brick_key)
            .with_context(|| format!("no manifest for key {:?}", brick_key))?;

        // Build trigger reference from owned Task fields
        let trigger = envelope::Trigger {
            source_node_id: &task.trigger_source_node_id,
            source_step: task.trigger_source_step,
            edge_id: &task.trigger_edge_id,
        };

        let env = envelope::build_envelope(
            &task.input_json,
            &graph_manifest.graph_id,
            &graph_manifest.graph_version,
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
                let msg = format!(
                    "envelope too large for brick '{}': {} bytes > limits.max_input_bytes {}",
                    brick_key.0, env.len(), max_input,
                );
                eprintln!("  {msg}");
                pre_invoke_failure = Some(result::trap_failure("INVALID_INPUT", msg));
            }
        }

        if pre_invoke_failure.is_some() {
            eprintln!(
                "[step {}] Skipping invoke for brick '{}' node '{}' (pre-invoke failure)",
                step, brick_key.0, task.node_id,
            );
        } else {
            eprintln!(
                "[step {}] Invoking brick '{}' node '{}' ({} byte envelope)",
                step, brick_key.0, task.node_id, env.len(),
            );
        }

        // Invoke brick (or use pre-invoke failure)
        let (brick_result, raw_result_bytes, latency_ms) = if let Some(br) = pre_invoke_failure {
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
                    eprintln!("  Got {} byte result", result_bytes.len());
                    let decoded = match result::decode_result(&result_bytes) {
                        Ok(r) => r,
                        Err(e) => {
                            let msg = format!("{e:#}");
                            eprintln!("  Result rejected: {msg}");
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
                    eprintln!("  Brick trap: {msg}");
                    (result::trap_failure(error_class, msg), None, latency)
                }
            }
        };

        // Emit invoke trace record
        let trace_params = trace::InvokeTraceParams {
            trace_id: &trace_id,
            session_id: &session_id,
            step,
            graph_id: &graph_manifest.graph_id,
            graph_version: &graph_manifest.graph_version,
            brick_id: &brick_key.0,
            brick_version: &brick_key.1,
            bundle_digest: &brick_manifest.artifact.digest,
            node_id: &task.node_id,
            envelope_bytes: &env,
            trigger_source_node_id: &task.trigger_source_node_id,
            trigger_source_step: task.trigger_source_step,
            trigger_edge_id: &task.trigger_edge_id,
            trigger_routing_reason: &task.trigger_routing_reason,
        };
        tracer.emit_invoke(
            &trace_params,
            &brick_result,
            raw_result_bytes.as_deref(),
            latency_ms,
            &now_rfc3339(),
        );

        eprintln!("  Result type: {}", brick_result.result_type());

        // ── Routing ────────────────────────────────────────────────
        let outbound: Vec<&manifest::Edge> = edges_by_source
            .get(task.node_id.as_str())
            .cloned()
            .unwrap_or_default();

        // Extract output confidence for threshold gating
        let output_confidence = brick_result
            .output()
            .and_then(mapping::extract_confidence);

        let routed = router::route(&outbound, &brick_result, output_confidence);

        if routed.is_empty() {
            // Terminal node
            eprintln!("  Terminal node (no outbound edges dispatched)");
            terminals.push(TerminalResult {
                node_id: task.node_id.clone(),
                brick_id: brick_key.0.clone(),
                step,
                result: brick_result,
            });
        } else {
            // Dispatch routed edges: apply field mapping, push tasks
            for routed_edge in &routed {
                let edge_def = edge_by_id.get(routed_edge.edge_id.as_str())
                    .with_context(|| format!("routed edge '{}' not found in graph", routed_edge.edge_id))?;

                // Build mapped input for target node
                let mapped_input = if edge_def.mapping.is_empty() {
                    // No mapping: pass through the source output wrapped as { "input": <output> }
                    // so build_envelope's auto-detection doesn't collide with output keys
                    match brick_result.output() {
                        Some(output) => serde_json::json!({ "input": mapping::cbor_to_json(output) }),
                        None => serde_json::json!({ "input": null }),
                    }
                } else {
                    // Apply field mappings: resolve from source, set in target
                    // Source root: the output wrapped under "output" key so that
                    // "output.label" resolves correctly via dot-path
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
                            .with_context(|| format!(
                                "mapping '{}' → '{}': source path '{}' not found in output",
                                fm.from, fm.to, fm.from,
                            ))?;
                        let overlay = mapping::set_path(&fm.to, resolved);
                        target_cbor = mapping::merge_maps(target_cbor, overlay);
                    }

                    // Convert to JSON — set_path already builds the full structure
                    // e.g. set_path("input.sentiment", val) → {"input":{"sentiment":val}}
                    mapping::cbor_to_json(&target_cbor)
                };

                eprintln!(
                    "  Route: edge '{}' → node '{}'",
                    routed_edge.edge_id, routed_edge.target_node
                );

                // Safety budget: max_queued
                if queue.len() as u64 >= max_queued {
                    bail!(
                        "safety budget exceeded: queue size {} >= max_queued {}",
                        queue.len(),
                        max_queued,
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

    // ── Final Output ───────────────────────────────────────────────
    if terminals.is_empty() {
        eprintln!("Warning: no terminal nodes reached (graph may have been budget-limited)");
        std::process::exit(1);
    }

    if all_terminals {
        // --all-terminals: JSON array of all terminal results, ordered by step
        let arr: Vec<serde_json::Value> = terminals
            .iter()
            .map(|t| {
                let mut obj = serde_json::json!({
                    "node_id": t.node_id,
                    "brick_id": t.brick_id,
                    "step": t.step,
                    "type": t.result.result_type(),
                });
                if let Some(output) = t.result.output() {
                    obj["output"] = mapping::cbor_to_json(output);
                }
                if let Some(error) = t.result.error() {
                    obj["error"] = serde_json::json!({
                        "error_class": error.error_class,
                        "message": error.message,
                    });
                }
                obj
            })
            .collect();
        println!("{}", serde_json::to_string_pretty(&arr)?);
    } else {
        // Default: last terminal's output
        let last = terminals.last().unwrap();
        match &last.result {
            result::BrickResult::Success { output }
            | result::BrickResult::LowConfidence { output, .. } => {
                let json = mapping::cbor_to_json(output);
                println!("{}", serde_json::to_string_pretty(&json)?);
            }
            result::BrickResult::Failure { error } => {
                let json = serde_json::json!({
                    "error": {
                        "error_class": error.error_class,
                        "message": error.message,
                    }
                });
                println!("{}", serde_json::to_string_pretty(&json)?);
                std::process::exit(1);
            }
        }
    }

    Ok(())
}
