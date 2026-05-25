use crate::manifest::Edge;
use crate::result::BrickResult;

/// A routed edge: the edge_id and target_node_id to dispatch to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoutedEdge {
    pub edge_id: String,
    pub target_node: String,
}

/// Evaluate outbound edges from a source node per §7.4.1.
///
/// Returns an ordered list of edges to dispatch:
/// - On error (Failure/LowConfidence): at most one edge (single-target).
/// - On success (Success): all eligible edges in deterministic order (fan-out).
/// - Empty list = terminal node.
pub fn route(
    outbound_edges: &[&Edge],
    result: &BrickResult,
    output_confidence: Option<f64>,
) -> Vec<RoutedEdge> {
    match result {
        // LowConfidence is routed as error path per §7.4.1 (error_class = LOW_CONFIDENCE).
        BrickResult::Failure { error } | BrickResult::LowConfidence { error, .. } => {
            route_on_error(outbound_edges, &error.error_class)
        }
        BrickResult::Success { .. } => {
            // §7.4.1 (Normative): if output.confidence is present but not a valid number in [0,1],
            // runtime MUST treat as Failure(INVALID_INPUT) and route via on_error.
            if let Some(c) = output_confidence {
                if !c.is_finite() || !(0.0..=1.0).contains(&c) {
                    return route_on_error(outbound_edges, "INVALID_INPUT");
                }
            }
            route_on_success(outbound_edges, output_confidence)
        }
    }
}

/// On error: collect edges whose on_error map contains the error_class,
/// order by priority descending then edge_id ascending, dispatch first only.
fn route_on_error(edges: &[&Edge], error_class: &str) -> Vec<RoutedEdge> {
    let mut candidates: Vec<&Edge> = edges
        .iter()
        .filter(|e| {
            // Phase 2: on_error map values (Vec<String>) are ignored — target comes from
            // edge.target_node. The map keys are the error_class filter.
            e.on_error
                .as_ref()
                .map_or(false, |m| m.contains_key(error_class))
        })
        .copied()
        .collect();

    // Sort: priority descending, then edge_id ascending
    candidates.sort_by(|a, b| {
        let pa = a.priority.unwrap_or(0);
        let pb = b.priority.unwrap_or(0);
        pb.cmp(&pa).then_with(|| a.edge_id.cmp(&b.edge_id))
    });

    // Single-target: dispatch first only
    candidates
        .first()
        .map(|e| {
            vec![RoutedEdge {
                edge_id: e.edge_id.clone(),
                target_node: e.target_node.clone(),
            }]
        })
        .unwrap_or_default()
}

