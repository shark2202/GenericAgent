# skill-scoring Specification

## Purpose
TBD - created by archiving change hermes-isolated-skill-scorer. Update Purpose after archive.
## Requirements
### Requirement: Skill Candidate Scoring Gate

The system SHALL insert a scoring gate between candidate skill op generation and skill file write. A `Scorer` abstraction SHALL provide `score(op, skill_md, history, catalog)` returning a `Verdict` with `score` (0-100), `verdict` (`pass`/`reject`/`revise`), per-dimension scores, and `rationale`. The gate SHALL pass the op to `_apply_op` only when `verdict == 'pass'` AND `score >= GA_SKILL_SCORER_THRESHOLD` (default 60); otherwise it SHALL enqueue a review Brief marked "candidate failed review" into `handler._pending_briefs` and SHALL NOT write the skill file.

#### Scenario: Scoring pass writes skill
- **WHEN** `distill()` generates a candidate op and the scorer returns `verdict=='pass'` with `score >= threshold`
- **THEN** the system calls `_apply_op` which writes the SKILL.md (with `.prev` backup) and validates structurally

#### Scenario: Scoring reject blocks write and enqueues Brief
- **WHEN** the scorer returns `verdict` other than `'pass'` OR `score < threshold`
- **THEN** the system SHALL NOT write the skill file, and SHALL append a Brief marked "candidate failed review" to `handler._pending_briefs` (flushed at `turn%10` / task end)

#### Scenario: Scorer disabled reverts to v1 behavior
- **WHEN** `GA_SKILL_EVOLUTION_ENABLED` is not `1` OR `GA_SKILL_SCORER` resolves to a no-op mode
- **THEN** `distill()` SHALL skip the scoring gate and behave as hermes v1 (single LLM call, op applied directly)

### Requirement: Isolated Scorer Subagent

The system SHALL provide a `SubagentScorer` implementation that evaluates a candidate skill op by dispatching an isolated sub-agent via `SubagentManager.run_single()`. The sub-agent SHALL run in its own git worktree (`--detach`) with an independent LLM session, satisfying independence-by-construction: the evaluator process and session are distinct from the generator's.

#### Scenario: Subagent scorer dispatches isolated evaluator
- **WHEN** `GA_SKILL_SCORER=subagent` AND `handler._subagent_mgr` is present
- **THEN** the scorer calls `run_single(desc=..., schema=SCORE_SCHEMA, tools_subset=['file_read'], ...)` spawning an isolated `agentmain.py` subprocess in a detached worktree

#### Scenario: Evaluator description excludes generator reasoning
- **WHEN** the scorer constructs the sub-agent `desc`
- **THEN** the `desc` contains the candidate SKILL.md full text + task history snapshot + existing skill catalog, and SHALL NOT contain the generator's `op['reason']` or any chain-of-thought

#### Scenario: Evaluator tool surface is read-only
- **WHEN** the sub-agent is spawned for scoring
- **THEN** `tools_subset` is restricted to `['file_read']` plus the forced `submit_result`; the system SHALL NOT grant `code_run`, `file_write`, `web_scan`, or `file_patch` to the evaluator

#### Scenario: Sub-agent submits schema-validated score
- **WHEN** the evaluator sub-agent completes
- **THEN** it calls `submit_result` with an object conforming to the score schema (`score`/`verdict`/`dims`/`rationale`), validated by `run_single`'s schema check before the gate consumes it

### Requirement: Scorer Gate Selection and Fallback

The system SHALL select the scorer implementation via `GA_SKILL_SCORER` env (`subagent` | `inprocess`), defaulting to `subagent` when `handler._subagent_mgr` is present and `inprocess` otherwise. The system SHALL provide an `InProcessScorer` (same-process second LLM call with a separate messages list) as the back-compat fallback. When `subagent` is requested but `_subagent_mgr` is absent, the system SHALL degrade to `InProcessScorer` and record one stderr line. When `SubagentScorer` returns `failed`/`timed_out`, `distill()` SHALL fall back to `InProcessScorer` and append a Brief noting the degradation (it SHALL NOT skip scoring and write unconditionally).

#### Scenario: Subagent requested but manager absent degrades to inprocess
- **WHEN** `GA_SKILL_SCORER=subagent` AND `_subagent_mgr` is None
- **THEN** the system uses `InProcessScorer`, writes one stderr deprecation line, and the scoring gate still runs

#### Scenario: Subagent failure falls back to inprocess
- **WHEN** `SubagentScorer.run_single` returns state `failed` or `timed_out`
- **THEN** `distill()` invokes `InProcessScorer.score` as fallback, and appends a Brief noting the degradation; the gate uses the inprocess verdict

#### Scenario: Inprocess scorer is the default without subagent manager
- **WHEN** `GA_SKILL_SCORER` is unset AND `_subagent_mgr` is absent
- **THEN** the system selects `InProcessScorer` without error

