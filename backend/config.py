# backend/config.py

# --- Robot Connection Settings ---
SIMULATION_HOST = '127.0.0.1'
SIMULATION_PORT = 55000
REAL_ROBOT_HOST = '192.168.125.1' # Your actual robot IP
REAL_ROBOT_PORT = 1025          # Your actual robot port

# Set True to use real robot, False for simulation
USE_REAL_ROBOT = False

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

A4_DRAWING_AREA_WIDTH_MM = 180  # Effective drawing width for scaling image contours
A4_DRAWING_AREA_HEIGHT_MM = 217 # Effective drawing height for scaling image contours

# Python Z-values for pen height, these will be sent as the 'Z' in the "X,Z,Y" string
# which corresponds to the 'y' offset in the RAPID MoveL Offs(WorkSpaceCenter1, x, y, z)
PEN_UP_Z_PY = -15.0  # Pen up position (e.g., -15mm from WorkSpaceCenter1's XY plane along its Y-axis)
PEN_DOWN_Z_PY = -7.0 # Pen down position (e.g., -7mm from WorkSpaceCenter1's XY plane along its Y-axis)

MIN_CONTOUR_LENGTH_PX = 50 # Minimum contour length in pixels to consider from image processing

DEFAULT_CANNY_THRESHOLD1 = 50
DEFAULT_CANNY_THRESHOLD2 = 150

# --- Signature Points ---
# Import from the separate signature_data.py file
try:
    from .signature_data import SIGNATURE_POINTS_RAW
    # If you used SIGNATURE_STROKES_RAW, import that instead:
    # from .signature_data import SIGNATURE_STROKES_RAW
except ImportError:
    # Fallback or error handling if the file/variable isn't found
    SIGNATURE_POINTS_RAW = () # Default to empty tuple if import fails
    # SIGNATURE_STROKES_RAW = [] 
    print("WARNING: Could not import signature data. Signature will be empty.")


# --- Temporary Audio File Settings ---
AUDIO_TEMP_FOLDER = 'audio_tmp' # Relative to the backend directory

# --- LLM Settings ---
# IMPORTANT: Replace with the actual filename of your downloaded GGUF model
LLM_MODEL_FILENAME = "deepseek-llm-7b-chat.Q4_K_M.gguf"
LLM_MAX_TOKENS = 512 # Max tokens for LLM response
LLM_TEMPERATURE = 0.3 # Temperature for LLM response
LLM_N_CTX = 2048 # Context window size for the LLM
LLM_N_GPU_LAYERS = 0 # Number of layers to offload to GPU. 0 for CPU only. 
                     # If you have a compatible GPU and llama-cpp-python with GPU support, you can increase this.
