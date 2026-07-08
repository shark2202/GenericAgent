## ADDED Requirements

### Requirement: Multi-Pane Runner View

The TUI SHALL render one pane per runner session, each showing the runner's status (`blocked` / `working` / `done`), and SHALL support split / switch / focus / close-pane (close stops rendering, not the runner).

#### Scenario: Multiple runners shown at a glance

- **WHEN** multiple sessions are running across different runner kinds
- **THEN** the TUI shows one pane per session with its current status visible simultaneously

#### Scenario: Close pane does not kill runner

- **WHEN** a user closes a pane
- **THEN** the runner continues in Core and the pane can be reattached later

### Requirement: Detach and Reattach

The TUI client SHALL be a stateless renderer: disconnecting SHALL NOT affect Core or runners; reconnecting SHALL rebuild pane state from Core's persisted state and live event stream.

#### Scenario: Reattach after close

- **WHEN** a user closes the TUI and later reopens it
- **THEN** all running sessions reappear with their current status and recent events

### Requirement: Keyboard and Mouse

The TUI SHALL support tmux-style prefix keys AND mouse (click to focus, drag to split, scroll), both first-class, with a configurable prefix and a global mouse-off toggle.

#### Scenario: Mouse split

- **WHEN** a user drags a pane border
- **THEN** the layout splits accordingly

#### Scenario: Prefix key navigation

- **WHEN** the user presses the prefix then a pane index
- **THEN** focus moves to that pane

### Requirement: Themes

The TUI SHALL support theme files (colors / styles / status colors) with built-in themes and user themes under a config dir.

#### Scenario: Apply user theme

- **WHEN** a user places a theme file and selects it
- **THEN** the TUI renders with that theme's colors and styles

### Requirement: Tool-Timeline and Approval Rendering

For structured runners the TUI SHALL render tool_call args / result / timing inline; for stream runners it SHALL render output lines and status. The TUI SHALL surface Core-pushed approval prompts in-pane and allow approve / reject with origin.

#### Scenario: Structured tool timeline

- **WHEN** a GA runner emits a tool_call event
- **THEN** the pane shows the tool name, args, result, and timing inline

#### Scenario: Approval prompt in TUI

- **WHEN** Core pushes a pending approval for the focused session
- **THEN** the pane prompts the user to approve or reject, and the decision carries the origin triple

### Requirement: Agent-Agnostic Pane Rendering

The TUI SHALL adapt pane rendering by runner kind: structured-event runners get a tool timeline; stream runners get an output+status view.

#### Scenario: Stream runner pane

- **WHEN** a codex runner is attached
- **THEN** its pane shows output lines and status, not a structured tool timeline
