class _Header:
    def __init__(self): self.stamp = None; self.frame_id = ''
class Joy:
    def __init__(self): self.header = _Header(); self.axes = []; self.buttons = []