/// On success: collect edges with on_success, apply threshold gating,
/// order by weight descending then edge_id ascending, dispatch all (fan-out).
fn route_on_success(edges: &[&Edge], output_confidence: Option<f64>) -> Vec<RoutedEdge> {
    let mut candidates: Vec<&Edge> = edges
        .iter()
        .filter(|e| e.on_success.is_some())
        .filter(|e| {
            let on_success = e.on_success.as_ref().unwrap();
            match (on_success.threshold, output_confidence) {
                // Threshold defined, confidence present: gate
                (Some(threshold), Some(confidence)) => confidence >= threshold,
                // Threshold defined, confidence absent: eligible
                (Some(_), None) => true,
                // No threshold: always eligible
                (None, _) => true,
            }
        })
        .copied()
        .collect();

    // Sort: weight descending, then edge_id ascending
    candidates.sort_by(|a, b| {
        let mut wa = a.on_success.as_ref().and_then(|s| s.weight).unwrap_or(0.0);
        let mut wb = b.on_success.as_ref().and_then(|s| s.weight).unwrap_or(0.0);

        // Defensive determinism: treat non-finite weights as 0.0
        if !wa.is_finite() {
            wa = 0.0;
        }
        if !wb.is_finite() {
            wb = 0.0;
        }

        wb.total_cmp(&wa).then_with(|| a.edge_id.cmp(&b.edge_id))
    });

    candidates
        .iter()
        .map(|e| RoutedEdge {
            edge_id: e.edge_id.clone(),
            target_node: e.target_node.clone(),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::manifest::{Edge, OnSuccess};
    use crate::result::{BrickResult, ErrorObject};
    use std::collections::HashMap;

    fn failure(error_class: &str) -> BrickResult {
        BrickResult::Failure {
            error: ErrorObject {
                error_class: error_class.to_string(),
                message: "test".to_string(),
                retry_advice: None,
                severity: None,
            },
        }
    }

    fn low_confidence() -> BrickResult {
        BrickResult::LowConfidence {
            output: crate::result::CborValue::Null,
            error: ErrorObject {
                error_class: "LOW_CONFIDENCE".to_string(),
                message: "test".to_string(),
                retry_advice: None,
                severity: None,
            },
        }
    }

    fn success() -> BrickResult {
        BrickResult::Success {
            output: crate::result::CborValue::Null,
        }
    }

    fn edge(id: &str, target: &str) -> Edge {
        Edge {
            edge_id: id.to_string(),
            source_node: "src".to_string(),
            target_node: target.to_string(),
            mapping: vec![],
            on_success: None,
            on_error: None,
            priority: None,
        }
    }

    fn ids(routed: &[RoutedEdge]) -> Vec<&str> {
        routed.iter().map(|r| r.edge_id.as_str()).collect()
    }

    // ── Conformance vectors ─────────────────────────────────────────

    #[test]
    fn v1_on_error_single_match() {
        let mut e1 = edge("e1", "recovery_node");
        e1.on_error = Some(HashMap::from([(
            "COMPUTATION_ERROR".into(),
            vec!["recovery_node".into()],
        )]));
        let mut e2 = edge("e2", "fallback_node");
        e2.on_error = Some(HashMap::from([(
            "INVALID_INPUT".into(),
            vec!["fallback_node".into()],
        )]));

        let result = failure("COMPUTATION_ERROR");
        let edges: Vec<&Edge> = vec![&e1, &e2];
        assert_eq!(ids(&route(&edges, &result, None)), vec!["e1"]);
    }

    #[test]
    fn v2_on_error_no_match_terminal() {
        let mut e1 = edge("e1", "fallback_node");
        e1.on_error = Some(HashMap::from([(
            "INVALID_INPUT".into(),
            vec!["fallback_node".into()],
        )]));

        let result = failure("COMPUTATION_ERROR");
        let edges: Vec<&Edge> = vec![&e1];
        assert!(route(&edges, &result, None).is_empty());
    }

    #[test]
    fn v3_on_error_priority_ordering() {
        let mut e1 = edge("e1", "node_a");
        e1.priority = Some(5);
        e1.on_error = Some(HashMap::from([(
            "COMPUTATION_ERROR".into(),
            vec!["node_a".into()],
        )]));
        let mut e2 = edge("e2", "node_b");
        e2.priority = Some(10);
        e2.on_error = Some(HashMap::from([(
            "COMPUTATION_ERROR".into(),
            vec!["node_b".into()],
        )]));

        let result = failure("COMPUTATION_ERROR");
        let edges: Vec<&Edge> = vec![&e1, &e2];
        assert_eq!(ids(&route(&edges, &result, None)), vec!["e2"]);
    }

    #[test]
    fn v4_on_error_tiebreak_edge_id() {
        let mut eb = edge("edge_b", "node_a");
        eb.priority = Some(10);
        eb.on_error = Some(HashMap::from([(
            "COMPUTATION_ERROR".into(),
            vec!["node_a".into()],
        )]));
        let mut ea = edge("edge_a", "node_b");
        ea.priority = Some(10);
        ea.on_error = Some(HashMap::from([(
            "COMPUTATION_ERROR".into(),
            vec!["node_b".into()],
        )]));

        let result = failure("COMPUTATION_ERROR");
        let edges: Vec<&Edge> = vec![&eb, &ea];
        assert_eq!(ids(&route(&edges, &result, None)), vec!["edge_a"]);
    }

    #[test]
    fn v5_low_confidence_routes_via_on_error() {
        let mut e1 = edge("e1", "llm_node");
        e1.on_error = Some(HashMap::from([(
            "LOW_CONFIDENCE".into(),
            vec!["llm_node".into()],
        )]));
        let mut e2 = edge("e2", "next_node");
        e2.on_success = Some(OnSuccess {
            weight: Some(1.0),
            threshold: None,
        });

        let result = low_confidence();
        let edges: Vec<&Edge> = vec![&e1, &e2];
        assert_eq!(ids(&route(&edges, &result, None)), vec!["e1"]);
    }

    #[test]
    fn v6_on_success_above_threshold() {
        let mut e1 = edge("e1", "next_node");
        e1.on_success = Some(OnSuccess {
            threshold: Some(0.5),
            weight: Some(1.0),
        });

        let result = success();
        let edges: Vec<&Edge> = vec![&e1];
        assert_eq!(ids(&route(&edges, &result, Some(0.8))), vec!["e1"]);
    }

    #[test]
    fn v7_on_success_below_threshold_terminal() {
        let mut e1 = edge("e1", "next_node");
        e1.on_success = Some(OnSuccess {
            threshold: Some(0.9),
            weight: Some(1.0),
        });

        let result = success();
        let edges: Vec<&Edge> = vec![&e1];
        assert!(route(&edges, &result, Some(0.7)).is_empty());
    }

    #[test]
    fn v8_on_success_no_confidence_eligible() {
        let mut e1 = edge("e1", "next_node");
        e1.on_success = Some(OnSuccess {
            threshold: Some(0.9),
            weight: Some(1.0),
        });

        let result = success();
        let edges: Vec<&Edge> = vec![&e1];
        assert_eq!(ids(&route(&edges, &result, None)), vec!["e1"]);
    }

    #[test]
    fn v9_on_success_fanout_weight_ordering() {
        let mut e1 = edge("e1", "node_a");
        e1.on_success = Some(OnSuccess {
            weight: Some(0.5),
            threshold: None,
        });
        let mut e2 = edge("e2", "node_b");
        e2.on_success = Some(OnSuccess {
            weight: Some(0.9),
            threshold: None,
        });
        let mut e3 = edge("e3", "node_c");
        e3.on_success = Some(OnSuccess {
            weight: Some(0.9),
            threshold: None,
        });

        let result = success();
        let edges: Vec<&Edge> = vec![&e1, &e2, &e3];
        assert_eq!(ids(&route(&edges, &result, None)), vec!["e2", "e3", "e1"]);
    }

    #[test]
    fn v10_on_success_weight_tiebreak_edge_id() {
        let mut ezz = edge("zz_edge", "node_a");
        ezz.on_success = Some(OnSuccess {
            weight: Some(1.0),
            threshold: None,
        });
        let mut eaa = edge("aa_edge", "node_b");
        eaa.on_success = Some(OnSuccess {
            weight: Some(1.0),
            threshold: None,
        });

        let result = success();
        let edges: Vec<&Edge> = vec![&ezz, &eaa];
        assert_eq!(
            ids(&route(&edges, &result, None)),
            vec!["aa_edge", "zz_edge"]
        );
    }

    #[test]
    fn v11_invalid_confidence_routes_as_invalid_input_via_on_error() {
        let mut on_err = edge("e_err", "err_node");
        on_err.on_error = Some(HashMap::from([(
            "INVALID_INPUT".into(),
            vec!["err_node".into()],
        )]));

        let mut on_ok = edge("e_ok", "next");
        on_ok.on_success = Some(OnSuccess {
            threshold: Some(0.5),
            weight: Some(1.0),
        });

        let result = success();
        let edges: Vec<&Edge> = vec![&on_ok, &on_err];

        // confidence present but invalid (>1.0) => treat as Failure(INVALID_INPUT) => route via on_error
        assert_eq!(ids(&route(&edges, &result, Some(1.2))), vec!["e_err"]);
    }

    // ── Non-finite weight determinism ───────────────────────────────

    #[test]
    fn v12_nan_weight_sorted_after_finite() {
        let mut ea = edge("ea", "node_a");
        ea.on_success = Some(OnSuccess {
            weight: Some(f64::NAN),
            threshold: None,
        });
        let mut eb = edge("eb", "node_b");
        eb.on_success = Some(OnSuccess {
            weight: Some(0.1),
            threshold: None,
        });

        let result = success();
        let edges: Vec<&Edge> = vec![&ea, &eb];
        // NaN coerced to 0.0, so eb (0.1) > ea (0.0) => eb first
        assert_eq!(ids(&route(&edges, &result, None)), vec!["eb", "ea"]);
    }

    #[test]
    fn v13_both_nan_weights_tiebreak_by_edge_id() {
        let mut eb = edge("eb", "node_b");
        eb.on_success = Some(OnSuccess {
            weight: Some(f64::NAN),
            threshold: None,
        });
        let mut ea = edge("ea", "node_a");
        ea.on_success = Some(OnSuccess {
            weight: Some(f64::NAN),
            threshold: None,
        });

        let result = success();
        let edges: Vec<&Edge> = vec![&eb, &ea];
        // Both coerced to 0.0 => tiebreak by edge_id ascending
        assert_eq!(ids(&route(&edges, &result, None)), vec!["ea", "eb"]);
    }
}
