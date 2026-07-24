# AI model configuration hardening design

## Goal

Make local AI analysis configuration load consistently, use the verified DeepSeek
Chat Completions contract, and prevent model credentials from entering Git or error
tracebacks.

## Scope

- Resolve the default environment file from the backend package location instead of
  the process working directory.
- Configure the local ignored `backend/.env` with the verified DeepSeek endpoint,
  `deepseek-v4-flash`, and `json_object` response mode.
- Add a generated local proposal-preview signing secret and restrict `backend/.env`
  permissions to the current user.
- Replace the committed API key in `backend/.env.example` with a placeholder and keep
  example values aligned with the supported request contract.
- Keep API keys wrapped as `SecretStr` across provider calls so exceptions and pytest
  tracebacks show a masked representation.

## Configuration flow

`Settings` uses an absolute default path pointing to `backend/.env`. Explicit process
environment variables retain Pydantic's normal precedence and can override the file
for deployments. API and worker processes therefore resolve the same settings whether
started from the repository root or `backend/`.

The HTTP provider sends requests to the complete configured endpoint. For the current
DeepSeek account the verified combination is:

```dotenv
RECONCILIATION_LLM_URL=https://api.deepseek.com/chat/completions
RECONCILIATION_LLM_MODEL=deepseek-v4-flash
RECONCILIATION_LLM_RESPONSE_MODE=json_object
```

The provider continues to parse JSON response content into the existing structured
domain schema. Application-level Pydantic validation remains the authoritative output
gate when gateway-level JSON Schema enforcement is unavailable.

## Security behavior

Real credentials remain only in ignored `backend/.env`. The checked-in example contains
placeholders, not usable tokens. Raw API-key strings are created only while constructing
the outbound authorization header and are not passed as exception-visible function
arguments. Local file mode is `0600`.

The previously committed example credential must be treated as compromised and rotated
outside the repository; removing it from the current file does not erase Git history.

## Verification

- A unit test proves the default env-file path is absolute and points to `backend/.env`.
- A unit test proves `backend/.env.example` contains no key-shaped committed secret.
- A provider test inspects an error traceback and proves the raw API key is absent.
- Existing provider and configuration tests remain green.
- The opt-in real gateway smoke test passes with the local ignored configuration.
- Loading `Settings` from both repository root and `backend/` yields the same model
  endpoint, model, response mode, and configured state without printing secrets.

## Non-goals

- Changing the analysis prompt, governance policy, worker scheduling, or model choice.
- Committing local credentials or generated signing secrets.
- Rewriting Git history automatically; credential rotation is an explicit external
  follow-up because it affects the credential owner.
