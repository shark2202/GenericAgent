---
type: Documentation Index
title: "Architecture"
description: "Files and subdirectories in Architecture."
---

# Files

- [Core Engine Architecture](overview.md) - The layered architecture of GenericAgent's core engine: agent_loop.py (pure engine loop), agentmain.py (SDK layer), ga.py/ga_utils.py (tool implementations), llmcore.py (LLM communication), mcp_client.py (MCP client), and skill_loader.py (skill discovery).
- [Self-Evolution System (Hermes)](self-evolution.md) - GenericAgent's Hermes self-evolution system: post-task skill distillation, the skill_manage drop-in tool, the skill_evolution plugin, provenance tracking, circuit breakers, and the L1-L4 memory architecture.
- [Tool Dispatch System](tool-dispatch.md) - Dual-track tool dispatch mechanism in agent_loop.py: method-track (do_<name> on handler) with registry-track fallback, plus the drop-in tools/ directory for zero-edit extensibility.
