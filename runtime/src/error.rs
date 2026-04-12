use std::fmt;
use std::path::PathBuf;

#[derive(Debug)]
pub enum RuntimeError {
    ManifestLoad { path: PathBuf, detail: String },
    ManifestValidation { path: PathBuf, detail: String },
    BrickResolution { brick_id: String, detail: String },
    DigestMismatch { brick_id: String, expected: String, actual: String },
    SizeMismatch { brick_id: String, expected: u64, actual: u64 },
    VersionMismatch { brick_id: String, manifest_version: String, required_range: String },
    WasmSelection { brick_id: String, detail: String },
    GraphValidation { detail: String },
    EntryNode { detail: String },
    InvokeError { node_id: String, detail: String },
    ResultBoundary { node_id: String, detail: String },
    QueueOverflow { max_queued: u64 },
    StepBudgetExceeded { max_steps: u64 },
    Unsupported { detail: String },
}

impl fmt::Display for RuntimeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ManifestLoad { path, detail } => write!(f, "failed to load manifest '{}': {detail}", path.display()),
            Self::ManifestValidation { path, detail } => write!(f, "invalid manifest '{}': {detail}", path.display()),
            Self::BrickResolution { brick_id, detail } => write!(f, "cannot resolve brick '{brick_id}': {detail}"),
            Self::DigestMismatch { brick_id, expected, actual } => write!(f, "digest mismatch for '{brick_id}': expected {expected}, got {actual}"),
            Self::SizeMismatch { brick_id, expected, actual } => write!(f, "size mismatch for '{brick_id}': expected {expected} bytes, got {actual}"),
            Self::VersionMismatch { brick_id, manifest_version, required_range } => write!(f, "version mismatch for '{brick_id}': manifest {manifest_version} does not satisfy {required_range}"),
            Self::WasmSelection { brick_id, detail } => write!(f, "cannot select .wasm for '{brick_id}': {detail}"),
            Self::GraphValidation { detail } => write!(f, "graph validation failed: {detail}"),
            Self::EntryNode { detail } => write!(f, "entry node detection failed: {detail}"),
            Self::InvokeError { node_id, detail } => write!(f, "invocation failed at node '{node_id}': {detail}"),
            Self::ResultBoundary { node_id, detail } => write!(f, "result boundary violation at node '{node_id}': {detail}"),
            Self::QueueOverflow { max_queued } => write!(f, "task queue exceeded {max_queued} entries"),
            Self::StepBudgetExceeded { max_steps } => write!(f, "step budget exceeded: {max_steps} steps"),
            Self::Unsupported { detail } => write!(f, "unsupported feature: {detail}"),
        }
    }
}

impl std::error::Error for RuntimeError {}
