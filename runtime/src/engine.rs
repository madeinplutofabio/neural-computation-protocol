use anyhow::{bail, Context, Result};
use wasmtime::{Engine, Linker, Module, Store, StoreLimits, StoreLimitsBuilder};

struct StoreState {
    limits: StoreLimits,
}

pub struct CompiledBrick {
    engine: Engine,
    module: Module,
}

impl CompiledBrick {
    pub fn new(wasm_bytes: &[u8]) -> Result<Self> {
        // Keep this shape so you can later do:
        // let mut cfg = wasmtime::Config::new();
        // cfg.consume_fuel(true);
        // let engine = Engine::new(&cfg)?;
        let engine = Engine::default();
        let module = Module::new(&engine, wasm_bytes)
            .map_err(|e| anyhow::anyhow!("compiling WASM module: {e}"))?;
        Ok(Self { engine, module })
    }

    /// Invoke using Model B: alloc → write → invoke → read → free
    pub fn invoke(
        &self,
        envelope: &[u8],
        max_mem_mb: u64,
        max_output_bytes: u64,
    ) -> Result<Vec<u8>> {
        let mem_bytes = (max_mem_mb as usize)
            .saturating_mul(1024)
            .saturating_mul(1024);
        if mem_bytes == 0 {
            bail!("limits.max_mem_mb must be > 0");
        }
        let limits = StoreLimitsBuilder::new().memory_size(mem_bytes).build();
        let mut store = Store::new(&self.engine, StoreState { limits });
        store.limiter(|state| &mut state.limits);

        let linker = Linker::new(&self.engine);
        let instance = linker
            .instantiate(&mut store, &self.module)
            .map_err(|e| anyhow::anyhow!("instantiating WASM module: {e}"))?;

        let memory = instance
            .get_memory(&mut store, "memory")
            .ok_or_else(|| anyhow::anyhow!("WASM module must export 'memory'"))?;

        let alloc_fn = instance
            .get_typed_func::<i32, i32>(&mut store, "alloc")
            .map_err(|e| anyhow::anyhow!("WASM module must export 'alloc(i32)->i32': {e}"))?;

        let free_fn = instance
            .get_typed_func::<(i32, i32), ()>(&mut store, "free")
            .map_err(|e| anyhow::anyhow!("WASM module must export 'free(i32,i32)': {e}"))?;

        let invoke_fn = instance
            .get_typed_func::<(i32, i32), i32>(&mut store, "invoke")
            .map_err(|e| anyhow::anyhow!("WASM module must export 'invoke(i32,i32)->i32': {e}"))?;

        let mut to_free: Vec<(i32, i32)> = Vec::new();

        let envelope_len_i32: i32 = envelope
            .len()
            .try_into()
            .context("envelope too large for i32 length")?;

        // 1) alloc envelope
        let envelope_ptr = alloc_fn
            .call(&mut store, envelope_len_i32)
            .map_err(|e| anyhow::anyhow!("calling alloc for envelope: {e}"))?;
        if envelope_ptr == 0 {
            bail!("alloc returned 0 (OOM) for envelope — map to Failure(RESOURCE_EXCEEDED)");
        }
        to_free.push((envelope_ptr, envelope_len_i32));

        // Run invoke + read in a closure so we always free afterward
        let res = (|| -> Result<Vec<u8>> {
            // 2) write envelope
            memory
                .write(&mut store, envelope_ptr as usize, envelope)
                .map_err(|e| anyhow::anyhow!("writing envelope to WASM memory: {e}"))?;

            // 3) invoke
            let result_ptr = invoke_fn
                .call(&mut store, (envelope_ptr, envelope_len_i32))
                .map_err(|e| anyhow::anyhow!("calling invoke: {e}"))?;
            if result_ptr == 0 {
                bail!("invoke returned 0 result_ptr");
            }

            // 4) read len prefix
            let mut len_buf = [0u8; 4];
            memory
                .read(&store, result_ptr as usize, &mut len_buf)
                .map_err(|e| anyhow::anyhow!("reading result length prefix: {e}"))?;
            let result_len = u32::from_le_bytes(len_buf) as u64;

            if result_len == 0 {
                bail!("invoke returned zero-length result");
            }
            if result_len > max_output_bytes {
                bail!(
                    "result too large: {} bytes > limits.max_output_bytes {}",
                    result_len,
                    max_output_bytes
                );
            }

            // schedule free of result buffer: prefix(4) + payload
            let total = 4u64 + result_len;
            let total_i32: i32 = total
                .try_into()
                .context("result buffer too large for i32 length")?;
            to_free.push((result_ptr, total_i32));

            // 5) read payload
            let mut result_bytes = vec![0u8; result_len as usize];
            memory
                .read(&store, result_ptr as usize + 4, &mut result_bytes)
                .map_err(|e| anyhow::anyhow!("reading result CBOR payload: {e}"))?;

            Ok(result_bytes)
        })();

        // 6) free (best-effort, reverse order for allocator friendliness) — always runs
        for (ptr, len) in to_free.into_iter().rev() {
            let _ = free_fn.call(&mut store, (ptr, len));
        }

        res
    }
}
