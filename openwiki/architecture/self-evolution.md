---
type: Architecture
title: Self-Evolution System (Hermes)
description: "GenericAgent's Hermes self-evolution system: post-task skill distillation, the skill_manage drop-in tool, the skill_evolution plugin, provenance tracking, circuit breakers, and the L1-L4 memory architecture."
resource: plugins/skill_evolution.py
tags: [architecture, self-evolution, hermes, skills, skill-manage, plugins]
---

# Self-Evolution System (Hermes)

GenericAgent's self-evolution capability — codenamed **Hermes** — enables the agent to crystallize successful task executions into reusable Skills. Rather than preloading capabilities, the agent grows its skill tree through use.

## Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│  agent_after hook (agent_loop.py line 146)           │
│  Fires on task completion                             │
├──────────────────────────────────────────────────────┤
│  plugins/skill_evolution.py                          │
│  distill(): spawns daemon thread, calls LLM,          │
│  produces skill create/patch via skill_manage         │
├──────────────────────────────────────────────────────┤
│  tools/skill_manage.py (drop-in, registry-track)     │
│  create / patch / retire / list_evolvable             │
│  provenance gating, .prev backup, circuit breakers    │
├──────────────────────────────────────────────────────┤
│  skill_loader.py                                     │
│  sync_skills_to_l1(), catalog, validate, provenance   │
├──────────────────────────────────────────────────────┤
│  .agents/skills/<name>/SKILL.md                      │
│  Skills on disk (agentskills.io format)               │
└──────────────────────────────────────────────────────┘
```

## The `skill_evolution` Plugin

The plugin (`plugins/skill_evolution.py`) implements the **v1 post-task skill distillation loop** — the primary self-evolution trigger.

### Trigger

The `agent_after` hook fires after every task completion in `agent_loop.py`. The plugin gates activation on:

- `current_turn >= SKILL_DISTILL_MIN_TURNS` (default: 6) — skip trivial tasks
- `GA_SKILL_EVOLUTION_ENABLED=1` — must be explicitly opted in

### Distillation Process

When triggered, `distill()` spawns a **daemon thread** that:

1. Snapshots `history_info` and the skill catalog
2. Makes a **one-shot LLM call** (not sharing the live session)
3. Produces 0 or 1 skill create/patch operations
4. Routes through `skill_manage` to write the skill to disk
5. Uses `skill_write_origin='background_review'` (ContextVar) for provenance

The distillation is intentionally simple: one LLM call, one skill operation per run. v1.5 plans richer signal-driven triggers.

### Provenance

A `contextvars.ContextVar` named `skill_write_origin` tracks who initiated a skill write:

| Value | Meaning |
|-------|---------|
| `foreground` | LLM directly called `skill_manage` during a task (user-directed) |
| `background_review` | Plugin autonomously distilled after task completion |

This mirrors the hermes-agent pattern. `do_skill_manage` refuses writes to user-authored or non-evolvable skills regardless of origin.

## The `skill_manage` Tool

`tools/skill_manage.py` is the [drop-in tool](/openwiki/architecture/tool-dispatch.md) that handles skill CRUD. It was migrated from `GenericAgentHandler.do_skill_manage` during the refactoring to demonstrate registry-track extensibility.

Registered as `@register_tool("skill_manage")`, it supports four actions:

| Action | Purpose | Gating |
|--------|---------|--------|
| `create` | Create a new skill with `SKILL.md` and provenance frontmatter | `GA_SKILL_EVOLUTION_ENABLED=1` |
| `patch` | Modify an existing agent-authored, evolvable skill | `GA_SKILL_EVOLUTION_ENABLED=1`; author=agent only |
| `retire` | Mark a skill as retired | `GA_SKILL_EVOLUTION_ENABLED=1`; author=agent only |
| `list_evolvable` | List all agent-authored, evolvable skills | No gating (read-only) |

### Safety Mechanisms

- **Provenance gating**: patch/retire only works on `author=agent` AND `evolvable!=false` skills. User-authored skills are read-only.
- **`.prev` backup**: Every create/patch writes a `.prev` backup before modifying, enabling `revert_skill_prev()`.
- **Circuit breakers (D8)**:
  - `MAX_AUTO_PATCH_PER_SKILL = 5` — a single skill can only be auto-patched 5 times consecutively without human correction; exceeded → Brief-only
  - `MAX_CHANGES_PER_DISTILL = 3` — max skills changed in one distillation
- **Gate**: `GA_SKILL_EVOLUTION_ENABLED=1` required for any write action (list_evolvable/dry_run exempt)

## Design Decisions (D1-D8)

Authoritative decisions live in `hermes/workflows/post-task-skill-distillation.md`. Key architectural choices:

| Decision | Summary |
|----------|---------|
| **D1** | In-place enhancement of `ga.py` (not standalone `hermes.py` initially) |
| **D2** | v1 loop = post-task skill distillation. Other loops (scheduled review, trajectory compression, swarm, Darwinian evolution) deferred to v2/v3 |
| **D3** | Skill = first-class citizen, `.agents/skills/<name>/SKILL.md` (agentskills.io standard). Provenance frontmatter with `author`, `evolvable`, `evolved_from`, `version`, `created_at` |
| **D4** | `ContextVar` provenance (`foreground`/`background_review`). Auto-loop can only mutate `author=agent` + `evolvable!=false` skills |
| **D5** | Event-driven trigger (`agent_after` hook) with complexity gating, not scheduled |
| **D6-D7** | Two-tier validation: L1 mechanism correctness (pytest T1-T10) + L2 effectiveness (negative signal decay + Brief acceptance rate + human demo) |
| **D8** | Circuit breakers: single-skill ≥5 auto-patches → Brief; single-distill ≥3 skill changes → cap |

## L1-L4 Memory System

The memory system stores execution knowledge at four levels:

| Level | Location | Content | Example |
|-------|----------|---------|---------|
| **L1** | `memory/global_mem_insight.txt` | Skill index, pointers | Skill catalog entries |
| **L2** | `memory/global_mem.txt` | Key facts, rules | "Python 3.11/3.12 only" |
| **L3** | `memory/*.md`, `memory/*.py` | Task SOPs, reusable scripts | `github_contribution_sop.md`, `adb_ui.py` |
| **L4** | `memory/L4_raw_sessions/` | Raw session logs | Full conversation transcripts |

Skills (`.agents/skills/`) are the new first-class evolution target. Legacy L3 SOPs (`memory/*_sop.md`) remain but are not indexed by `skill_loader`.

## v1.5 Recommendations

`hermes/NOTES_v1.5_recommendations.md` contains 9 prioritized recommendations (R1-R9) for the next iteration, including:

- Signal-driven triggers (error/dissatisfaction detection)
- Enhanced fitness scoring
- Cross-session skill search (FTS5)
- Darwinian skill evolution loop
- Trajectory compression for training data

## Relevant Tests

| Test File | Coverage |
|-----------|----------|
| `tests/test_skill_evolution.py` | Core evolution plugin logic |
| `tests/test_skill_evolution_plugin.py` | Plugin hook integration, gating |
| `tests/test_skill_manage_registry.py` | `skill_manage` via registry (38 skill tests) |

Run: `pytest tests/test_skill_evolution.py tests/test_skill_evolution_plugin.py tests/test_skill_manage_registry.py`

## Where to Start When Changing This Area

- **Adding a new skill action**: Edit `tools/skill_manage.py`, add a new `action` branch
- **Changing distillation logic**: Edit `plugins/skill_evolution.py`; read `hermes/workflows/post-task-skill-distillation.md` first
- **Changing circuit breaker thresholds**: Constants at top of `plugins/skill_evolution.py` (`MAX_AUTO_PATCH_PER_SKILL`, `MAX_CHANGES_PER_DISTILL`)
- **Adding v1.5 features**: Start with `hermes/NOTES_v1.5_recommendations.md`; coordinate with the existing `do_skill_manage` interface
