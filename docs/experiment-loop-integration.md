# Experiment Loop Integration v2

This integration connects `model-optimize-loop` and `write_agent` through
versioned JSON/JSONL files plus a detached loop worker. Long-running experiments
remain under the loop's approval, preflight, watchdog, registry, evaluation,
and rollback policies; the writing agent never executes arbitrary training
commands.

## Files beside the generated paper

| File | Purpose |
|---|---|
| `WRITING_STATE.json` | Durable writing state and pending request IDs |
| `EXPERIMENT_REQUESTS.jsonl` | Append-only requests from planning/review |
| `EXPERIMENT_RESPONSES.jsonl` | Append-only experiment results |
| `paper_plan.json` | Machine-readable paper plan used by `--resume` |
| `PIPELINE_REPORT.json` | Current pipeline status |

## Project profile: one bridge, multiple team methods

The bridge no longer requires method-specific filenames in its source code.
Use a project profile to declare the method repository, runner module,
experiment-contract files, and result layout. A complete LPD-ILT example is
`model-optimize-loop/projects/lpd_ilt.yaml`.

```yaml
project_id: member-method
goal_id: GOAL-MEMBER-METHOD
adapter: config_seed_sweep
runner:
  module: member_method.run_experiment
experiment:
  search_space: member_method/configs/search_space.json
  fixed_protocol: member_method/configs/fixed_protocol.json
results:
  root: runs/agent_experiments
  result_file: result.json
  config_file: generated_config.json
bundle:
  exporter: registry
```

Run or resume the writer with the member's own workspace and a shared profile:

```powershell
python -m write_agent.cli `
  --narrative "NARRATIVE_REPORT.md" `
  --experiment-bundle "D:\path\to\evidence_bundle.json" `
  --auto-experiments `
  --loop-root "D:\path\to\model-optimize-loop" `
  --project-profile "D:\path\to\model-optimize-loop\projects\member_method.yaml" `
  --workspace-root "D:\path\to\member-workspace" `
  --experiment-python "D:\path\to\experiment-env\python.exe"
```

`--workspace-root` is supplied at runtime, so a profile can be shared without
embedding a personal absolute path. The older `--lithobench-root` flag remains
a compatibility alias. If no profile is supplied, the original LPD-ILT paths
and `pw_lpd_ilt.run_experiment` runner are used.

## 1. Export experiment evidence

For the existing LithoBench campaigns, export the historical `result.json`
files directly. This is the correct path for runs created before the generic
loop registry was wired into the campaign scripts:

```powershell
python -m write_agent.orchestrator export-lithobench-bundle `
  --lithobench-root "D:\研究生\计算光刻\比赛\lithobench" `
  --output "D:\研究生\计算光刻\比赛\evidence_bundle.json" `
  --best-run "R18_2k16_seed2028" `
  --baseline-run "R20_BASELINE_2k16_seed2028"
