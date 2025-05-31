# backend/config.py

# --- Robot Connection Settings ---
SIMULATION_HOST = '127.0.0.1'
SIMULATION_PORT = 55000
# REAL_ROBOT_HOST = '192.168.125.1' # Your actual robot IP
# REAL_ROBOT_PORT = 1025          # Your actual robot port

# Set True to use real robot, False for simulation
USE_REAL_ROBOT = False

# --- Robot Predefined Positions ---
# These are (X, Z, Y) tuples as sent from Python
# Z is typically the pen height/depth axis for drawing.
# Y is typically the left/right axis on the paper for drawing.

# User-defined home position corresponding to RAPID's home1
# (home1.X, home1.Z, home1.Y)
ROBOT_HOME_POSITION_PY = (409.328464947, -350.922061873, 30.699294352)

# A safe position above the center of the workspace (example)
# (X_offset_from_WorkSpaceCenter, Z_depth_height, Y_offset_from_WorkSpaceCenter)
SAFE_ABOVE_CENTER_PY = (0.00, -150.0, 0.00) # e.g., Z=-150 might be 150mm above paper if WorkSpaceCenter is on paper

# --- Drawing Constants (from original main.py, if needed later) ---
# PEN_UP_Z_PY = -15.0  # Python Z-value for pen up (becomes RAPID Y-offset)
# PEN_DOWN_Z_PY = -7.0 # Python Z-value for pen down (becomes RAPID Y-offset)