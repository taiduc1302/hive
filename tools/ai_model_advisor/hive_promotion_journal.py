from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

_CONFIG_KEYS = ("provider", "model_id", "effort", "execution_mode")
_STATE_BY_EVENT = {
    "promotion_preview": "previewed",
    "applied_lifecycle": "applied_verified",
    "rollback_audit": "rolled_back_verified",
}


class HivePromotionJournalError(ValueError):
    """Raised when Hive promotion journal evidence is malformed."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required_string(mapping: dict[str, Any], key: str, *, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HivePromotionJournalError(f"{label} must contain non-empty {key}")
    return value.strip()


def _config(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HivePromotionJournalError(f"{label} must be an object")
    return {
        key: _required_string(value, key, label=label)
        for key in _CONFIG_KEYS
    }


def _preview_metadata(
    preview: dict[str, Any],
) -> tuple[str, str, str, dict[str, dict[str, str]]]:
    if preview.get("schema_version") != 1:
        raise HivePromotionJournalError("promotion preview schema_version must be 1")
    if preview.get("host") != "hive":
        raise HivePromotionJournalError("promotion preview host must be hive")
    if preview.get("state") != "ready_for_manual_hive_edit":
        raise HivePromotionJournalError(
            "promotion preview must be ready_for_manual_hive_edit"
        )
    if preview.get("safe_to_auto_apply") is not False:
        raise HivePromotionJournalError(
            "promotion preview must keep safe_to_auto_apply=false"
        )
    if preview.get("automatic_config_mutation") is not False:
        raise HivePromotionJournalError(
            "promotion preview must keep automatic_config_mutation=false"
        )
    if preview.get("requires_human_approval") is not True:
        raise HivePromotionJournalError(
            "promotion preview must require human approval"
        )

    category = _required_string(preview, "category", label="promotion preview")
    change_id = _required_string(preview, "change_id", label="promotion preview")
    scope = _required_string(preview, "scope", label="promotion preview")
    if scope not in {"queen", "worker", "both"}:
        raise HivePromotionJournalError(
            "promotion preview scope must be queen, worker, or both"
        )

    raw = preview.get("selected_transition")
    if not isinstance(raw, dict):
        raise HivePromotionJournalError(
            "promotion preview must contain selected_transition"
        )
    transition = {
        "before": _config(raw.get("before"), label="selected_transition.before"),
        "after": _config(raw.get("after"), label="selected_transition.after"),
        "rollback_to": _config(
            raw.get("rollback_to"),
            label="selected_transition.rollback_to",
        ),
    }
    if transition["rollback_to"] != transition["before"]:
        raise HivePromotionJournalError(
            "selected_transition.rollback_to must exactly match before"
        )
    return category, change_id, scope, transition


def _entry_sha256(
    *,
    sequence: int,
    event: str,
    state: str,
    artifact_sha256: str,
    previous_entry_sha256: str | None,
    evidence_hashes: dict[str, str | None],
) -> str:
    return _canonical_sha256(
        {
            "sequence": sequence,
            "event": event,
            "state": state,
            "artifact_sha256": artifact_sha256,
            "previous_entry_sha256": previous_entry_sha256,
            "evidence_hashes": evidence_hashes,
        }
    )


def _entry(
    *,
    sequence: int,
    event: str,
    artifact_sha256: str,
    previous_entry_sha256: str | None,
    evidence_hashes: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    if event not in _STATE_BY_EVENT:
        raise HivePromotionJournalError(f"unsupported journal event: {event!r}")
    state = _STATE_BY_EVENT[event]
    hashes = evidence_hashes or {}
    entry_sha = _entry_sha256(
        sequence=sequence,
        event=event,
        state=state,
        artifact_sha256=artifact_sha256,
        previous_entry_sha256=previous_entry_sha256,
        evidence_hashes=hashes,
    )
    return {
        "sequence": sequence,
        "event": event,
        "state": state,
        "artifact_sha256": artifact_sha256,
        "previous_entry_sha256": previous_entry_sha256,
        "evidence_hashes": hashes,
        "entry_sha256": entry_sha,
    }


def _validate_entries(journal: dict[str, Any]) -> list[dict[str, Any]]:
    entries = journal.get("entries")
    if not isinstance(entries, list) or not entries:
        raise HivePromotionJournalError(
            "promotion journal must contain a non-empty entries list"
        )

    previous_sha: str | None = None
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise HivePromotionJournalError(
                f"promotion journal entry {index} must be an object"
            )
        if item.get("sequence") != index:
            raise HivePromotionJournalError(
                f"promotion journal entry {index} has invalid sequence"
            )

        event = _required_string(item, "event", label=f"journal entry {index}")
        if event not in _STATE_BY_EVENT:
            raise HivePromotionJournalError(
                f"promotion journal entry {index} has unsupported event {event!r}"
            )
        expected_state = _STATE_BY_EVENT[event]
        if item.get("state") != expected_state:
            raise HivePromotionJournalError(
                f"promotion journal entry {index} state does not match its event"
            )

        artifact_sha = _required_string(
            item,
            "artifact_sha256",
            label=f"journal entry {index}",
        )
        if item.get("previous_entry_sha256") != previous_sha:
            raise HivePromotionJournalError(
                f"promotion journal entry {index} breaks the hash chain"
            )

        evidence_hashes = item.get("evidence_hashes")
        if not isinstance(evidence_hashes, dict):
            raise HivePromotionJournalError(
                f"promotion journal entry {index} evidence_hashes must be an object"
            )
        normalized_hashes: dict[str, str | None] = {}
        for key, value in evidence_hashes.items():
            if not isinstance(key, str) or not key:
                raise HivePromotionJournalError(
                    f"promotion journal entry {index} has invalid evidence hash key"
                )
            if value is not None and not isinstance(value, str):
                raise HivePromotionJournalError(
                    f"promotion journal entry {index} has invalid evidence hash value"
                )
            normalized_hashes[key] = value

        expected_entry_sha = _entry_sha256(
            sequence=index,
            event=event,
            state=expected_state,
            artifact_sha256=artifact_sha,
            previous_entry_sha256=previous_sha,
            evidence_hashes=normalized_hashes,
        )
        if item.get("entry_sha256") != expected_entry_sha:
            raise HivePromotionJournalError(
                f"promotion journal entry {index} entry_sha256 is invalid"
            )
        previous_sha = expected_entry_sha

    if entries[0].get("event") != "promotion_preview":
        raise HivePromotionJournalError(
            "promotion journal must start with a promotion_preview entry"
        )
    if journal.get("head_entry_sha256") != previous_sha:
        raise HivePromotionJournalError(
            "promotion journal head_entry_sha256 does not match the final entry"
        )
    expected_state = entries[-1]["state"]
    if journal.get("state") != expected_state:
        raise HivePromotionJournalError(
            "promotion journal state does not match its final entry"
        )
    return entries


def validate_hive_promotion_journal(journal: dict[str, Any]) -> None:
    """Validate a promotion journal and its complete entry hash chain."""
    if not isinstance(journal, dict):
        raise HivePromotionJournalError("promotion journal must be a JSON object")
    if journal.get("schema_version") != 1:
        raise HivePromotionJournalError("promotion journal schema_version must be 1")
    if journal.get("host") != "hive":
        raise HivePromotionJournalError("promotion journal host must be hive")
    if journal.get("safe_to_auto_apply") is not False:
        raise HivePromotionJournalError(
            "promotion journal must keep safe_to_auto_apply=false"
        )
    if journal.get("automatic_config_mutation") is not False:
        raise HivePromotionJournalError(
            "promotion journal must keep automatic_config_mutation=false"
        )
    if journal.get("automatic_rollback") is not False:
        raise HivePromotionJournalError(
            "promotion journal must keep automatic_rollback=false"
        )
    if journal.get("requires_human_approval") is not True:
        raise HivePromotionJournalError(
            "promotion journal must require human approval"
        )

    _required_string(journal, "category", label="promotion journal")
    _required_string(journal, "change_id", label="promotion journal")
    scope = _required_string(journal, "scope", label="promotion journal")
    if scope not in {"queen", "worker", "both"}:
        raise HivePromotionJournalError(
            "promotion journal scope must be queen, worker, or both"
        )
    _required_string(
        journal,
        "promotion_preview_sha256",
        label="promotion journal",
    )

    raw_transition = journal.get("transition")
    if not isinstance(raw_transition, dict):
        raise HivePromotionJournalError(
            "promotion journal must contain transition"
        )
    transition = {
        "before": _config(raw_transition.get("before"), label="transition.before"),
        "after": _config(raw_transition.get("after"), label="transition.after"),
        "rollback_to": _config(
            raw_transition.get("rollback_to"),
            label="transition.rollback_to",
        ),
    }
    if transition["rollback_to"] != transition["before"]:
        raise HivePromotionJournalError(
            "promotion journal rollback_to must exactly match before"
        )

    entries = _validate_entries(journal)
    if entries[0]["artifact_sha256"] != journal["promotion_preview_sha256"]:
        raise HivePromotionJournalError(
            "promotion journal genesis entry does not match promotion_preview_sha256"
        )

    event_order = [item["event"] for item in entries]
    allowed_orders = [
        ["promotion_preview"],
        ["promotion_preview", "applied_lifecycle"],
        ["promotion_preview", "applied_lifecycle", "rollback_audit"],
    ]
    if event_order not in allowed_orders:
        raise HivePromotionJournalError(
            "promotion journal contains an invalid event transition sequence"
        )


def build_hive_promotion_journal(
    preview: dict[str, Any],
) -> dict[str, Any]:
    """Create the genesis promotion journal entry from a reviewed Hive preview."""
    if not isinstance(preview, dict):
        raise HivePromotionJournalError("promotion preview must be a JSON object")
    category, change_id, scope, transition = _preview_metadata(preview)
    preview_sha = _canonical_sha256(preview)
    genesis = _entry(
        sequence=0,
        event="promotion_preview",
        artifact_sha256=preview_sha,
        previous_entry_sha256=None,
        evidence_hashes={
            "promotion_review_sha256": (
                preview.get("promotion_review_sha256")
                if isinstance(preview.get("promotion_review_sha256"), str)
                else None
            )
        },
    )
    return {
        "schema_version": 1,
        "host": "hive",
        "state": "previewed",
        "safe_to_auto_apply": False,
        "automatic_config_mutation": False,
        "automatic_rollback": False,
        "requires_human_approval": True,
        "category": category,
        "change_id": change_id,
        "scope": scope,
        "transition": transition,
        "promotion_preview_sha256": preview_sha,
        "entries": [genesis],
        "head_entry_sha256": genesis["entry_sha256"],
    }


def _validate_event_common(
    journal: dict[str, Any],
    artifact: dict[str, Any],
) -> dict[str, Any]:
    if artifact.get("schema_version") != 1:
        raise HivePromotionJournalError("event artifact schema_version must be 1")
    if artifact.get("host") != "hive":
        raise HivePromotionJournalError("event artifact host must be hive")
    if artifact.get("category") != journal["category"]:
        raise HivePromotionJournalError(
            "event artifact category does not match promotion journal"
        )
    if artifact.get("change_id") != journal["change_id"]:
        raise HivePromotionJournalError(
            "event artifact change_id does not match promotion journal"
        )
    if artifact.get("scope") != journal["scope"]:
        raise HivePromotionJournalError(
            "event artifact scope does not match promotion journal"
        )
    if artifact.get("transition") != journal["transition"]:
        raise HivePromotionJournalError(
            "event artifact transition does not match promotion journal"
        )

    hashes = artifact.get("artifact_hashes")
    if not isinstance(hashes, dict):
        raise HivePromotionJournalError(
            "event artifact must contain artifact_hashes"
        )
    if (
        hashes.get("promotion_preview_sha256")
        != journal["promotion_preview_sha256"]
    ):
        raise HivePromotionJournalError(
            "event artifact promotion_preview_sha256 does not match journal genesis"
        )
    return hashes


def append_hive_promotion_journal(
    journal: dict[str, Any],
    *,
    event: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Append one validated lifecycle event to a promotion journal."""
    if not isinstance(artifact, dict):
        raise HivePromotionJournalError("event artifact must be a JSON object")
    validate_hive_promotion_journal(journal)

    updated = copy.deepcopy(journal)
    current_state = updated["state"]
    entries = updated["entries"]
    evidence_hashes: dict[str, str | None]

    if event == "applied_lifecycle":
        if current_state != "previewed":
            raise HivePromotionJournalError(
                "applied_lifecycle can only follow the previewed journal state"
            )
        if artifact.get("state") != "applied_verified":
            raise HivePromotionJournalError(
                "applied lifecycle artifact must have state=applied_verified"
            )
        if artifact.get("applied_verified") is not True:
            raise HivePromotionJournalError(
                "applied lifecycle artifact must set applied_verified=true"
            )
        if artifact.get("automatic_config_mutation") is not False:
            raise HivePromotionJournalError(
                "applied lifecycle must keep automatic_config_mutation=false"
            )
        if artifact.get("automatic_rollback") is not False:
            raise HivePromotionJournalError(
                "applied lifecycle must keep automatic_rollback=false"
            )
        hashes = _validate_event_common(updated, artifact)
        evidence_hashes = {
            "runtime_gate_sha256": (
                hashes.get("runtime_gate_sha256")
                if isinstance(hashes.get("runtime_gate_sha256"), str)
                else None
            ),
            "promotion_receipt_sha256": (
                hashes.get("promotion_receipt_sha256")
                if isinstance(hashes.get("promotion_receipt_sha256"), str)
                else None
            ),
        }
    elif event == "rollback_audit":
        if current_state != "applied_verified":
            raise HivePromotionJournalError(
                "rollback_audit can only follow an applied_verified journal state"
            )
        if artifact.get("state") != "rolled_back_verified":
            raise HivePromotionJournalError(
                "rollback audit artifact must have state=rolled_back_verified"
            )
        if artifact.get("rolled_back_verified") is not True:
            raise HivePromotionJournalError(
                "rollback audit artifact must set rolled_back_verified=true"
            )
        if artifact.get("automatic_config_mutation") is not False:
            raise HivePromotionJournalError(
                "rollback audit must keep automatic_config_mutation=false"
            )
        if artifact.get("automatic_rollback") is not False:
            raise HivePromotionJournalError(
                "rollback audit must keep automatic_rollback=false"
            )
        hashes = _validate_event_common(updated, artifact)
        applied_entry = entries[-1]
        if applied_entry.get("event") != "applied_lifecycle":
            raise HivePromotionJournalError(
                "rollback audit requires the prior journal entry to be applied_lifecycle"
            )
        if (
            hashes.get("applied_lifecycle_sha256")
            != applied_entry["artifact_sha256"]
        ):
            raise HivePromotionJournalError(
                "rollback audit applied_lifecycle_sha256 does not match "
                "the journaled applied lifecycle artifact"
            )
        evidence_hashes = {
            "rollback_receipt_sha256": (
                hashes.get("rollback_receipt_sha256")
                if isinstance(hashes.get("rollback_receipt_sha256"), str)
                else None
            ),
            "applied_lifecycle_sha256": hashes.get(
                "applied_lifecycle_sha256"
            ),
        }
    else:
        raise HivePromotionJournalError(
            "event must be applied_lifecycle or rollback_audit"
        )

    artifact_sha = _canonical_sha256(artifact)
    new_entry = _entry(
        sequence=len(entries),
        event=event,
        artifact_sha256=artifact_sha,
        previous_entry_sha256=updated["head_entry_sha256"],
        evidence_hashes=evidence_hashes,
    )
    updated["entries"].append(new_entry)
    updated["head_entry_sha256"] = new_entry["entry_sha256"]
    updated["state"] = new_entry["state"]
    validate_hive_promotion_journal(updated)
    return updated


