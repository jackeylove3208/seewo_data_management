# Third-data LLM source design

## Goal

Expose `third_data.third_data` as a server-configured, read-only authoritative MySQL source so the Agent can test LLM-derived source mapping against the LLM-derived `seewo-data-mysql` target.

## Access boundary

- Reuse the existing `authority_reader` MySQL account.
- Grant only `SELECT` on `third_data.third_data`; do not grant database-wide access or any mutation privilege.
- Add a separate server-side credential reference whose DSN selects the `third_data` database. The credential value remains in ignored `backend/.env` and reuses the existing `authority_reader` secret.
- Never expose the DSN or password in YAML, logs, model input, or committed documentation.

## Connector configuration

Add `third-data-mysql` to `backend/config/database-connectors.yaml` with:

- role `authoritative`;
- database `third_data` and table `third_data`;
- primary key `row_id` and version column `version`;
- mapping mode `llm` and no predefined canonical field mapping;
- read and paginated-read capabilities only.

The model may inspect the bounded schema and map the organization fields, while the capability gate prevents create, update, and delete operations through this source connector.

## Verification

Verify all of the following without printing credentials or row contents:

1. `authority_reader` can select from `third_data.third_data`.
2. `authority_reader` cannot insert into the table; run the check inside a transaction and roll it back defensively.
3. Application settings load `third-data-mysql` as an authoritative LLM-mapped connector with read-only capabilities.
4. Connector schema discovery returns the expected column names and a paginated read succeeds.
5. Existing focused configuration and connector tests continue to pass.

## Rollback

Remove the `third-data-mysql` YAML entry and its ignored credential entry, then revoke `SELECT` on `third_data.third_data` from `authority_reader`. No table data or schema changes are required.
