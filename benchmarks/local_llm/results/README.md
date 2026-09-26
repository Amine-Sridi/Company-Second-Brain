# Raw benchmark results

**The authoritative dataset is `sweep_clean.json` + `sweep_granite.json` +
`sweep_last2.json`** (161 rows, **all 7 candidate models** × 23 cases). All three were run
with the final harness on a quiet machine — no model downloads and no other benchmark
competing for the GPU. They are three files only because three models' downloads stalled
and completed at different times; each was measured by the same script, the same cases and
the same schemas. Every number in `LOCAL_LLM_BENCHMARK_AND_FALLBACK.md` §6 and in
`graphs/` comes from these three, except VRAM.

**VRAM comes from `vram_footprint.json`**, produced by `measure_vram.py`. Do not use the
`vram_peak_mb` / `vram_model_mb` fields inside the sweep rows: `unload()` returns before
the driver actually releases memory, so the inline baselines drifted between 2606 MB and
4455 MB and some deltas came out negative. `measure_vram.py` polls the GPU to a settled
idle state before each baseline instead.

## The other files, and why they are kept

| File | What it is |
|---|---|
| `sweep_clean.json` | **Authoritative.** Final harness, quiet machine, 4 models. |
| `sweep_granite.json` | **Authoritative.** `granite4.1:3b`, measured identically once its download finally completed. It won both structured tasks. |
| `sweep_last2.json` | **Authoritative.** `gemma4:e2b-it-qat` and `mistral:7b`, the last two to download. Neither changed a winner; `mistral:7b` is the 7 B over-budget control. |
| `vram_footprint.json` | **Authoritative for VRAM.** Idle-to-loaded delta per model. |
| `summary.json` | Output of `analyze.py`: per-task means and the winner per task. |
| `sweep_20260925T214644Z.json` | The **"before" arm** of the required-keys experiment (§5c). Same 3 models *without* key promotion, where resume extraction capped at 0.60 and the task got no winner at all. Kept as the evidence for that fix. |
| `sweep_20260925T212857Z.json` | Earlier duplicate of the same pre-fix run. |
| `sweep_final_requiredkeys.json` | First run *with* key promotion, but latency is contaminated by a concurrent model download (roughly 3× inflated). Quality figures are valid; latency figures are not. Superseded by `sweep_clean.json`. |
| `sweep_remaining.json` | Partial run (23 rows) from an early attempt at the models that would not download. Superseded by `sweep_clean.json`. |
| `sweep_status_*.json` | Per-model ok / pull_failed / benchmark_failed status for each sweep. |

Nothing here is edited by hand. Regenerate any of it with the commands in
`LOCAL_LLM_BENCHMARK_AND_FALLBACK.md` §13.
