# 2026-05-23 — Local Model Infra

**Session goal:** Stand up local model serving via Ollama to replace
Anthropic API calls for the RAO loop build.

## Decisions made

- GPU acceleration abandoned for now — Windows ROCm does not detect the
  RX 6600 XT despite correct community-fork install; deferred to future
  Linux/WSL2 setup
- Accept CPU inference (~15 tok/s) — stable and sufficient for dev/eval;
  not serving live traffic
- phi4-mini pulled as the working local model

## What happened

- Installed ollama-for-amd community fork, swapped gfx1032 rocblas libs —
  install correct but GPU discovery returns 0 devices (logged in
  validation log)
- Discovered the real instability cause: disk was at 532MB free; freed to
  37GB, system now stable
- ollama serve confirmed running, phi4-mini responds on CPU

## Measurements

phi4-mini ~13-15 tok/s CPU; 8.1GB RAM available post-cleanup

- Staged smoke test post-disk-cleanup: retriever loads clean (32s first
  run = weight download, cached after), single Ollama CPU call 4.3s, full
  e2e (ownership_over_20pct) passed in 24.86s with no crash
- CONFIRMED: prior crashes were caused by disk at 532MB free, not pipeline
  load. Pipeline is healthy.

## Next session

Wire Aether's planner/critic to call the local Ollama endpoint instead of
Anthropic API. Then build the RAO loop.
