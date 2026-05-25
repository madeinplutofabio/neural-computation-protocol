use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use sha2::{Digest, Sha256};

use crate::manifest::{self, BrickManifest};

/// A resolved brick: parsed manifest + verified WASM bytes.
pub struct ResolvedBrick {
    pub manifest: BrickManifest,
    pub wasm_bytes: Vec<u8>,
    pub manifest_path: PathBuf,
    pub wasm_path: PathBuf,
}

/// Brick-map: maps brick_id → directory path, resolved relative to the map file's parent.
pub struct BrickMap {
    pub base_dir: PathBuf,
    pub map: HashMap<String, PathBuf>,
}

impl BrickMap {
    /// Resolve a brick_id to its directory, joining relative paths against base_dir.
    pub fn resolve(&self, brick_id: &str) -> Option<PathBuf> {
        self.map.get(brick_id).map(|p| {
            if p.is_relative() {
                self.base_dir.join(p)
            } else {
                p.clone()
            }
        })
    }
}

/// Load a brick-map file (YAML or JSON based on extension).
pub fn load_brick_map(path: &Path) -> Result<BrickMap> {
    let contents = std::fs::read_to_string(path)
        .with_context(|| format!("reading brick-map '{}'", path.display()))?;

    let map: HashMap<String, PathBuf> = match path.extension().and_then(|e| e.to_str()) {
        Some("json") => serde_json::from_str(&contents)
            .with_context(|| format!("parsing brick-map as JSON '{}'", path.display()))?,
        _ => serde_yaml::from_str(&contents)
            .with_context(|| format!("parsing brick-map as YAML '{}'", path.display()))?,
    };

    let base_dir = path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .to_path_buf();

    Ok(BrickMap { base_dir, map })
}

/// Resolve a brick_id to its directory.
///
/// Precedence:
/// 1. brick-map entry (exact brick_id match, resolved relative to map file)
/// 2. `<brick_dir>/<short_name>/` where short_name = last dot-segment of brick_id
fn resolve_brick_dir(
    brick_id: &str,
    brick_dir: &Path,
    brick_map: &Option<BrickMap>,
) -> Result<PathBuf> {
    if let Some(map) = brick_map {
        if let Some(dir) = map.resolve(brick_id) {
            return Ok(dir);
        }
    }

    let short_name = brick_id.rsplit('.').next().unwrap_or(brick_id);
    let dir = brick_dir.join(short_name);

    if !dir.is_dir() {
        bail!(
            "cannot resolve brick '{}': directory '{}' does not exist",
            brick_id,
            dir.display()
        );
    }

    Ok(dir)
}

/// Select the .wasm file from a brick directory (deterministic rule).
///
/// Precedence:
/// 1. manifest.artifact.path if present
/// 2. Exactly one .wasm file in directory
/// 3. <short_name>.wasm
/// 4. Error listing candidates (sorted for determinism)
fn select_wasm(brick_id: &str, dir: &Path, artifact_path: &Option<PathBuf>) -> Result<PathBuf> {
    // 1. artifact.path
    if let Some(rel_path) = artifact_path {
        let full = dir.join(rel_path);
        if full.is_file() {
            return Ok(full);
        }
        bail!(
            "cannot select .wasm for '{}': artifact.path '{}' not found in '{}'",
            brick_id,
            rel_path.display(),
            dir.display()
        );
    }

    // Scan directory for .wasm files
    let mut wasm_files: Vec<PathBuf> = std::fs::read_dir(dir)
        .with_context(|| format!("reading directory '{}'", dir.display()))?
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|p| p.extension().is_some_and(|ext| ext == "wasm"))
        .collect();
    wasm_files.sort();

    // 2. Exactly one .wasm
    if wasm_files.len() == 1 {
        return Ok(wasm_files.into_iter().next().unwrap());
    }

    // 3. <short_name>.wasm
    let short_name = brick_id.rsplit('.').next().unwrap_or(brick_id);
    let named = dir.join(format!("{short_name}.wasm"));
    if named.is_file() {
        return Ok(named);
    }

    // 4. Error (sorted candidates for determinism)
    let candidates: Vec<String> = wasm_files
        .iter()
        .map(|p| {
            p.file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned()
        })
        .collect();

    if candidates.is_empty() {
        bail!(
            "cannot select .wasm for '{}': no .wasm files in '{}'",
            brick_id,
            dir.display()
        );
    }

    bail!(
        "cannot select .wasm for '{}': multiple .wasm files in '{}': [{}]. \
         Use artifact.path in manifest or ensure only one .wasm exists.",
        brick_id,
        dir.display(),
        candidates.join(", ")
    );
}

