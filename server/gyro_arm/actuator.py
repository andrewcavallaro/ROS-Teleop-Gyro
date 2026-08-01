"""Hardware hook -- EDIT THIS FILE to wire up your real actuator.

send_to_actuator() is called ~CONFIG["loop_hz"] times per second with the
latest state snapshot. If it raises, the control loop stops all motion, flags
hw_fault (shown as a red HW FAULT chip on the dashboard), and retries only a
SAFE-STOP command at 1 Hz until a call succeeds again -- then teleop resumes.

The firmware side should have its own watchdog too: if this process dies right
after commanding a velocity, nothing here can stop the motor. See the Arduino
example in the README (it stops output if no line arrives for 300 ms).
"""

# import serial                                          # pip install pyserial
# SER = serial.Serial("/dev/ttyACM0", 115200, timeout=0) # open once, not per call


def send_to_actuator(s):
    """s has: x, y (0..1 floats), grip_closed (bool), vx, vy, angles, connected.

    Example -- stream "x,y,grip" lines to an Arduino driving servos/steppers:
        SER.write(f"{s['x']:.3f},{s['y']:.3f},{int(s['grip_closed'])}\n".encode())
    """
    pass
