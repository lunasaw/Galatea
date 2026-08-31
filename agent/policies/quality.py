"""Quality gate policy for stage validation."""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum


class GateStatus(str, Enum):
    """Quality gate status."""

    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    SKIP = "skip"


@dataclass
class QualityGate:
    """
    Quality gate definition.

    Defines threshold and comparison for a metric.
    """

    name: str
    metric_name: str
    threshold: float
    comparison: str  # ">=", "<=", "==", ">", "<"
    required: bool = True
    weight: float = 1.0

    def evaluate(self, value: float) -> GateStatus:
        """
        Evaluate metric value against gate.

        Args:
            value: Metric value

        Returns:
            Gate status

        """
        comparisons = {
            ">=": value >= self.threshold,
            "<=": value <= self.threshold,
            "==": value == self.threshold,
            ">": value > self.threshold,
            "<": value < self.threshold,
        }
        if self.comparison not in comparisons:
            raise ValueError(f"Unsupported comparison: {self.comparison}")
        return GateStatus.PASS if comparisons[self.comparison] else GateStatus.FAIL


class QualityGatePolicy:
    """
    Quality gate policy for stage validation.

    Manages quality gates for data, training, and inference stages.
    """

    def __init__(self):
        """Initialize quality gate policy."""
        self.gates: Dict[str, List[QualityGate]] = {
            "data": [],
            "training": [],
            "inference": [],
        }

    def add_gate(
        self,
        stage: str,
        gate: QualityGate,
    ) -> None:
        """
        Add quality gate for stage.

        Args:
            stage: Stage name (data, training, inference)
            gate: Quality gate definition

        """
        self._validate_stage(stage)
        if any(existing.name == gate.name for existing in self.gates[stage]):
            raise ValueError(f"Quality gate already exists for {stage}: {gate.name}")
        self.gates[stage].append(gate)

    def remove_gate(
        self,
        stage: str,
        gate_name: str,
    ) -> None:
        """
        Remove quality gate.

        Args:
            stage: Stage name
            gate_name: Gate name to remove

        """
        self._validate_stage(stage)
        self.gates[stage] = [gate for gate in self.gates[stage] if gate.name != gate_name]

    def evaluate_gates(
        self,
        stage: str,
        metrics: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        Evaluate all gates for stage.

        Args:
            stage: Stage name
            metrics: Metric values

        Returns:
            Evaluation results with pass/fail per gate

        """
        self._validate_stage(stage)
        results: List[Dict[str, Any]] = []
        failed_required = []

        for gate in self.gates[stage]:
            if gate.metric_name not in metrics:
                status = GateStatus.FAIL if gate.required else GateStatus.SKIP
                actual = None
            else:
                actual = metrics[gate.metric_name]
                status = gate.evaluate(actual)

            result = {
                "name": gate.name,
                "metric_name": gate.metric_name,
                "status": status.value,
                "required": gate.required,
                "comparison": gate.comparison,
                "threshold": gate.threshold,
                "actual": actual,
            }
            results.append(result)
            if gate.required and status == GateStatus.FAIL:
                failed_required.append(result)

        return {
            "stage": stage,
            "status": "pass" if not failed_required else "fail",
            "passed": not failed_required,
            "results": results,
            "failed_required": failed_required,
        }

    def get_gates(self, stage: str) -> List[QualityGate]:
        """
        Get quality gates for stage.

        Args:
            stage: Stage name

        Returns:
            List of quality gates

        """
        self._validate_stage(stage)
        return list(self.gates[stage])

    def _validate_stage(self, stage: str) -> None:
        if stage not in self.gates:
            raise ValueError(f"Unknown stage: {stage}")


class QualityGateFailedError(Exception):
    """Raised when required quality gate fails."""

    def __init__(
        self,
        gate_name: str,
        expected: float,
        actual: float,
    ):
        """
        Initialize quality gate failed error.

        Args:
            gate_name: Gate that failed
            expected: Expected threshold
            actual: Actual value
        """
        self.gate_name = gate_name
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Quality gate '{gate_name}' failed: {actual} vs {expected}"
        )
