class _V3:
    def __init__(self): self.x = 0.0; self.y = 0.0; self.z = 0.0
class _Quat:
    def __init__(self): self.x = 0.0; self.y = 0.0; self.z = 0.0; self.w = 1.0
class _Header:
    def __init__(self): self.stamp = None; self.frame_id = ''
class Twist:
    def __init__(self): self.linear = _V3(); self.angular = _V3()
class TwistStamped:
    def __init__(self): self.header = _Header(); self.twist = Twist()
class _Pose:
    def __init__(self): self.position = _V3(); self.orientation = _Quat()
class PoseStamped:
    def __init__(self): self.header = _Header(); self.pose = _Pose()