```

The command scans `runs/agent_experiments/*/result.json`, retains compact
official/proxy metrics and artifact provenance, and includes campaign summary
JSON files. It does not modify or rerun an experiment.

For future experiments that are registered in
`model-optimize-loop/registry/experiments.db`, the registry exporter remains
available:

```powershell
python -m write_agent.orchestrator export-bundle `
  --loop-root "D:\研究生\计算光刻\比赛\model-optimize-loop" `
  --output "D:\研究生\计算光刻\比赛\evidence_bundle.json"
```

Do not treat a prose report as an authoritative replacement for missing
structured metrics. A valid illustrative contract is in
`examples/evidence_bundle.example.json`.

## 2. Start the fully automatic loop

```powershell
python -m write_agent.cli `
  --narrative "NARRATIVE_REPORT.md" `
  --experiment-bundle "D:\研究生\计算光刻\比赛\evidence_bundle.json" `
  --output-dir "output" `
  --auto-experiments `
  --loop-root "D:\研究生\计算光刻\比赛\model-optimize-loop" `
  --lithobench-root "D:\研究生\计算光刻\比赛\lithobench" `
  --experiment-python "D:\ANACONDA\envs\lithobench\python.exe"
```

The writer produces a complete initial draft. If planning finds a
`needs_experiment` claim, or review returns `EXPERIMENT_REQUIRED`, it writes a
durable request and immediately submits it to `model-optimize-loop`. The loop:

1. normalizes legacy review fields into seeds, metrics, budget and success criteria;
2. applies `ApprovalGate` and `Preflight`;
3. resolves the matching historical configuration and reuses compatible seeds;
4. launches only missing runs through a detached `Runner` worker;
5. registers metrics/artifacts and appends `EXPERIMENT_RESPONSES.jsonl`;
6. refreshes the Evidence Bundle and invokes `--resume` automatically.

For the current hc24 request, the resolver reuses seeds 2026–2028 and creates
only seed 2029 and 2030 runs.

Job state and logs are under:

```text
model-optimize-loop/state/writing_bridge/jobs/<request-id>/
```

Use the state command for a machine-readable snapshot:

```powershell
python -m orchestration.writing_bridge status `
  --state "state\writing_bridge\jobs\<request-id>\state.json"
```

## 3. Resume an already-paused paper with automation

```powershell
python -m write_agent.cli `
  --resume "D:\path\to\paper" `
  --auto-experiments `
  --loop-root "D:\研究生\计算光刻\比赛\model-optimize-loop" `
  --lithobench-root "D:\研究生\计算光刻\比赛\lithobench" `
  --experiment-python "D:\ANACONDA\envs\lithobench\python.exe"
```

This submits unresolved requests idempotently; rerunning the command does not
start a duplicate job.

## 4. Manual recovery workflow

Automatic execution currently accepts deterministic configuration/seed sweeps
supported by the runner declared in the project profile. The runner must accept
`--spec`, `--search-space`, and `--protocol` and emit the declared result and
resolved-config files. A request requiring a new evaluator,
an unprotected code-editing agent, or an unspecified seed list fails explicitly
and records a failed response instead of guessing. The original manual contract
tools remain available for recovery.

### Convert requests to the loop contract

```powershell
python -m write_agent.orchestrator prepare-requests `
  --paper-dir "output\paper" `
  --output-dir "D:\研究生\计算光刻\比赛\model-optimize-loop\writing_requests" `
  --goal-id "GOAL-ILT-001" `
  --workspace "D:\研究生\计算光刻\比赛\lithobench"
```

Each output file follows the loop's `ExperimentPlan` schema. It intentionally
defaults to `approval_required=true`. The loop agent/operator reviews the plan,
adds concrete changed files/configuration as needed, and executes it through
the existing preflight, runner, evaluator, registry, and decision chain.

### Record the completed experiment

After the loop has registered a completed run:

```powershell
python -m write_agent.orchestrator record-response `
  --paper-dir "output\paper" `
  --loop-root "D:\研究生\计算光刻\比赛\model-optimize-loop" `
  --request-id "WR-EXP-XXXXXXXXXXXX" `
  --experiment-id "WRITE-XXXXXXXXXXXX"
```

This copies exact metrics, decision, provenance, and artifact references into
`EXPERIMENT_RESPONSES.jsonl`; it does not copy large checkpoints.

### Resume writing and review

```powershell
python -m write_agent.cli --resume "output\paper"
```

Resume checks that every pending request has a terminal response. Completed
evidence is injected into a targeted updater, which may revise only existing
affected sections. The zero-context review loop then runs again. A failed or
cancelled experiment pauses for human review instead of silently weakening a
core claim.

## State flow

```text
PLANNING -> WRITING -> EXPERIMENTS_RUNNING
                    -> LOOP_ACCEPTED -> PREFLIGHT_PASSED -> GPU_RUNS
                    -> RESPONSE_RECORDED -> EVIDENCE_INGESTED -> REVIEWING
                    -> EXPERIMENTS_RUNNING (another cycle)
                    -> FINALIZED
```

## Safety and idempotency

- Requests and responses remain append-only and keyed by `request_id`.
- One job directory exists per request; duplicate submission returns its state.
- Existing compatible seeds are reused by exact effective-config signature.
- Missing runs use the project profile's seed-aware search space and fixed
  protocol before real execution.
- Evaluation files and protected code are never modified by the bridge.
