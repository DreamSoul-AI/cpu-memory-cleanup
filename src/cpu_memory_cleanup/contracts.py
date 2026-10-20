"""共享数据与平台合同。导入本模块不取得任何系统资源。"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Protocol

SCHEMA_VERSION = 1
RULE_VERSION = "1"
MECHANISMS = ("working-set", "standby")


def finite_number(value, name, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: 必须是数值")
    try:
        valid = math.isfinite(value) and value >= minimum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name}: 必须是有限且不小于 {minimum} 的数值")
    return value


def _check_values(value, path):
    if value is None or isinstance(value, str):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: 字段名必须是文本")
            _check_values(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_values(item, f"{path}[{index}]")
    else:
        signed = path.rsplit(".", 1)[-1] in {"residual_gib", "residual_bytes", "residual_pct_of_commit"}
        finite_number(value, path, minimum=-float("inf") if signed else 0)


@dataclass
class Sample:
    run_id: str
    sequence: int
    timestamp: str | None
    metrics: dict
    processes: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("run_id: 缺少运行标识")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("sequence: 必须是非负整数")
        if self.timestamp is not None and not isinstance(self.timestamp, str):
            raise ValueError("timestamp: 必须是带时区的 ISO 时间")
        if self.timestamp is not None:
            try:
                stamp = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("timestamp: 无效 ISO 时间") from error
            if stamp.tzinfo is None:
                raise ValueError("timestamp: 缺少时区")
        if not isinstance(self.metrics, dict) or not isinstance(self.processes, list):
            raise ValueError("样本指标必须是对象，进程必须是列表")
        if not isinstance(self.errors, list):
            raise ValueError("errors: 必须是列表")
        _check_values(self.metrics, "metrics")
        _check_values(self.processes, "processes")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ActionRequest:
    mechanism: str
    scope: str = "system"
    confirmed: bool = False
    confirmation_source: str = ""

    def __post_init__(self):
        if self.mechanism not in MECHANISMS or self.scope != "system":
            raise ValueError("只支持明确的单项系统范围维护")
        if type(self.confirmed) is not bool or not isinstance(self.confirmation_source, str):
            raise ValueError("维护确认格式无效")


def validate_action(action):
    if not isinstance(action, dict):
        raise ValueError("维护调用事实必须是对象")
    attempted, outcome = action.get("attempted"), action.get("call_outcome")
    if (type(attempted) not in (bool, type(None)) or outcome not in ("success", "failure", "unknown", "not_started")
            or (outcome in ("success", "failure") and attempted is not True)
            or (outcome == "not_started" and attempted is not False)
            or (attempted is None and outcome != "unknown")
            or type(action.get("privilege_restored")) not in (bool, type(None))):
        raise ValueError("维护调用事实互相矛盾")
    if outcome in ("success", "failure"):
        status = action.get("status")
        if type(status) is not int or not 0 <= status <= 0xffffffff or ((status == 0) != (outcome == "success")):
            raise ValueError("原生状态与结果不一致")
    return action.copy()


@dataclass
class RunReport:
    run_id: str
    mode: str
    completion: str = "failed"
    phase: str = "preparing"
    samples: list = field(default_factory=list)
    analysis: dict = field(default_factory=dict)
    action: dict = field(default_factory=lambda: {
        "attempted": False, "call_outcome": "not_started", "privilege_restored": None,
    })
    errors: list = field(default_factory=list)
    cancel_requested: bool = False
    metadata: dict = field(default_factory=dict)
    output_dir: str | None = None

    def to_dict(self):
        return asdict(self)


class Observation(Protocol):
    def read(self, run_id: str, sequence: int) -> Sample: ...
    def close(self) -> dict: ...


class Operation(Protocol):
    def preflight(self) -> dict: ...
    def perform_once(self) -> dict: ...
    def close(self) -> dict: ...


class Reader(Protocol):
    def open_observation(self, *, with_processes: bool = False, top: int = 10) -> Observation: ...


class Maintainer(Protocol):
    def open_operation(self, request: ActionRequest) -> Operation: ...
