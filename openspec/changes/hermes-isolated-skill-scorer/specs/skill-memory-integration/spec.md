## ADDED Requirements

### Requirement: Scoring Gate on Background Skill Write

The background distillation path (`distill()` → `_apply_op()`) SHALL route every generated skill op through the `skill-scoring` capability's scoring gate before writing. An op that the gate rejects SHALL NOT be written to disk and SHALL instead produce a review Brief. This adds a quality gate to the existing `skill_write_origin='background_review'` auto-write path without changing the foreground (`do_skill_manage` invoked directly by the LLM) path.

#### Scenario: Background op passes scoring gate and writes
- **WHEN** `distill()` produces an op with `skill_write_origin='background_review'` and the scoring gate returns `verdict=='pass'` with `score >= threshold`
- **THEN** `_apply_op` writes the SKILL.md with `.prev` backup and validates structurally

#### Scenario: Background op rejected by scoring gate
- **WHEN** `distill()` produces an op with `skill_write_origin='background_review'` and the scoring gate returns a non-pass verdict or `score < threshold`
- **THEN** the system SHALL NOT write the SKILL.md and SHALL enqueue a "candidate failed review" Brief into `handler._pending_briefs`

#### Scenario: Foreground write path bypasses scoring gate
- **WHEN** `do_skill_manage` is invoked directly by the LLM (`skill_write_origin='foreground'`)
- **THEN** the scoring gate SHALL NOT apply; the foreground path retains its existing read-only boundary (`author=user` / `evolvable=false` rejection) and write behavior

### Requirement: Auto-Patch Breaker Reset

The per-skill auto-patch breaker counter (`_auto_patch_counts[name]`) SHALL be resettable, fixing the v1 bug where it was never reset and permanently locked a skill after `MAX_AUTO_PATCH_PER_SKILL` (5) successful auto-patches. The system SHALL expose `reset_auto_patch_count(name)` and call it in two cases: (a) when a `foreground` `do_skill_manage` patch succeeds (human-in-the-loop correction resets the counter); (b) a `background_review` patch that passes the scoring gate (`verdict=='pass'`) and succeeds in `_apply_op` SHALL reset `_auto_patch_counts[name]` to zero via `reset_auto_patch_count(name)` (an independently-verified patch resets the unverified-patch streak, the same treatment as a foreground correction). Patches that fail scoring, are rejected by the gate, or fail `_apply_op` SHALL NOT change the counter.

#### Scenario: Foreground correction resets breaker counter
- **WHEN** `do_skill_manage` is invoked with `skill_write_origin='foreground'` and the patch succeeds
- **THEN** `reset_auto_patch_count(name)` is called, zeroing `_auto_patch_counts[name]`

#### Scenario: Scored-pass background patch resets breaker counter
- **WHEN** a `background_review` patch passes the scoring gate (`verdict=='pass'`) and `_apply_op` succeeds
- **THEN** `reset_auto_patch_count(name)` is called, zeroing `_auto_patch_counts[name]`

#### Scenario: Unscored or rejected background patch behavior unchanged
- **WHEN** `GA_SKILL_SCORER` resolves to no-op (v1 mode) and a `background_review` patch succeeds
- **THEN** `_auto_patch_counts[name]` SHALL increment as in hermes v1 (legacy behavior preserved when scoring disabled)

#### Scenario: Breaker no longer permanently locks
- **WHEN** a skill has accumulated `< MAX_AUTO_PATCH_PER_SKILL` auto-patches and a foreground correction resets the counter
- **THEN** subsequent auto-patches SHALL proceed without breaker-lock until the counter again reaches the cap, regardless of process uptime
