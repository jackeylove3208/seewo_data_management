# Model timeout extension design

## Goal

Allow the configured model gateway up to 120 seconds to complete each structured request while preserving safe Agent worker lease fencing.

## Design

- Change the default `llm_timeout_seconds` from 60 to 120 seconds.
- Change the default `analysis_worker_lease_seconds` from 90 to 150 seconds so the worker lease remains strictly longer than one model request.
- Keep the existing maximum of 10 records per analysis batch and four logical model attempts per subtask unchanged.
- Continue allowing deployment-specific environment variables to override both defaults.

## Verification

- Update the configuration contract test to assert the new defaults.
- Verify that an Agent worker configured with the defaults passes the lease-versus-timeout safety validation.
- Run the focused configuration and model-provider tests, followed by the backend quality gates.