/// Verify digest and size of WASM bytes against manifest.
fn verify_artifact(brick_id: &str, manifest: &BrickManifest, wasm_bytes: &[u8]) -> Result<()> {
    // Size check
    let actual_size = wasm_bytes.len() as u64;
    if actual_size != manifest.artifact.size_bytes {
        bail!(
            "size mismatch for '{}': manifest declares {} bytes, actual {} bytes",
            brick_id,
            manifest.artifact.size_bytes,
            actual_size
        );
    }

    // Digest check — expect "sha256:<hex>"
    let expected_digest = &manifest.artifact.digest;
    let expected_hex = expected_digest.strip_prefix("sha256:").with_context(|| {
        format!(
            "unsupported digest format for '{}': expected 'sha256:<hex>', got '{}'",
            brick_id, expected_digest
        )
    })?;

    // Strict validation: must be exactly 64 lowercase hex chars
    let expected_hex = expected_hex.to_ascii_lowercase();
    if expected_hex.len() != 64 || !expected_hex.chars().all(|c| c.is_ascii_hexdigit()) {
        bail!(
            "invalid digest for '{}': expected 64 hex chars after 'sha256:', got '{}'",
            brick_id,
            expected_hex
        );
    }

    let mut hasher = Sha256::new();
    hasher.update(wasm_bytes);
    let actual_hex = hex::encode(hasher.finalize());

    if actual_hex != expected_hex {
        bail!(
            "digest mismatch for '{}': expected sha256:{}, got sha256:{}",
            brick_id,
            expected_hex,
            actual_hex
        );
    }

    Ok(())
}

/// Check brick manifest version against a node's version_or_range requirement.
pub fn check_version(brick_id: &str, manifest_version: &str, version_or_range: &str) -> Result<()> {
    let version = semver::Version::parse(manifest_version).with_context(|| {
        format!(
            "invalid semver in manifest for '{}': '{}'",
            brick_id, manifest_version
        )
    })?;

    let range_str = version_or_range.trim();
    let req = semver::VersionReq::parse(range_str).with_context(|| {
        format!(
            "invalid version range for '{}': '{}' (manifest version: {})",
            brick_id, range_str, manifest_version
        )
    })?;

    if !req.matches(&version) {
        bail!(
            "version mismatch for '{}': manifest version {} does not satisfy {}",
            brick_id,
            manifest_version,
            range_str
        );
    }

    Ok(())
}

/// Validate manifest invariants required by the runtime (Phase 2).
fn validate_manifest(brick_id: &str, manifest: &BrickManifest, manifest_path: &Path) -> Result<()> {
    if manifest.artifact.format != "wasm" {
        bail!(
            "invalid manifest '{}': artifact.format must be 'wasm', got '{}'",
            manifest_path.display(),
            manifest.artifact.format
        );
    }

    if manifest.artifact.entrypoint != "invoke" {
        bail!(
            "invalid manifest '{}': artifact.entrypoint must be 'invoke', got '{}'",
            manifest_path.display(),
            manifest.artifact.entrypoint
        );
    }

    if !manifest.capabilities.is_empty() {
        bail!(
            "invalid manifest '{}': capabilities must be empty in v0.2, got {:?}",
            manifest_path.display(),
            manifest.capabilities
        );
    }

    if !manifest.required_runtime_features.is_empty() {
        bail!(
            "unsupported feature: brick '{}' declares required_runtime_features {:?} \
             (Phase 2 runtime does not support runtime intrinsics)",
            brick_id,
            manifest.required_runtime_features
        );
    }

    // Limits sanity
    if manifest.limits.max_ms == 0 {
        bail!(
            "invalid manifest '{}': limits.max_ms must be > 0",
            manifest_path.display()
        );
    }
    if manifest.limits.max_mem_mb == 0 {
        bail!(
            "invalid manifest '{}': limits.max_mem_mb must be > 0",
            manifest_path.display()
        );
    }
    if manifest.limits.max_output_bytes == 0 {
        bail!(
            "invalid manifest '{}': limits.max_output_bytes must be > 0",
            manifest_path.display()
        );
    }
    if manifest.limits.max_input_bytes.is_none() {
        bail!(
            "invalid manifest '{}': limits.max_input_bytes is required \
             (Phase 2 runtime enforces input size guards)",
            manifest_path.display()
        );
    }

    if manifest.carry_state_class != "none" {
        bail!(
            "unsupported feature: brick '{}' declares carry_state_class '{}' \
             (Phase 2 runtime only supports 'none')",
            brick_id,
            manifest.carry_state_class
        );
    }

    if !manifest.graph_ref_slots.is_empty() {
        bail!(
            "unsupported feature: brick '{}' declares {} graph_ref_slots \
             (Phase 2 runtime does not support graph refs)",
            brick_id,
            manifest.graph_ref_slots.len()
        );
    }

    Ok(())
}

/// Resolve, verify, and validate a single brick.
pub fn resolve_brick(
    brick_id: &str,
    version_or_range: &str,
    brick_dir: &Path,
    brick_map: &Option<BrickMap>,
) -> Result<ResolvedBrick> {
    // 1. Find brick directory
    let dir = resolve_brick_dir(brick_id, brick_dir, brick_map)?;

    // 2. Load manifest
    let manifest_path = dir.join("manifest.yaml");
    let manifest = manifest::load_brick(&manifest_path)?;

    // 3. Validate manifest invariants
    validate_manifest(brick_id, &manifest, &manifest_path)?;

    // 4. Check version
    check_version(brick_id, &manifest.version, version_or_range)?;

    // 5. Select .wasm file
    let wasm_path = select_wasm(brick_id, &dir, &manifest.artifact.path)?;

    // 6. Read and verify WASM bytes
    let wasm_bytes = std::fs::read(&wasm_path)
        .with_context(|| format!("reading WASM file '{}'", wasm_path.display()))?;
    verify_artifact(brick_id, &manifest, &wasm_bytes)?;

    Ok(ResolvedBrick {
        manifest,
        wasm_bytes,
        manifest_path,
        wasm_path,
    })
}
