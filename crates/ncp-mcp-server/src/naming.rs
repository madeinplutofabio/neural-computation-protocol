//! Tool-name derivation rule (locked, per docs/MCP_ADAPTER.md §3).
//!
//! Maps NCP graph IDs to MCP tool names by replacing any character not in
//! `[A-Za-z0-9_.-]` with `_`. Dots are preserved (the MCP spec allows dots
//! in tool names; `admin.tools.list` is the spec's own example).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, Result};

/// Maximum tool-name length per MCP spec.
pub const MAX_TOOL_NAME_LEN: usize = 128;

/// Derives an MCP tool name from a graph ID per the locked rule
/// (see docs/MCP_ADAPTER.md §3).
///
/// Replaces any character not in `[A-Za-z0-9_.-]` with `_`. Returns an
/// error if the derived name is empty or exceeds [`MAX_TOOL_NAME_LEN`].
///
/// Note: because the rule REPLACES (not removes) disallowed characters,
/// the derived name is empty only when the input is empty. An input like
/// `///` produces `___` (a valid, non-empty tool name), not an error.
///
/// Since the function only emits ASCII characters, byte length equals
/// character count — no need to disambiguate.
pub fn graph_id_to_tool_name(graph_id: &str) -> Result<String> {
    let derived: String = graph_id
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();

    if derived.is_empty() {
        return Err(anyhow!(
            "derived tool name is empty (graph_id `{}` was empty)",
            graph_id
        ));
    }
    if derived.len() > MAX_TOOL_NAME_LEN {
        return Err(anyhow!(
            "derived tool name `{}` exceeds MCP spec limit ({} > {})",
            derived,
            derived.len(),
            MAX_TOOL_NAME_LEN
        ));
    }
    Ok(derived)
}

/// Validates that a slice of (graph-path, tool-name) pairs contains no
/// duplicate tool names.
///
/// On collision, returns an error naming both graph paths that produced
/// the same tool name. Adopters must rename or rework one of the
/// colliding graphs.
pub fn validate_no_collisions(pairs: &[(PathBuf, String)]) -> Result<()> {
    let mut seen: HashMap<&str, &Path> = HashMap::new();
    for (path, name) in pairs {
        if let Some(prior) = seen.insert(name.as_str(), path.as_path()) {
            return Err(anyhow!(
                "tool name `{}` derived from both `{}` and `{}` — graphs must produce unique tool names",
                name,
                prior.display(),
                path.display()
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    // ── graph_id_to_tool_name ───────────────────────────────────────────

    #[test]
    fn simple_reverse_domain_case() {
        // Spec-aligned graph_id: all chars already in allowed set.
        assert_eq!(
            graph_id_to_tool_name("org.ncp-examples.echo-pipeline").unwrap(),
            "org.ncp-examples.echo-pipeline"
        );
    }

    #[test]
    fn dots_preserved() {
        assert_eq!(graph_id_to_tool_name("a.b.c").unwrap(), "a.b.c");
    }

    #[test]
    fn hyphens_preserved() {
        assert_eq!(graph_id_to_tool_name("a-b-c").unwrap(), "a-b-c");
    }

    #[test]
    fn underscores_preserved() {
        assert_eq!(graph_id_to_tool_name("a_b_c").unwrap(), "a_b_c");
    }

    #[test]
    fn digits_preserved() {
        assert_eq!(graph_id_to_tool_name("a0.b1.c2").unwrap(), "a0.b1.c2");
    }

    #[test]
    fn slashes_become_underscores() {
        assert_eq!(graph_id_to_tool_name("foo/bar/baz").unwrap(), "foo_bar_baz");
    }

    #[test]
    fn mixed_allowed_and_disallowed() {
        assert_eq!(
            graph_id_to_tool_name("foo.bar/baz@qux").unwrap(),
            "foo.bar_baz_qux"
        );
    }

    #[test]
    fn unicode_codepoints_each_become_one_underscore() {
        // Each non-ASCII code point becomes a single `_`.
        // "café" = c + a + f + é (4 code points; é is U+00E9 → `_`).
        assert_eq!(graph_id_to_tool_name("café").unwrap(), "caf_");
    }

    #[test]
    fn all_disallowed_chars_become_all_underscores_not_error() {
        // Disallowed chars are REPLACED with `_`, not removed.
        // "///" → "___" (a valid, non-empty tool name).
        assert_eq!(graph_id_to_tool_name("///").unwrap(), "___");
    }

    #[test]
    fn empty_input_errors() {
        let err = graph_id_to_tool_name("").unwrap_err().to_string();
        assert!(err.contains("derived tool name is empty"), "got: {err}");
    }

    #[test]
    fn at_max_len_succeeds() {
        let name = "a".repeat(MAX_TOOL_NAME_LEN);
        assert_eq!(graph_id_to_tool_name(&name).unwrap(), name);
    }

    #[test]
    fn over_max_len_errors() {
        let name = "a".repeat(MAX_TOOL_NAME_LEN + 1);
        let err = graph_id_to_tool_name(&name).unwrap_err().to_string();
        assert!(err.contains("exceeds MCP spec limit"), "got: {err}");
    }

    // ── validate_no_collisions ──────────────────────────────────────────

    #[test]
    fn no_collisions_passes() {
        let pairs = vec![
            (PathBuf::from("a.yaml"), "tool_a".to_string()),
            (PathBuf::from("b.yaml"), "tool_b".to_string()),
        ];
        assert!(validate_no_collisions(&pairs).is_ok());
    }

    #[test]
    fn empty_input_passes() {
        let pairs: Vec<(PathBuf, String)> = vec![];
        assert!(validate_no_collisions(&pairs).is_ok());
    }

    #[test]
    fn collision_errors_and_names_both_paths() {
        let pairs = vec![
            (PathBuf::from("a.yaml"), "shared".to_string()),
            (PathBuf::from("b.yaml"), "shared".to_string()),
        ];
        let err = validate_no_collisions(&pairs).unwrap_err().to_string();
        assert!(err.contains("a.yaml"), "got: {err}");
        assert!(err.contains("b.yaml"), "got: {err}");
        assert!(err.contains("shared"), "got: {err}");
    }
}
