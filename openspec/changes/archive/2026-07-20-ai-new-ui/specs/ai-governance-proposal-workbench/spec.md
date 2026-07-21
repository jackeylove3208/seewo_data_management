## ADDED Requirements

### Requirement: Use real reconciliation data in the Web workbench
The Web application SHALL retrieve task stages, difference pages, analysis results, and proposal state from typed backend APIs for non-demo tasks.

#### Scenario: User opens a real task
- **WHEN** the task was created from uploaded CSV files
- **THEN** the task detail and difference views do not substitute local demo differences or browser-only stage state

### Requirement: Display the four-stage workflow
The task detail view SHALL display data ingestion, entity resolution, difference detection, and mandatory AI analysis with current status, progress, errors, and permitted retry actions.

#### Scenario: AI analysis is active
- **WHEN** the backend reports an incomplete analysis batch
- **THEN** the AI stage displays a stable-size analysis animation and completed-versus-total progress without shifting surrounding layout

#### Scenario: Reduced motion is preferred
- **WHEN** the operating system requests reduced motion
- **THEN** the UI replaces continuous animation with a static active indicator while retaining textual progress

### Requirement: Open one difference analysis modal on demand
The workbench SHALL open an analysis modal only when the user selects one difference and SHALL NOT automatically open a sequence of modals after batch analysis.

#### Scenario: Selected difference is still analyzing
- **WHEN** the user opens a difference whose analysis is pending
- **THEN** the modal displays the analysis animation and refreshes in place until the result or failure is available

#### Scenario: Selected difference is analyzed
- **WHEN** analysis-v2 is available
- **THEN** the modal displays source and Seewo values, cause, evidence, risk, confidence, provenance label, and every validated option

### Requirement: Present validated AI options
The analysis modal SHALL present at most three validated options, identify the recommended option, explain rationale and preconditions, and provide an explicit adopt-and-preview command for each option.

#### Scenario: User adopts an AI option
- **WHEN** the user chooses adopt-and-preview
- **THEN** the UI shows exact before and after values and only then submits the analysis ID, option ID, and expected difference version to create a pending execution proposal

### Requirement: Support manual-only analysis
The modal SHALL show only the manual path when analysis is manual-only and SHALL explain why AI did not produce an executable option.

#### Scenario: Analysis has insufficient evidence
- **WHEN** analysis-v2 reports manual-only because information is missing or risk is high
- **THEN** no AI adoption button is rendered and the manual modification action remains available

### Requirement: Allow whitelisted manual entity changes
The workbench SHALL provide a manual editor generated from backend-owned entity field policy and SHALL keep identifiers, source provenance, snapshots, and audit fields read-only.

#### Scenario: User edits a teacher
- **WHEN** the user opens manual modification for a teacher difference
- **THEN** only allowed canonical fields such as name, phone, email, status, or organization relation are editable

#### Scenario: User edits an organization unit
- **WHEN** the user opens manual modification for an organization difference
- **THEN** only allowed canonical fields such as name, status, and parent relation are editable

### Requirement: Require manual rationale and preview
The system SHALL require a non-blank operator rationale and an explicit before-and-after preview before accepting a manual proposal.

#### Scenario: Manual form contains no meaningful change
- **WHEN** the user submits unchanged values, protected fields, or a blank rationale
- **THEN** both frontend and backend reject the request with field-specific feedback

### Requirement: Persist both proposal sources through one contract
The backend SHALL persist AI-selected and operator-authored proposals with the same pending-execution lifecycle, difference version binding, backend-owned operator identity, and supersession history.

#### Scenario: AI option is persisted
- **WHEN** a valid analysis option is confirmed
- **THEN** the backend creates an immutable proposal with `proposal_source=ai` and copies content from the persisted analysis rather than trusting client-supplied changes

#### Scenario: Manual change is persisted
- **WHEN** an allowed manual preview is confirmed
- **THEN** the backend creates an immutable proposal with `proposal_source=operator` and does not modify the current target snapshot or CSV

#### Scenario: User replaces an existing proposal
- **WHEN** the user chooses another AI option or submits a revised manual change for the same difference version
- **THEN** the backend creates a new proposal version linked to the superseded proposal and retains the earlier audit record

### Requirement: Detect stale difference and target values
The proposal APIs SHALL reject stale difference versions or before values and SHALL require the user to reload current evidence before creating a new proposal.

#### Scenario: Target snapshot changed after modal opened
- **WHEN** the user confirms a proposal whose expected difference version or before value no longer matches current data
- **THEN** the backend returns a conflict and the UI closes confirmation, reloads evidence, and explains that the preview is stale

### Requirement: Stop before governance execution
The workbench SHALL label a saved proposal as pending governance execution and SHALL NOT offer a control that directly mutates the Seewo CSV or target API in this change.

#### Scenario: Proposal is saved successfully
- **WHEN** either an AI or manual proposal is created
- **THEN** the difference row shows pending governance execution and the target source remains unchanged
