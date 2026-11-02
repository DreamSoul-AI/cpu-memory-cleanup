"""可移除的过程装饰状态，不拥有业务接口或真实指标。"""


class CleanupEffect:
    def __init__(self):
        self.stop()

    def stop(self):
        self.phase = None
        self.value = 0.

    def start(self, value, now):
        self.phase, self.started = "emptying", now
        self.origin = self.value = max(0., min(100., value or 0.))
        self.ready = False

    def finish(self, value):
        self.target = max(0., min(100., value or 0.))
        self.ready = True

    def tick(self, now):
        if self.phase == "emptying":
            p = min(1., max(0., (now-self.started)/3))
            self.value = self.origin*(1-p*p*(3-2*p))
            if p == 1:
                self.phase, self.started = "waiting", now
        elif self.phase == "waiting" and self.ready and now-self.started >= .2:
            self.phase, self.started = "returning", now
        elif self.phase == "returning":
            p = min(1., max(0., (now-self.started)/1.2))
            self.value = self.target*(1-(1-p)**3)
            if p == 1:
                self.phase = None
        return self.value
