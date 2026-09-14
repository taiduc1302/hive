from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

_PIN_RE = re.compile(r"\blitellm\s*==\s*([0-9][0-9A-Za-z.+-]*)", re.IGNORECASE)
_PIN_CANDIDATES = (
    "pyproject.toml",
    "core/pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "core/requirements.txt",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def detect_litellm_pin(repo_root: Path) -> dict[str, str] | None:
    """Return the first exact LiteLLM pin found in common dependency files."""
    candidates = [repo_root / rel for rel in _PIN_CANDIDATES]
    requirements_dir = repo_root / "requirements"
    if requirements_dir.is_dir():
        candidates.extend(sorted(requirements_dir.glob("*.txt")))

    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        match = _PIN_RE.search(text)
        if match:
            return {"version": match.group(1), "source": str(path.relative_to(repo_root))}
    return None


def _installed_litellm_version() -> str | None:
    try:
        return importlib.metadata.version("litellm")
    except importlib.metadata.PackageNotFoundError:
        return None


def _probe_hive_transport(repo_root: Path) -> dict[str, Any]:
    core_dir = repo_root / "core"
    core_text = str(core_dir)
    if core_text not in sys.path:
        sys.path.insert(0, core_text)

    try:
        from framework.llm import litellm as hive_litellm
        from framework.llm.litellm import LiteLLMProvider
    except Exception as exc:  # noqa: BLE001 - probe must report, not crash
        return {
            "importable": False,
            "provider_class": False,
            "post_transform_capture": False,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }

    capture = getattr(hive_litellm, "_last_llm_request", None)
    return {
        "importable": True,
        "provider_class": callable(LiteLLMProvider),
        "post_transform_capture": callable(getattr(capture, "get", None)),
    }


def build_capability_report(
    repo_root: Path | None = None,
    *,
    installed_version_getter: Callable[[], str | None] | None = None,
    transport_probe: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe controls the current Hive host can prove without provider calls."""
    root = (repo_root or _repo_root()).resolve()
    pin = detect_litellm_pin(root)
    installed = (installed_version_getter or _installed_litellm_version)()
    transport = (transport_probe or _probe_hive_transport)(root)

    wire_capture = bool(transport.get("post_transform_capture"))
    provider_class = bool(transport.get("provider_class"))
    single_ready = provider_class and wire_capture

    warnings: list[str] = []
    if pin is None:
        warnings.append("No exact LiteLLM pin was found in the known dependency files.")
    if installed is None:
        warnings.append("LiteLLM is not installed in this Python environment.")
    if pin and installed and pin["version"] != installed:
        warnings.append(
            f"Installed LiteLLM {installed} differs from repository pin {pin['version']}; "
            "benchmark evidence should identify the installed runtime version."
        )
    if not wire_capture:
        warnings.append(
            "Hive post-transform request capture is unavailable; explicit model/effort proof is fail-closed."
        )

    return {
        "schema_version": 1,
        "host": "hive",
        "repo_root": str(root),
        "litellm": {
            "repository_pin": pin,
            "installed_version": installed,
            "versions_match": bool(pin and installed and pin["version"] == installed),
        },
        "transport": {
            "name": "hive_litellm",
            **transport,
            "single_call_evidence_ready": single_ready,
        },
        "controls": {
            "model": {
                "status": "runtime_verified",
                "proof": "post-transform request body",
            },
            "reasoning_effort": {
                "status": "runtime_verified",
                "proof": "post-transform provider request body",
                "default_semantics": "omit explicit effort parameter",
            },
            "execution_modes": {
                "single": "supported_by_advisor_adapter",
                "hive_agent_loop": "host_exists_adapter_not_implemented",
                "dynamic_workflow": "host_exists_adapter_not_implemented",
                "subagents": "host_exists_adapter_not_implemented",
                "chatgpt_work": "external_host_not_hive",
                "ultracode": "not_a_native_hive_control",
            },
        },
        "evidence_policy": {
            "network_calls_performed": False,
            "credentials_required": False,
            "registry_listing_implies_runtime_support": False,
            "provider_call_required_for_model_specific_compatibility": True,
            "unsupported_or_unproved_configuration": "fail_closed_no_model_evidence",
        },
        "warnings": warnings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    litellm = report["litellm"]
    transport = report["transport"]
    pin = litellm.get("repository_pin")
    pin_text = pin["version"] if isinstance(pin, dict) else "not detected"
    installed = litellm.get("installed_version") or "not installed"
    execution = report["controls"]["execution_modes"]

    lines = [
        "# Hive Runtime Capability Probe",
        "",
        f"- Repository LiteLLM pin: **{pin_text}**",
        f"- Installed LiteLLM: **{installed}**",
        f"- Hive LiteLLM transport importable: **{bool(transport.get('importable'))}**",
        f"- Post-transform request capture: **{bool(transport.get('post_transform_capture'))}**",
        f"- Single-call evidence ready: **{bool(transport.get('single_call_evidence_ready'))}**",
        "",
        "## Execution controls",
        "",
    ]
    lines.extend(f"- `{mode}`: {status}" for mode, status in execution.items())
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "This probe performs no provider/network call. It proves host plumbing only. "
            "A real adapter run must still prove the requested model and effort on the post-transform wire body.",
        ]
    )
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Hive runtime controls without calling a model provider.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    args = parser.parse_args(argv)

    report = build_capability_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
