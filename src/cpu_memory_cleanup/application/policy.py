"""显式武装的纯规则状态机；不采样、不创建线程、不自行派发。"""

from dataclasses import dataclass

from ..contracts import finite_number


@dataclass(frozen=True)
class Rule:
    mode: str = "interval"
    interval_s: float = 600
    available_pct: float = 10
    sustain_s: float = 30
    cooldown_s: float = 300

    def __post_init__(self):
        if self.mode not in ("interval", "pressure"):
            raise ValueError("规则只支持定时或持续压力")
        for key in ("interval_s", "sustain_s", "cooldown_s"):
            value = finite_number(getattr(self, key), key, minimum=1)
            if value > 86400:
                raise ValueError("规则时间不能超过一天")
        if finite_number(self.available_pct, "available_pct", minimum=0.1) > 100:
            raise ValueError("可用内存阈值不能超过100%")


class Policy:
    def __init__(self, rule=None):
        self.rule = rule or Rule()
        self.armed = False
        self._last_time = self._last_dispatch = self._low_since = None
        self.source = ""

    def arm(self, now, *, confirmed=False, source=""):
        finite_number(now, "时钟")
        if confirmed is not True or not isinstance(source, str) or not source.strip():
            raise ValueError("规则启用需要本次确认与来源")
        self.armed, self.source = True, source
        self._last_time = self._last_dispatch = now
        self._low_since = None

    def stop(self):
        self.armed = False
        self._low_since = None

    def due(self, now, *, available_pct=None, busy=False):
        finite_number(now, "时钟")
        if not self.armed:
            return False
        if now < self._last_time:
            self.stop()
            raise ValueError("规则时钟回退，已停用")
        gap = now - self._last_time
        self._last_time = now
        if busy or gap > 5:
            self._low_since = None
        if self.rule.mode == "pressure":
            if available_pct is None:
                self._low_since = None
                return False
            if finite_number(available_pct, "可用比例") > 100:
                raise ValueError("可用比例无效")
            if available_pct >= self.rule.available_pct:
                self._low_since = None
                return False
            if self._low_since is None:
                self._low_since = now
            ready = now - self._low_since >= self.rule.sustain_s
        else:
            ready = now - self._last_dispatch >= self.rule.interval_s
        return not busy and ready and now - self._last_dispatch >= self.rule.cooldown_s

    def dispatched(self, now):
        finite_number(now, "时钟")
        self._last_dispatch, self._low_since = now, None
