import time

PARAM_OVERRIDES = {}

class _Param:
    def __init__(self, value): self.value = value

class _Logger:
    def info(self, msg, **kw): print('[INFO]', msg)
    def warn(self, msg, **kw): print('[WARN]', msg)
    def error(self, msg, **kw): print('[ERR ]', msg)

class _Now:
    def to_msg(self): return {'t': time.time()}

class _Clock:
    def now(self): return _Now()

class _Pub:
    def __init__(self, topic): self.topic, self.msgs = topic, []
    def publish(self, m): self.msgs.append(m)

class Node:
    def __init__(self, name):
        self._name, self._params, self.pubs, self.timers = name, {}, {}, []
    def declare_parameter(self, name, default):
        self._params[name] = PARAM_OVERRIDES.get(name, default)
    def get_parameter(self, name): return _Param(self._params[name])
    def create_publisher(self, type_, topic, qos):
        p = _Pub(topic); self.pubs[topic] = p; return p
    def create_timer(self, period, cb):
        self.timers.append(cb); return cb
    def get_logger(self): return _Logger()
    def get_clock(self): return _Clock()
    def destroy_node(self): pass
