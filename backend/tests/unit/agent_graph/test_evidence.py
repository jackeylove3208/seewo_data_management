import pytest

from app.agent_graph.evidence import (
    EvidenceMembershipError,
    build_evidence_manifest,
    require_manifest_evidence,
    require_manifest_resource,
    require_manifest_token,
)


def _manifest():
    return build_evidence_manifest(
        tenant_ref="tenant-ref:demo",
        task_id="task:1",
        run_id="run:1",
        graph_node="analyze_actionable_batches",
        action_id="analyze_students:batch-1",
        snapshot_pair=("snapshot:authority", "snapshot:target"),
        target_version="sha256:target-version",
        resource_ids=("work-item:1", "work-item:2"),
        allowed_evidence_refs=("paired-record:1", "paired-record:2"),
        issued_sensitive_tokens=("phone-token:1",),
    )


def test_manifest_hash_is_stable_for_the_same_bounded_membership() -> None:
    first = _manifest()
    second = build_evidence_manifest(
        manifest_id=first.manifest_id,
        tenant_ref=first.tenant_ref,
        task_id=first.task_id,
        run_id=first.run_id,
        graph_node=first.graph_node,
        action_id=first.action_id,
        snapshot_pair=first.snapshot_pair,
        target_version=first.target_version,
        resource_ids=first.resource_ids,
        allowed_evidence_refs=first.allowed_evidence_refs,
        issued_sensitive_tokens=first.issued_sensitive_tokens,
        created_at=first.created_at,
    )

    assert first.content_hash == second.content_hash
    assert first.content_hash.startswith("sha256:")


def test_manifest_rejects_duplicate_members() -> None:
    with pytest.raises(ValueError, match="resource_ids"):
        build_evidence_manifest(
            tenant_ref="tenant-ref:demo",
            task_id="task:1",
            run_id="run:1",
            graph_node="inspect_sources",
            action_id="inspect",
            resource_ids=("source:1", "source:1"),
        )


def test_manifest_membership_checks_fail_closed() -> None:
    manifest = _manifest()

    require_manifest_resource(manifest, "work-item:1")
    require_manifest_evidence(manifest, "paired-record:1")
    require_manifest_token(manifest, "phone-token:1")

    with pytest.raises(EvidenceMembershipError, match="resource"):
        require_manifest_resource(manifest, "work-item:foreign")
    with pytest.raises(EvidenceMembershipError, match="evidence"):
        require_manifest_evidence(manifest, "paired-record:foreign")
    with pytest.raises(EvidenceMembershipError, match="token"):
        require_manifest_token(manifest, "phone-token:foreign")