def render_markdown(journal: dict[str, Any]) -> str:
    validate_hive_promotion_journal(journal)
    lines = [
        "# Hive Promotion Journal",
        "",
        f"- State: **{journal['state']}**",
        f"- Category: **{journal['category']}**",
        f"- Scope: **{journal['scope']}**",
        f"- Change ID: `{journal['change_id']}`",
        "- Automatic config mutation: **disabled**",
        "- Automatic rollback: **disabled**",
        "",
        "## Evidence chain",
        "",
        "| # | Event | State | Artifact SHA-256 | Previous entry |",
        "|---:|---|---|---|---|",
    ]
    for item in journal["entries"]:
        previous = item.get("previous_entry_sha256") or "genesis"
        lines.append(
            f"| {item['sequence']} | {item['event']} | "
            f"**{item['state']}** | `{item['artifact_sha256']}` | "
            f"`{previous}` |"
        )
    lines.extend(
        [
            "",
            f"- Head entry SHA-256: `{journal['head_entry_sha256']}`",
            "",
            (
                "The journal is append-only evidence. It does not apply, "
                "reapply, or roll back Hive configuration."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HivePromotionJournalError(f"{label} must be a JSON object")
    return payload


def _write(path: str | None, content: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create or append an integrity-checked Hive promotion evidence journal."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--promotion-preview", required=True)
    init.add_argument("--output")
    init.add_argument("--json-output")

    append = sub.add_parser("append")
    append.add_argument("--journal", required=True)
    append.add_argument(
        "--event",
        choices=["applied_lifecycle", "rollback_audit"],
        required=True,
    )
    append.add_argument("--artifact", required=True)
    append.add_argument("--output")
    append.add_argument("--json-output")

    args = parser.parse_args(argv)
    if args.command == "init":
        journal = build_hive_promotion_journal(
            _load_json_object(args.promotion_preview, "promotion preview")
        )
    else:
        journal = append_hive_promotion_journal(
            _load_json_object(args.journal, "promotion journal"),
            event=args.event,
            artifact=_load_json_object(args.artifact, "event artifact"),
        )

    markdown = render_markdown(journal)
    if args.output:
        _write(args.output, markdown)
    else:
        print(markdown)
    if args.json_output:
        _write(
            args.json_output,
            json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
