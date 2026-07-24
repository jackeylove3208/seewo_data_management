import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceMembershipError(PermissionError):
    pass


class IdentityKeyHitV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key_kind: Literal["number", "phone", "email"]
    authority_ref: str = Field(min_length=1, max_length=256)


class PairedRecordEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_ref: str = Field(min_length=1, max_length=256)
    work_item_id: str = Field(min_length=1, max_length=128)
    persisted_kind: str = Field(min_length=1, max_length=64)
    entity_kind: Literal["department", "student", "teacher"]
    target_record: dict[str, Any] | None
    authority_record: dict[str, Any] | None
    identity_key_hits: tuple[IdentityKeyHitV1, ...] = ()
    candidate_conflicts: tuple[str, ...] = ()
    authority_claim: str | None = Field(default=None, max_length=256)
    target_stable_order: int | None = Field(default=None, ge=0)
    field_differences: tuple[str, ...] = ()
    allowed_candidates: tuple[str, ...] = ()
    allowed_operations: tuple[
        Literal["create", "update", "delete", "retain", "skip"], ...
    ] = ()
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bounded_membership(self) -> "PairedRecordEvidenceV1":
        for field_name in (
            "candidate_conflicts",
            "field_differences",
            "allowed_candidates",
            "allowed_operations",
            "evidence_refs",
        ):
            values = getattr(self, field_name)
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must contain unique members")
        if self.evidence_ref not in self.evidence_refs:
            raise ValueError("paired evidence must include its own evidence_ref")
        candidate_refs = set(self.allowed_candidates)
        if self.authority_claim is not None and self.authority_claim not in candidate_refs:
            raise ValueError("authority claim must belong to allowed candidates")
        if not set(self.candidate_conflicts).issubset(candidate_refs):
            raise ValueError("candidate conflicts must belong to allowed candidates")
        return self


class EvidenceManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_id: UUID
    tenant_ref: str = Field(min_length=1, max_length=256)
    task_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    graph_node: str = Field(min_length=1, max_length=128)
    action_id: str = Field(min_length=1, max_length=128)
    snapshot_pair: tuple[str, str] | None = None
    target_version: str | None = Field(default=None, max_length=256)
    resource_ids: tuple[str, ...] = ()
    allowed_evidence_refs: tuple[str, ...] = ()
    issued_sensitive_tokens: tuple[str, ...] = ()
    created_at: datetime
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_membership_and_hash(self) -> "EvidenceManifestV1":
        for field_name in (
            "resource_ids",
            "allowed_evidence_refs",
            "issued_sensitive_tokens",
        ):
            values = getattr(self, field_name)
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must contain unique members")
        if self.content_hash != _manifest_hash(_manifest_content(self)):
            raise ValueError("evidence manifest content_hash does not match content")
        return self


def build_evidence_manifest(
    *,
    tenant_ref: str,
    task_id: str,
    run_id: str,
    graph_node: str,
    action_id: str,
    snapshot_pair: tuple[str, str] | None = None,
    target_version: str | None = None,
    resource_ids: tuple[str, ...] = (),
    allowed_evidence_refs: tuple[str, ...] = (),
    issued_sensitive_tokens: tuple[str, ...] = (),
    manifest_id: UUID | None = None,
    created_at: datetime | None = None,
) -> EvidenceManifestV1:
    payload = {
        "manifest_id": manifest_id or uuid4(),
        "tenant_ref": tenant_ref,
        "task_id": task_id,
        "run_id": run_id,
        "graph_node": graph_node,
        "action_id": action_id,
        "snapshot_pair": snapshot_pair,
        "target_version": target_version,
        "resource_ids": resource_ids,
        "allowed_evidence_refs": allowed_evidence_refs,
        "issued_sensitive_tokens": issued_sensitive_tokens,
        "created_at": created_at or datetime.now(UTC),
    }
    return EvidenceManifestV1.model_validate(
        {**payload, "content_hash": _manifest_hash(_manifest_content(payload))}
    )


def require_manifest_resource(manifest: EvidenceManifestV1, resource_id: str) -> None:
    if resource_id not in manifest.resource_ids:
        raise EvidenceMembershipError("resource is outside evidence manifest")


def require_manifest_evidence(
    manifest: EvidenceManifestV1,
    evidence_ref: str,
) -> None:
    if evidence_ref not in manifest.allowed_evidence_refs:
        raise EvidenceMembershipError("evidence is outside evidence manifest")


def require_manifest_token(manifest: EvidenceManifestV1, token: str) -> None:
    if token not in manifest.issued_sensitive_tokens:
        raise EvidenceMembershipError("token is outside evidence manifest")


def opaque_tenant_ref(*, secret: str, tenant_id: str) -> str:
    if len(secret) < 16:
        raise ValueError("tenant reference secret must contain at least 16 characters")
    if not tenant_id:
        raise ValueError("tenant_id is required")
    digest = hmac.new(
        secret.encode("utf-8"),
        tenant_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"tenant-ref:{digest[:24]}"


def _manifest_content(
    value: EvidenceManifestV1 | dict[str, object],
) -> dict[str, object]:
    def get(name: str) -> object:
        return getattr(value, name) if isinstance(value, EvidenceManifestV1) else value[name]

    created_at = get("created_at")
    if not isinstance(created_at, datetime):
        raise ValueError("evidence manifest created_at must be a datetime")
    normalized_created_at = created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "manifest_id": str(get("manifest_id")),
        "tenant_ref": get("tenant_ref"),
        "task_id": get("task_id"),
        "run_id": get("run_id"),
        "graph_node": get("graph_node"),
        "action_id": get("action_id"),
        "snapshot_pair": get("snapshot_pair"),
        "target_version": get("target_version"),
        "resource_ids": get("resource_ids"),
        "allowed_evidence_refs": get("allowed_evidence_refs"),
        "issued_sensitive_tokens": get("issued_sensitive_tokens"),
        "created_at": normalized_created_at,
    }


def _manifest_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
