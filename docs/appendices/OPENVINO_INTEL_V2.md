# Future work only — OpenVINO / Intel NPU inference (v2)

**NOT authorized for v1 implementation.** V1 requires a provider-neutral inference port with typed request/stream/cancel/usage/health/capability behavior, and an Ollama implementation. The Go client and core orchestration must not know about GPU APIs.

## Candidate hardware and runtime
Intel Core Ultra 200V processors may include Arc 130V/140V GPU and Intel NPU. Presence does not prove usable drivers. Intel Arc has shared system memory; available model memory and throughput depend on host configuration, drivers, quantization, bandwidth and runtime. Typical Ollama acceleration may employ supported CPU/GPU paths; Intel NPU support is **not assumed**. V2 should evaluate OpenVINO GenAI as a separately approved provider on Linux, including supported model families and official driver/runtime compatibility, independently of Ollama.

## Proposed provider architecture
- Implement an `OpenVINOInferenceProvider` using the same `InferenceProvider` contract created by T13. Adapter implements validated model resolution, request/stream, cooperative/best-effort cancellation, typed errors, usage optionality, health and capability discovery; no provider-specific types leak into application services, public API, database or Go client.
- Keep deployment config and model registry provider specific behind the port. No runtime plugin loader is required. Select provider at startup; no automatic GPU/NPU switching during runs.
- Normalize streamed text into ordered deltas and terminal semantics; test pause/disconnect/resnapshot independently of provider implementation. Do not assume identical tokenizer/token accounting across models.
- Keep Mem0/`MemoryService` independent: provider change must not silently change Qwen3-Embedding-4B vector space. If embedding engine changes, verify numerical equivalence for model revision, tokenizer, input prefix/normalization, vector size and index retrieval; otherwise rebuild physical collections, validate and coordinate alias cutover.

## Hardware discovery and execution proof
1. Identify exact Intel CPU, GPU/NPU PCI/device IDs, driver/kernel and OpenVINO runtime versions; store no unnecessarily identifying host metadata.
2. Enumerate runtime-exposed supported devices, not just enumerated silicon.
3. Verify model load and correctness on each proposed backend, with GPU/NPU device utilization evidence.
4. Benchmark quantized candidate models: time to first token, tokens/s, peak host/shared memory, throughput under Mem0 embedding/consolidation contention, thermal throttling and energy where available; compare against Ollama CPU/GPU fallback.
5. Document GPU/NPU device permissions and rootless/container implications; macOS support must not be inferred from Linux driver behavior.
6. Explicit operator selection/fallback after validation; no silent change to backend model identity.

## Acceptance criteria for future design review
Exact compatibility matrix pinned; model conversion/quantization reproducible; stream/cancel error mapping conformance; stable output/no invalid UTF-8; privacy/local-only guarantee; bounded resource use; Linux native host and container device tests; clean operator rollback to Ollama; memory-index unchanged or independently reindexed with proof. Add a separate v2 spike/PR before implementation. No v1 scope or CI dependency on OpenVINO.
