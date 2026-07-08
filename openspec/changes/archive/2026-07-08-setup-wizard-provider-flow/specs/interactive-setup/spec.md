## MODIFIED Requirements

### Requirement: Provider Auto-Configuration

The system SHALL configure apibase and model based on the user's provider selection, with apibase being overridable and model being discovered dynamically via the provider's `/v1/models` endpoint when available.

#### Scenario: User selects Anthropic

- **WHEN** user selects Anthropic as provider
- **THEN** the wizard SHALL display the default apibase `https://api.anthropic.com` and allow the user to override it by typing a different value or accept the default by pressing Enter

#### Scenario: User selects OpenAI

- **WHEN** user selects OpenAI as provider
- **THEN** the wizard SHALL display the default apibase `https://api.openai.com/v1` and allow the user to override it by typing a different value or accept the default by pressing Enter

#### Scenario: User selects Custom

- **WHEN** user selects Custom provider
- **THEN** the wizard SHALL prompt for apibase manually with no default value

#### Scenario: Dynamic model discovery succeeds

- **WHEN** the user has entered a valid apibase and API key for a provider that implements `GET /v1/models`
- **THEN** the system SHALL call the endpoint using provider-appropriate authentication headers (Anthropic: `x-api-key` + `anthropic-version: 2023-06-01`; OpenAI-compatible: `Authorization: Bearer`) with a 10-second timeout, display the returned model list for selection, and write the selected model `id` to the configuration

#### Scenario: Dynamic model discovery fails

- **WHEN** the `/v1/models` request fails due to timeout, authentication error, network error, or endpoint not implemented
- **THEN** the system SHALL fall back to prompting the user to type a model name manually and SHALL NOT block the setup flow

#### Scenario: Model list display format

- **WHEN** the model discovery endpoint returns a list of models
- **THEN** each entry SHALL be displayed with a human-readable name and its `id` in parentheses, and the selected entry's `id` SHALL be written to the configuration
