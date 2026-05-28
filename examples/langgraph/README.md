<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# `examples/langgraph/` -- LangGraph adapter examples

This directory shows how to drop an [NCP] graph into a
[LangGraph] workflow using
[`ncp-langgraph`](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/python/ncp-langgraph),
the Python adapter that wraps `ncp-mcp-server` as a LangGraph node.

**One NCP graph = one LangGraph node.**
`NCPNode.from_subprocess(...)` returns a callable instance; register
it via `builder.add_node(name, instance)`, compile your `StateGraph`,
and invoke.

[NCP]: https://github.com/madeinplutofabio/neural-computation-protocol
[LangGraph]: https://github.com/langchain-ai/langgraph

---

## Contents

| File | Purpose |
|---|---|
| `README.md` | This file. |
| `lead_qualification_agent.py` | End-to-end runnable example: LangGraph workflow shape for lead qualification, currently stubbed on the bundled `echo-pipeline` NCP graph. |
| `requirements.txt` | Python dependencies required to run the example (LangGraph; `ncp-langgraph` is installed separately, see below). |

---

## Current stub: `echo-pipeline`

The example **wires the bundled `echo-pipeline` NCP graph** as the
underlying graph. Echo is a passthrough; it mirrors its input back,
so the `qualification` output you'll see is **NOT** a real
qualification verdict.

The intended graph is `examples/graphs/lead-qualification/`, tracked as
issue [#29](https://github.com/madeinplutofabio/neural-computation-protocol/issues/29).
Once that graph lands, swap the `graph=` path in the example and the
output becomes a real qualification result.

This substitution decouples Phase 3A.3 (the adapter) from issue #29
(the graph) so the adapter ships without blocking on graph-authoring
work.

---

## Quick start

### 1. Install the runtime

```bash
cargo install ncp-mcp-server --version 0.1.0 --locked
```

The version is pinned to keep this example reproducible against
`ncp-langgraph v0.1.x`. That command puts `ncp-mcp-server` on `PATH`
(via `$CARGO_HOME/bin`). It's the Rust binary that `ncp-langgraph`
spawns as a subprocess.

### 2. Install the Python adapter

Until `ncp-langgraph` v0.1.0 is published to PyPI in Phase 3A.3 PR F,
install editable from a workspace clone:

```bash
python -m pip install -e python/ncp-langgraph
```

After publish, the same package will be installable from PyPI directly:

```bash
python -m pip install ncp-langgraph
```

### 3. Install example dependencies (LangGraph)

```bash
python -m pip install -r examples/langgraph/requirements.txt
```

### 4. Run the example

```bash
python examples/langgraph/lead_qualification_agent.py
```

Expected output: the final LangGraph state dict with
- `company_url` and `target_icp` (your initial state)
- `qualification` (echoed input, the stub behavior)
- `ncp_trace` (`result_type`, `trace_id`, `trace_path`)

---

## How the example works

```python
qualify_lead = NCPNode.from_subprocess(
    graph="examples/graphs/echo-pipeline/graph.yaml",  # TODO(#29)
    brick_dir="examples/bricks",
    output_key="qualification",
    timeout=30.0,
)

builder = StateGraph(LeadState)
builder.add_node("qualify_lead", qualify_lead)
builder.add_edge(START, "qualify_lead")
builder.add_edge("qualify_lead", END)
compiled = builder.compile()

result = compiled.invoke({
    "company_url": "https://example.com",
    "target_icp": "B2B SaaS selling to creators",
})
# result["qualification"]  -- the NCP graph's output_json
# result["ncp_trace"]      -- {"result_type", "trace_id", "trace_path"}
```

`NCPNode.__call__` does NOT mutate `state`; it returns a NEW partial
state update which LangGraph merges according to your `StateGraph`'s
schema + reducers.

---

## Full design reference

[`docs/LANGGRAPH_ADAPTER.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md)
is the binding design contract for `ncp-langgraph`, including the
locked `NCPNode` signature, state input/output semantics, exception
model, and v0.1.0 limitations.
