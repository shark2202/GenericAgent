## ADDED Requirements

### Requirement: Quick Setup Mode

The system SHALL provide a `ga setup` CLI command that interactively asks the user for the minimum required information to generate a working `mykey.jsonc`.

#### Scenario: New user runs ga setup
- **WHEN** user runs `ga setup` with no existing `mykey.jsonc`
- **THEN** the wizard asks for API key, provider selection (Anthropic/OpenAI/Custom), and automatically derives apibase and model to generate a working `mykey.jsonc`

#### Scenario: Quick mode generates minimal config
- **WHEN** user completes the quick mode questions
- **THEN** a `mykey.jsonc` is created containing at minimum: apikey, apibase, model, and a native session type appropriate for the chosen provider

### Requirement: Provider Auto-Configuration

The system SHALL automatically set apibase and model based on the user's provider selection.

#### Scenario: User selects Anthropic
- **WHEN** user selects Anthropic as provider
- **THEN** apibase SHALL be set to `https://api.anthropic.com` and model SHALL default to `claude-opus-4-7`

#### Scenario: User selects OpenAI
- **WHEN** user selects OpenAI as provider
- **THEN** apibase SHALL be set to `https://api.openai.com/v1` and model SHALL default to `gpt-5.5`

#### Scenario: User selects Custom
- **WHEN** user selects Custom provider
- **THEN** the wizard SHALL prompt for apibase and model manually

### Requirement: Advanced Setup Mode

After quick mode completes, the system SHALL offer to enter advanced mode for optional parameters.

#### Scenario: User declines advanced mode
- **WHEN** user answers "no" to advanced mode prompt
- **THEN** the wizard exits and `mykey.jsonc` is saved with quick mode configuration only

#### Scenario: User accepts advanced mode
- **WHEN** user answers "yes" to advanced mode prompt
- **THEN** the wizard prompts for proxy, connect_timeout, read_timeout, max_tokens, temperature, and context_win, each with sensible defaults

### Requirement: Existing File Detection

The system SHALL detect when `mykey.jsonc` already exists and offer resolution options.

#### Scenario: mykey.jsonc exists
- **WHEN** `mykey.jsonc` already exists in the project root
- **THEN** the wizard SHALL prompt the user to choose: overwrite, exit, or append a new configuration block

#### Scenario: User chooses to overwrite
- **WHEN** user selects "overwrite"
- **THEN** the existing `mykey.jsonc` is replaced with the new configuration

#### Scenario: User chooses to append
- **WHEN** user selects "append"
- **THEN** the new configuration block is appended to the end of the existing `mykey.jsonc`

### Requirement: CLI Integration

The `ga setup` command SHALL be registered in the existing `ga_cli/cli.py` command system.

#### Scenario: ga setup is recognized
- **WHEN** user runs `ga setup` or `python -m ga_cli setup`
- **THEN** the interactive setup wizard launches

#### Scenario: ga setup shows in help
- **WHEN** user runs `ga --help`
- **THEN** `setup` appears in the command list with a description
