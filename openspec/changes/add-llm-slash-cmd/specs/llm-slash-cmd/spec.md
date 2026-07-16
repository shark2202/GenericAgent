# LLM Slash Command

## ADDED Requirements

### Requirement: List all available models

The system SHALL display all configured LLM models when the user sends `/llm` with no arguments, and SHALL mark the currently active model with ✓.

#### Scenario: User types /llm with no arguments

- Given the agent has at least one LLM model configured
- When the user sends `/llm` with no arguments
- Then the system lists all models with their index numbers
- And the currently active model is marked with ✓
- And a summary line shows the current model name

### Requirement: Switch to a specific model by index

The system SHALL switch the active LLM model when the user sends `/llm <index>`, and SHALL validate the index range before switching.

#### Scenario: User switches to a valid model index

- Given the agent has multiple LLM models configured
- When the user sends `/llm 1`
- Then the system switches the active model to the model at index 1
- And a confirmation message displays the new model name

#### Scenario: User provides an out-of-range index

- Given the agent has N models configured
- When the user sends `/llm` with an index >= N or < 0
- Then the system SHALL display an error message indicating the valid range (0 to N-1)

#### Scenario: User provides a non-numeric argument

- Given the user sends `/llm abc`
- Then the system SHALL display a usage hint: "用法: /llm [索引号]"
