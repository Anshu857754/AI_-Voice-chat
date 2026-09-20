"""Latency + counter collection.

Everything recorded here comes from ``time.perf_counter()`` stamps taken by the
real pipeline stages. No value is synthesized: if a stage was skipped (for
example TTS when a bot replies in text only) its latency stays ``None`` and is
excluded from aggregates.

The collector keeps a bounded ring of recent traces so the debug panel in the
UI can show observed numbers without unbounded memory growth.
"""

from __future__ import annotations

import statistics
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.models.conversation import LatencyTrace
from app.utils.logging import TAG_LATENCY, get_logger

log = get_logger(__name__)

_STAGES = (
    "stt_latency_ms",
    "router_latency_ms",
    "llm_ttft_ms",
    "llm_latency_ms",
    "tts_latency_ms",
    "total_latency_ms",
)


@dataclass
class StageStats:
    count: int = 0
    p50: float | None = None
    p95: float | None = None
    mean: float | None = None
    minimum: float | None = None
    maximum: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "p50_ms": self.p50,
            "p95_ms": self.p95,
            "mean_ms": self.mean,
            "min_ms": self.minimum,
            "max_ms": self.maximum,
        }


def _summarize(values: list[float]) -> StageStats:
    if not values:
        return StageStats()
    ordered = sorted(values)
    return StageStats(
        count=len(ordered),
        p50=round(statistics.median(ordered), 1),
        p95=round(_percentile(ordered, 0.95), 1),
        mean=round(statistics.fmean(ordered), 1),
        minimum=round(ordered[0], 1),
        maximum=round(ordered[-1], 1),
    )


def _percentile(ordered: list[float], q: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


@dataclass
class MetricsCollector:
    """Process-local metrics. Swap for Prometheus/OTel later behind this API."""

    capacity: int = 200
    traces: deque[LatencyTrace] = field(default_factory=lambda: deque(maxlen=200))
    counters: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        self.traces = deque(maxlen=self.capacity)

    # ---- counters ---------------------------------------------------------
    def incr(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    # ---- latency ----------------------------------------------------------
    def record(self, trace: LatencyTrace) -> None:
        """Store a completed trace and log the measured stage timings."""
        self.traces.append(trace)
        log.stage(TAG_LATENCY, **trace.as_log_fields())

    def stage_stats(self, stage: str, traces: Iterable[LatencyTrace] | None = None) -> StageStats:
        source = self.traces if traces is None else traces
        values = [v for t in source if (v := getattr(t, stage)) is not None]
        return _summarize(values)

    def snapshot(self) -> dict[str, object]:
        """Aggregate view used by /metrics and the UI debug panel."""
        return {
            "samples": len(self.traces),
            "stages": {stage: self.stage_stats(stage).as_dict() for stage in _STAGES},
            "counters": dict(self.counters),
        }

    def recent(self, limit: int = 20) -> list[dict[str, object]]:
        items = list(self.traces)[-limit:]
        return [t.as_log_fields() for t in reversed(items)]

    def reset(self) -> None:
        self.traces.clear()
        self.counters.clear()


# Shared instance — the orchestrator and the API both report from this.
METRICS = MetricsCollector()
