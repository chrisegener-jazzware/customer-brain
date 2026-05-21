"""Customer-brain modules engine.

Each module is a function (s: Session, company_id: str) -> ModuleResult.
Modules compute per-account signals and expose a normalized result so the
API + UI can list them uniformly.

JAZ-113 Sales Pulse, JAZ-114 Renewal Radar, JAZ-115 Churn Early-Warning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from .churn import compute as compute_churn
from .renewal import compute as compute_renewal
from .sales_pulse import compute as compute_sales_pulse


@dataclass
class ModuleResult:
    """Normalized output every module returns."""

    module_id: str            # 'churn_ew' | 'renewal_radar' | 'sales_pulse'
    label: str
    score: float | None       # 0-100 (higher = more attention), None if N/A
    severity: str             # 'low' | 'medium' | 'high' | 'na'
    headline: str             # 1-line plain-English summary
    drivers: list[dict[str, Any]] = field(default_factory=list)  # supporting facts
    metrics: dict[str, Any] = field(default_factory=dict)        # raw numbers for UI


# Registry — order matters for UI list.
MODULE_REGISTRY: list[tuple[str, Callable[[Session, str], ModuleResult]]] = [
    ("churn_ew",      compute_churn),
    ("renewal_radar", compute_renewal),
    ("sales_pulse",   compute_sales_pulse),
]


def run_all(s: Session, company_id: str) -> list[ModuleResult]:
    """Run every registered module and return their results."""
    results: list[ModuleResult] = []
    for module_id, fn in MODULE_REGISTRY:
        try:
            results.append(fn(s, company_id))
        except Exception as exc:  # noqa: BLE001
            results.append(ModuleResult(
                module_id=module_id,
                label=module_id,
                score=None,
                severity="na",
                headline=f"module error: {exc}",
            ))
    return results
