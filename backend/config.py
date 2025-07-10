# backend/config.py

import os 

# --- Robot Connection Settings ---
SIMULATION_HOST = '127.0.0.1'
SIMULATION_PORT = 55000
REAL_ROBOT_HOST = '192.168.125.1' # Your actual robot IP
REAL_ROBOT_PORT = 1025          # Your actual robot port

# The USE_REAL_ROBOT constant is now primarily a server-side default
# if the client doesn't specify. The client's choice will take precedence.
# It can also be used by other backend modules if they need a default
# robot type assumption without client input.
USE_REAL_ROBOT_DEFAULT = False # Default to simulation if not specified by client

# --- QR Code Upload Settings ---
QR_UPLOAD_FOLDER = 'qr_uploads' # Relative to the backend directory


# --- Robot Predefined Positions ---
# These are (X, Z_depth, Y_left_right) tuples as sent from Python
# Z is typically the pen height/depth axis for drawing.
# Y is typically the left/right axis on the paper for drawing.

ROBOT_HOME_POSITION_PY = (300, -350.922061873, 300)
SAFE_ABOVE_CENTER_PY = (0.00, -150.0, 0.00)

# --- Drawing Constants (adapted from original main.py) ---
# These define the target drawing area in mm for scaling.
# The (X,Y,Z) offsets sent to the robot are relative to WorkSpaceCenter1 in RAPID.
# The Python (X_py, Y_py) from image processing will map to RAPID (x_offset, z_offset).
# The Python Z_py (pen height) will map to RAPID y_offset.

A4_DRAWING_AREA_WIDTH_MM = 320  # Effective drawing width for scaling image contours
A4_DRAWING_AREA_HEIGHT_MM = 212 # Effective drawing height for scaling image contours

# Python Z-values for pen height, these will be sent as the 'Z' in the "X,Z,Y" string
# which corresponds to the 'y' offset in the RAPID MoveL Offs(WorkSpaceCenter1, x, y, z)


# *** MODIFIED: Renamed to DEFAULT_PEN_DOWN_Z_PY to indicate it's a fallback ***
DEFAULT_PEN_DOWN_Z_PY = -15 # Pen down position (e.g., -7mm from WorkSpaceCenter1's XY plane along its Y-axis)
PEN_UP_Z_PY = 1.2 * DEFAULT_PEN_DOWN_Z_PY  # Pen up position (e.g., -15mm from WorkSpaceCenter1's XY plane along its Y-axis)

MIN_CONTOUR_LENGTH_PX = 20# Minimum contour length in pixels to consider from image processing

DEFAULT_CANNY_THRESHOLD1 = 50
DEFAULT_CANNY_THRESHOLD2 = 150

# --- Signature Settings ---
# Path to the signature image, relative to the backend directory
ASSETS_FOLDER_NAME = 'assets'
SIGNATURE_IMAGE_FILENAME = "signature.jpg" # Ensure this file is in backend/assets/

# Canny thresholds for processing the signature image.
# These can be tuned for optimal results with your specific signature.jpg
SIGNATURE_CANNY_THRESHOLD1 = 50
SIGNATURE_CANNY_THRESHOLD2 = 150
# You might want to make these thresholds different from the default image processing
# if your signature image requires different settings for optimal edge detection.
# For example, if the signature is very clean, lower thresholds might be better.
# SIGNATURE_CANNY_THRESHOLD1 = 30
# SIGNATURE_CANNY_THRESHOLD2 = 100


# --- Temporary Audio File Settings ---
AUDIO_TEMP_FOLDER = 'audio_tmp' # Relative to the backend directory

# --- LLM Settings ---
# IMPORTANT: Replace with the actual filename of your downloaded GGUF model.
# The file should be placed inside the `backend/models/` directory.
# LLM_MODEL_FILENAME = "deepseek-llm-7b-chat.Q4_K_M.gguf" 
LLM_MODEL_FILENAME = " " 

LLM_MAX_TOKENS = 512
LLM_TEMPERATURE = 0.3
LLM_N_CTX = 2048
LLM_N_GPU_LAYERS = 0 # Set to a number > 0 to offload layers to GPU. Requires compatible hardware and drivers.
