## ADDED Requirements

### Requirement: Dual-track dispatch with method priority
The system SHALL dispatch tool calls by first looking up a `do_<name>` method on the handler (method-track), and only when no such method exists SHALL it consult a module-level tool registry (registry-track).

#### Scenario: Method-track takes precedence over registry on name collision
- **WHEN** the handler class defines `do_<name>` AND a tool `<name>` is also registered via `register_tool`
- **THEN** the system SHALL dispatch via the method-track (`do_<name>`) and SHALL NOT invoke the registry entry

#### Scenario: Registry-track fallback when no method exists
- **WHEN** no `do_<name>` method exists on the handler AND `<name>` is registered via `register_tool`
- **THEN** the system SHALL dispatch the registered function and return its `StepOutcome`

#### Scenario: Unknown tool
- **WHEN** neither a `do_<name>` method nor a registry entry exists for `<name>`
- **THEN** the system SHALL yield "未知工具" and return a `StepOutcome` with a "未知工具" next_prompt

### Requirement: Self-registration without handler modification
A tool SHALL be registrable from an independent module via `register_tool(name)` such that the LLM can invoke it without editing the `GenericAgentHandler` class body or the ga.py main file.

#### Scenario: New tool added in an independent module
- **WHEN** a module (not ga.py) defines and registers `register_tool("echo")(fn)` and is loaded, without any edit to `GenericAgentHandler`'s body
- **THEN** the LLM's `echo` tool_call SHALL dispatch successfully and return a `StepOutcome`

#### Scenario: Drop-in auto-discovery with zero edit to ga.py main file
- **WHEN** a new tool is added solely by dropping a module file into the `tools/` directory (auto-discovered and imported at startup, self-registering via `register_tool`), with no edit to the `GenericAgentHandler` class body AND no edit to the ga.py main file
- **THEN** the tool SHALL be loaded and invokable by the LLM, satisfying the extensibility goal without modifying existing handler or ga.py source

### Requirement: Backward compatibility of existing do_* methods
Existing `do_*` methods (e.g., `do_code_run`, `do_file_read`, `do_skill_manage`) SHALL continue to be dispatched via the method-track with zero behavior change.

#### Scenario: Existing tool still dispatches via method-track
- **WHEN** the LLM calls `code_run` (an existing `do_*` method)
- **THEN** the system SHALL dispatch `do_code_run` exactly as before the registry introduction, with no change to `StepOutcome` shape or side effects

### Requirement: Arg injection parity across tracks
The registry-track dispatch SHALL receive the same `_index` and `_tool_num` argument injection that the method-track receives.

#### Scenario: Registry function receives injected args
- **WHEN** a registered tool function is dispatched at `index=i, tool_num=n`
- **THEN** its `args` dict SHALL contain `_index=i` and `_tool_num=n`, identical to a method-track `do_*` at the same position

### Requirement: Registry function signature
A tool function registered via `register_tool(name)` SHALL have the signature `(handler, args, response) -> StepOutcome`, mirroring the existing `do_*(self, args, response)` method signature (with `handler` replacing `self`).

#### Scenario: Registered function accesses handler state
- **WHEN** a registered tool function needs handler instance state (e.g., `handler.cwd`, `handler._pending_briefs`)
- **THEN** it SHALL access them via the `handler` parameter, without being a method on the handler class
