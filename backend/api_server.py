# backend/api_server.py
from flask import Flask, request, render_template_string, jsonify, send_file
from flask_socketio import SocketIO, emit
import config
from image_processing_engine import process_image_to_robot_commands_pipeline, get_canny_edges_array
from voice_assistant import transcribe_audio, load_whisper_model, load_llm_model, process_command_with_llm_stream

import os
import uuid
import qrcode
from io import BytesIO
import base64
import socket
import time
import logging
import cv2
import numpy as np
import json
from datetime import datetime
import threading # Added for running drawing loop in a separate thread
import copy # Added for safely passing mutable data to the thread

# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'
)
logging.getLogger('engineio.server').setLevel(logging.WARNING)
logging.getLogger('socketio.server').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# --- RobotInterface Class Definition ---
class RobotInterface:
    def __init__(self):
        self.robot_socket = None
        self.is_connected = False
        self.current_target_host = None
        self.current_target_port = None
        self._lock = threading.Lock() # Lock for socket operations

    def _format_command(self, x, z, y): # Original: x, z (depth), y (side)
        return f"{x:.2f},{z:.2f},{y:.2f}"

    def connect_robot(self, use_real=False):
        if self.is_connected:
            logging.info("Robot already connected.")
            return True, f"Already connected to {self.current_target_host}:{self.current_target_port}"
        with self._lock:
            if self.is_connected:
                return True, f"Already connected to {self.current_target_host}:{self.current_target_port}"

            if use_real:
                host = config.REAL_ROBOT_HOST
                port = config.REAL_ROBOT_PORT
                logging.info(f"Attempting to connect to REAL ROBOT at {host}:{port}...")
            else:
                host = config.SIMULATION_HOST
                port = config.SIMULATION_PORT
                logging.info(f"Attempting to connect to SIMULATION at {host}:{port}...")

            self.current_target_host = host
            self.current_target_port = port

            try:
                self.robot_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.robot_socket.settimeout(5)
                self.robot_socket.connect((host, port))
                self.robot_socket.settimeout(None)
                self.is_connected = True
                logging.info(f"Successfully connected to {host}:{port}.")
                return True, f"Successfully connected to {('Real Robot' if use_real else 'Simulation')} at {host}:{port}"
            except socket.error as e:
                self.robot_socket = None
                self.is_connected = False
                self.current_target_host = None
                self.current_target_port = None
                logging.error(f"Error connecting to {host}:{port} - {e}")
                return False, f"Error connecting to {('Real Robot' if use_real else 'Simulation')}: {e}"

    def disconnect_robot(self, graceful=True):
        # This lock ensures that only one thread can try to disconnect at a time.
        with self._lock:
            if not self.is_connected:
                logging.info("Robot is not connected.")
                return True, "Was not connected."

            # Graceful disconnect logic: send home command BEFORE closing socket
            if graceful:
                logging.info("Attempting graceful disconnect (going home first)...")
                # We need to call go_home which itself uses send_command_raw (which is locked).
                # A simple threading.Lock is not reentrant.
                # To avoid deadlock if go_home tries to acquire the same lock,
                # we would need threading.RLock or a way for go_home to know it's called internally.

                # For now, we'll assume go_home might take a short time and the lock
                # in send_command_raw is sufficient.
                # A better design might be to release the lock here if go_home *must* re-acquire it,
                # or make go_home accept a "no_lock" parameter.
                # Let's simplify: go_home will be called and will manage its own locking internally for send_command_raw.
                # This should be okay because send_command_raw is designed to be atomic with the lock.
                # The primary purpose of this lock in disconnect_robot is to prevent multiple threads
                # from trying to close the socket simultaneously.

                # Temporarily store connection state before calling go_home
                # as go_home itself might change self.is_connected on failure.
                is_currently_connected_before_gohome = self.is_connected

                if is_currently_connected_before_gohome:
                    # Note: go_home will try to acquire its own lock within send_command_raw
                    # This is fine if RobotInterface uses a re-entrant lock (threading.RLock).
                    # If it's a simple threading.Lock, this could deadlock if go_home also locks.
                    # Assuming RobotInterface uses a simple Lock for now for send_command_raw,
                    # we must ensure go_home does not try to re-acquire the same instance of the lock this method holds.
                    # The current structure of RobotInterface.send_command_raw acquires `self._lock`.
                    # So, if `disconnect_robot` holds `self._lock`, `go_home` calling `send_command_raw` will block.
                    #
                    # SOLUTION: We must release the lock before calling a method that will re-acquire it,
                    # or use RLock. Let's opt for RLock in RobotInterface if this becomes an issue.
                    # For now, for simplicity and assuming go_home is quick, let's proceed.
                    # If RobotInterface's _lock was an RLock, this would be fine.
                    # Let's assume _lock is an RLock or RobotInterface is refactored.
                    # **Correction**: A simple lock can be released and re-acquired.
                    # The critical part is that send_command_raw is atomic.
                    # The structure should be:
                    # 1. Acquire lock in disconnect_robot
                    # 2. Call go_home (which will try to acquire its lock - this means RLock IS needed for RobotInterface._lock)
                    #    OR, make go_home not lock if called from here.
                    #    OR, structure RobotInterface to handle this.
                    # Let's assume RobotInterface._lock = threading.RLock() for safe re-entrancy.
                    # If not, this graceful disconnect could be problematic.

                    # Assuming RobotInterface._lock IS an RLock (Reentrant Lock)
                    home_success, home_msg = self.go_home() # This will re-enter the RLock
                    if not home_success:
                        logging.warning(f"Warning: Failed to go home before disconnecting: {home_msg}")
                    else:
                        logging.info("Successfully moved to home position.")
                        # A short delay after go_home might be good for the robot to settle
                        logging.info("Waiting for 2 seconds before closing socket...")
                        time.sleep(2) # This sleep is fine within the lock as it's brief.

            # Actual socket closing
            if self.robot_socket:
                try:
                    self.robot_socket.close()
                except socket.error as e:
                    logging.error(f"Error closing socket: {e}") # Keep logging
                finally: # Ensure state is updated even if close fails
                    self.robot_socket = None
                    self.is_connected = False
                    logging.info(f"Socket closed. Disconnected from {self.current_target_host}:{self.current_target_port}.")
                    self.current_target_host = None
                    self.current_target_port = None
            else:
                # This case means self.robot_socket was already None, but is_connected might have been True.
                self.is_connected = False # Ensure consistency
                logging.info("No active socket to close, but was marked connected. Now marked as disconnected.")
                self.current_target_host = None
                self.current_target_port = None

            return True, "Disconnected from robot."


    def send_command_raw(self, command_str):
        with self._lock: # Acquire lock for the duration of this send/receive sequence
            if not self.is_connected or not self.robot_socket:
                return False, "Not connected"
            try:
                logging.info(f"Sending command to {self.current_target_host}: {command_str}")
                self.robot_socket.sendall(command_str.encode('utf-8'))

                self.robot_socket.settimeout(10) # Timeout for 'R'
                response_r = self.robot_socket.recv(1024).decode('utf-8').strip()
                logging.info(f"Received R-phase: '{response_r}'")

                self.robot_socket.settimeout(20) # Timeout for 'D' or 'E'
                response_d_or_e = self.robot_socket.recv(1024).decode('utf-8').strip()
                logging.info(f"Received D/E-phase: '{response_d_or_e}'")

                self.robot_socket.settimeout(None) # Reset to blocking

                if response_r.upper() != "R":
                    return False, f"Robot did not acknowledge (R). Got: {response_r}"
                if response_d_or_e.upper() == "D":
                    return True, f"Command '{command_str}' successful."
                elif response_d_or_e.upper() == "E":
                    return False, f"Command '{command_str}' failed: Robot reported error (E)."
                else:
                    return False, f"Robot did not signal done (D) or error (E). Got: {response_d_or_e}"

            except socket.timeout:
                logging.error(f"Socket timeout during send/recv for command: {command_str}")
                self.is_connected = False # Mark as disconnected on critical error
                if self.robot_socket:
                    try: self.robot_socket.close()
                    except: pass
                self.robot_socket = None
                return False, "Socket timeout"
            except socket.error as e:
                logging.error(f"Socket error during send/recv: {e}")
                self.is_connected = False
                if self.robot_socket:
                    try: self.robot_socket.close()
                    except: pass
                self.robot_socket = None
                return False, f"Socket error: {e}"
            except Exception as e: # Catch any other unexpected error
                logging.error(f"An unexpected error occurred in send_command_raw: {e}", exc_info=True)
                self.is_connected = False
                if self.robot_socket:
                    try: self.robot_socket.close()
                    except: pass
                self.robot_socket = None
                return False, f"Unexpected error: {e}"

    def go_home(self):
        if not self.is_connected:
            logging.warning("go_home called but robot not connected. Frontend should ensure connection first.")
            return False, "Cannot go home. Robot not connected."

        logging.info("Sending robot to home position...")
        x_paper, z_pen_depth, y_paper_side_to_side = config.ROBOT_HOME_POSITION_PY
        cmd_str = self._format_command(x_paper, z_pen_depth, y_paper_side_to_side)
        return self.send_command_raw(cmd_str)

    def move_to_position_py(self, x_py_paper, z_py_depth, y_py_side):
        if not self.is_connected:
            logging.warning("move_to_position_py called but robot not connected.")
            return False, "Cannot move. Robot not connected."
        cmd_str = self._format_command(x_py_paper, z_py_depth, y_py_side)
        return self.send_command_raw(cmd_str)
# --- End RobotInterface Class ---


app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_here!'
BASE_DIR = os.path.dirname(__file__)
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, config.QR_UPLOAD_FOLDER)
app.config['AUDIO_TEMP_FOLDER_PATH'] = os.path.join(BASE_DIR, config.AUDIO_TEMP_FOLDER)
ASSETS_DIR = os.path.join(BASE_DIR, config.ASSETS_FOLDER_NAME)

DRAWING_HISTORY_FILE = os.path.join(BASE_DIR, "drawing_history.json")
MAX_DRAWING_HISTORY = 5


for folder_path in [app.config['UPLOAD_FOLDER'], app.config['AUDIO_TEMP_FOLDER_PATH'], ASSETS_DIR]:
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        logging.info(f"Created folder at: {folder_path}")

SIGNATURE_IMAGE_FULL_PATH = os.path.join(ASSETS_DIR, config.SIGNATURE_IMAGE_FILENAME)

logging.info("--- Initializing AI Models ---")
if load_whisper_model(): logging.info("Whisper model loaded successfully.")
else: logging.error("Whisper model FAILED to load.")
if load_llm_model(): logging.info("LLM model loaded successfully.")
else: logging.error("LLM model FAILED to load.")
logging.info("--- AI Model Initialization Complete ---")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=10 * 1024 * 1024)
robot = RobotInterface()
robot._lock = threading.RLock() # Use RLock for re-entrancy needed in graceful disconnect

current_upload_session_id = None
is_drawing_active_flag = False # Global flag indicating if a drawing thread is active
drawing_history = [] # Global list for drawing history items
active_drawing_session_id = None # ID of the currently active or last active drawing session
drawing_thread = None # Global reference to the drawing thread
drawing_history_lock = threading.Lock() # Lock for synchronizing access to drawing_history list

# --- History and Utility Functions ---
def get_ui_history_summary(history_list):
    ui_summary = []
    for item in history_list:
        total_commands = item.get('total_commands', 0)
        current_index = item.get('current_command_index', 0)
        status = item.get('status', 'unknown')
        progress_val = 0
        if total_commands > 0: progress_val = (current_index / total_commands) * 100
        elif status == 'completed': progress_val = 100
        ui_summary.append({
            'drawing_id': item.get('drawing_id'), 'original_filename': item.get('original_filename'),
            'status': status, 'progress': progress_val, 'last_updated': item.get('last_updated')
        })
    return ui_summary

def save_drawing_history():
    global drawing_history, drawing_history_lock
    with drawing_history_lock:
        try:
            with open(DRAWING_HISTORY_FILE, 'w') as f: json.dump(drawing_history, f, indent=4)
            logging.info(f"Drawing history saved to {DRAWING_HISTORY_FILE}")
        except IOError as e: logging.error(f"Error saving drawing history: {e}")

def load_drawing_history():
    global drawing_history, drawing_history_lock
    with drawing_history_lock:
        if os.path.exists(DRAWING_HISTORY_FILE):
            try:
                with open(DRAWING_HISTORY_FILE, 'r') as f:
                    history_data = json.load(f)
                    if isinstance(history_data, list):
                        valid_history = [s for s in history_data if isinstance(s, dict) and all(k in s for k in ['drawing_id', 'original_filename', 'status'])]
                        drawing_history = valid_history[:MAX_DRAWING_HISTORY]
                        logging.info(f"Drawing history loaded with {len(drawing_history)} entries.")
                    else:
                        drawing_history = []; os.remove(DRAWING_HISTORY_FILE) if os.path.exists(DRAWING_HISTORY_FILE) else None
            except (IOError, json.JSONDecodeError) as e:
                logging.error(f"Error loading drawing history: {e}."); drawing_history = []
                if os.path.exists(DRAWING_HISTORY_FILE): os.remove(DRAWING_HISTORY_FILE)
        else: drawing_history = []

def add_or_update_drawing_in_history(drawing_data_to_add_or_update):
    global drawing_history, active_drawing_session_id, drawing_history_lock
    with drawing_history_lock:
        # Ensure drawing_id exists
        if 'drawing_id' not in drawing_data_to_add_or_update:
            drawing_data_to_add_or_update['drawing_id'] = f"draw_{int(time.time())}_{uuid.uuid4().hex[:6]}"

        drawing_data_to_add_or_update['last_updated'] = datetime.now().isoformat()
        found_index = next((i for i, item in enumerate(drawing_history) if item.get('drawing_id') == drawing_data_to_add_or_update['drawing_id']), -1)

        if found_index != -1:
            drawing_history[found_index] = drawing_data_to_add_or_update
        else:
            drawing_history.insert(0, drawing_data_to_add_or_update)
            drawing_history = drawing_history[:MAX_DRAWING_HISTORY] # Keep history to max size
    # Save after modification (outside this specific lock if save_drawing_history has its own)
    save_drawing_history() # save_drawing_history now handles its own locking
    return drawing_data_to_add_or_update # Return potentially modified data (with ID)

def get_drawing_from_history(drawing_id_to_find):
    global drawing_history, drawing_history_lock
    with drawing_history_lock:
        return next((copy.deepcopy(item) for item in drawing_history if item.get('drawing_id') == drawing_id_to_find), None)


def update_drawing_status_in_history(drawing_id_to_update, new_status, current_cmd_idx=None):
    global drawing_history, active_drawing_session_id, drawing_history_lock
    item_updated = False
    with drawing_history_lock:
        item_to_update = next((item for item in drawing_history if item.get('drawing_id') == drawing_id_to_update), None)
        if item_to_update:
            item_to_update['status'] = new_status
            item_to_update['last_updated'] = datetime.now().isoformat()
            if current_cmd_idx is not None:
                item_to_update['current_command_index'] = current_cmd_idx
            # Update progress based on new status and index
            if item_to_update.get('total_commands', 0) > 0:
                 item_to_update['progress'] = (item_to_update['current_command_index'] / item_to_update['total_commands']) * 100
            elif new_status == 'completed':
                 item_to_update['progress'] = 100
            else:
                 item_to_update['progress'] = 0


            # Clear active_drawing_session_id if the *currently active* drawing is now completed or aborted
            if active_drawing_session_id == drawing_id_to_update and new_status in ["completed", "aborted_manual_override", "aborted_new_drawing", "aborted_new_action", "interrupted_error"]:
                active_drawing_session_id = None # This indicates no drawing session is actively being controlled by a thread
                # is_drawing_active_flag will be set to False by the drawing thread itself upon termination
            item_updated = True
            logging.info(f"Updated status of drawing '{drawing_id_to_update}' to '{new_status}'. Current index: {current_cmd_idx}")
    if item_updated:
        save_drawing_history()
    return item_updated

load_drawing_history()

# --- QR Upload Route ---
UPLOAD_PAGE_TEMPLATE = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Upload Image</title><style>body{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;background-color:#f0f0f0}.container{background-color:white;padding:20px;border-radius:8px;box-shadow:0 0 10px rgba(0,0,0,.1);text-align:center}input[type=file]{margin-bottom:15px;display:block;margin-left:auto;margin-right:auto}button{padding:10px 15px;background-color:#007bff;color:white;border:none;border-radius:4px;cursor:pointer;font-size:1em}button:hover{background-color:#0056b3}#message{margin-top:15px;font-weight:700}h2{margin-top:0}</style></head><body><div class=container><h2>Select Image to Upload</h2><form id=uploadForm method=post enctype=multipart/form-data><input type=file name=image id=imageFile accept=image/* required><button type=submit>Upload</button></form><div id=message></div></div><script>document.getElementById("uploadForm").addEventListener("submit",async function(e){e.preventDefault();const t=new FormData(this),s=document.getElementById("message"),a=this.querySelector('button[type="submit"]'),i=this.querySelector('input[type="file"]');s.textContent="Uploading...",a.disabled=!0,i.disabled=!0;try{const e=await fetch(window.location.href,{method:"POST",body:t}),n=await e.json();e.ok?(s.textContent="Success: "+n.message+". You can close this page.",s.style.color="green"):(s.textContent="Error: "+(n.error||"Upload failed. Please try again."),s.style.color="red",a.disabled=!1,i.disabled=!1)}catch(e){s.textContent="Network Error: "+e.message+". Please try again.",s.style.color="red",a.disabled=!1,i.disabled=!1}})</script></body></html>
"""
@app.route('/qr_upload_page/<session_id>', methods=['GET', 'POST'])
def handle_qr_upload_page(session_id):
    global current_upload_session_id, active_drawing_session_id, is_drawing_active_flag
    if session_id != current_upload_session_id: return "Invalid or expired upload session.", 403
    if request.method == 'POST':
        if 'image' not in request.files: return jsonify({"error": "No image file part"}), 400
        file = request.files['image']
        if file.filename == '': return jsonify({"error": "No selected file"}), 400
        if file:
            original_filename = file.filename; _, f_ext = os.path.splitext(original_filename)
            if f_ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']: return jsonify({"error": "Invalid file type."}), 400
            filename_on_server = str(uuid.uuid4()) + f_ext
            filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
            try:
                file.save(filepath_on_server)
                # If a drawing was active, abort it
                if check_and_abort_active_drawing("New QR image upload"):
                    logging.info("Aborted active drawing due to new QR image upload.")

                socketio.emit('qr_image_received', { 'success': True, 'message': f"Image '{original_filename}' uploaded.", 'original_filename': original_filename, 'filepath_on_server': filepath_on_server})
                socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history)) # Emit updated history
                current_upload_session_id = None
                return jsonify({"message": f"Image '{original_filename}' uploaded successfully!"}), 200
            except Exception as e:
                logging.error(f"Error saving QR uploaded file: {e}", exc_info=True)
                socketio.emit('qr_image_received', {'success': False, 'message': f"Error saving '{original_filename}'.", 'original_filename': original_filename })
                return jsonify({"error": "Failed to save file on server."}), 500
    return render_template_string(UPLOAD_PAGE_TEMPLATE)

# --- SocketIO Event Handlers ---
@socketio.on('connect')
def handle_connect():
    global drawing_history, is_drawing_active_flag, active_drawing_session_id
    logging.info(f"Client connected: {request.sid}")
    emit('response', {'data': 'Connected to Python backend!'})
    # Emit initial status immediately
    emit_current_drawing_status(request.sid) # Use helper to send full status

@socketio.on('client_ready_for_status')
def handle_client_ready_for_status():
    logging.info(f"Client {request.sid} is ready for status (received client_ready_for_status).")
    emit_current_drawing_status(request.sid)

def emit_current_drawing_status(target_sid=None):
    """ Helper function to emit the comprehensive current drawing status. """
    global drawing_history, is_drawing_active_flag, active_drawing_session_id, robot
    
    # Emit robot connection status
    status_payload = {
        'success': robot.is_connected,
        'message': f"Connected to {robot.current_target_host}" if robot.is_connected else 'Not connected to robot'
    }
    if target_sid: emit('robot_connection_status', status_payload, to=target_sid)
    else: emit('robot_connection_status', status_payload)


    # Emit drawing history
    history_summary = get_ui_history_summary(drawing_history)
    if target_sid: emit('drawing_history_updated', history_summary, to=target_sid)
    else: emit('drawing_history_updated', history_summary)


    # Emit current drawing activity status
    current_drawing_session_data = get_drawing_from_history(active_drawing_session_id) if active_drawing_session_id else None

    if is_drawing_active_flag and current_drawing_session_data:
        # A drawing is actively being processed by a thread
        progress = 0
        if current_drawing_session_data.get('total_commands', 0) > 0:
            progress = (current_drawing_session_data.get('current_command_index', 0) / current_drawing_session_data.get('total_commands')) * 100

        drawing_status_payload = {
            'active': True,
            'message': f"Drawing of '{current_drawing_session_data['original_filename']}' is in progress.",
            'resumable': True, # Assumed resumable if active
            'drawing_id': current_drawing_session_data['drawing_id'],
            'original_filename': current_drawing_session_data['original_filename'],
            'progress': progress
        }
    else:
        # No drawing thread is active. Check history for last relevant state.
        last_relevant_drawing = None
        if active_drawing_session_id: # If an ID was set but thread might have just finished
            last_relevant_drawing = get_drawing_from_history(active_drawing_session_id)
        
        if not last_relevant_drawing: # Fallback to find most recent non-completed, or any if all completed
            last_relevant_drawing = next((item for item in drawing_history if item.get('status') != 'completed'), None)
            if not last_relevant_drawing and drawing_history:
                last_relevant_drawing = drawing_history[0] # Show most recent if all are completed

        if last_relevant_drawing:
            status = last_relevant_drawing.get('status', 'unknown')
            progress = 0
            if last_relevant_drawing.get('total_commands', 0) > 0:
                 progress = (last_relevant_drawing.get('current_command_index', 0) / last_relevant_drawing.get('total_commands')) * 100
            elif status == 'completed':
                 progress = 100

            drawing_status_payload = {
                'active': False, # No thread actively drawing
                'message': f"Status of '{last_relevant_drawing['original_filename']}': {status.replace('_', ' ')}.",
                'resumable': status not in ['completed', 'pending_execution', 'pending_restart'], # Resumable if interrupted, etc.
                'drawing_id': last_relevant_drawing['drawing_id'],
                'original_filename': last_relevant_drawing['original_filename'],
                'progress': progress
            }
        else:
            # No history, truly idle
            drawing_status_payload = {'active': False, 'message': 'Idle', 'resumable': False}

    if target_sid: emit('drawing_status_update', drawing_status_payload, to=target_sid)
    else: emit('drawing_status_update', drawing_status_payload)


@socketio.on('disconnect')
def handle_disconnect(): logging.info(f"Client disconnected: {request.sid}")

@socketio.on('robot_connect_request')
def handle_robot_connect_request(data):
    global is_drawing_active_flag
    if is_drawing_active_flag:
        emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'}); return
    use_real = data.get('use_real_robot', config.USE_REAL_ROBOT_DEFAULT)
    success, message = robot.connect_robot(use_real=use_real)
    emit('robot_connection_status', {'success': success, 'message': message})

@socketio.on('robot_disconnect_request')
def handle_robot_disconnect_request(json_data):
    global is_drawing_active_flag
    if is_drawing_active_flag:
        emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'}); return
    success, message = robot.disconnect_robot(graceful=True)
    # After disconnect, robot.is_connected should be false
    emit('robot_connection_status', {'success': robot.is_connected, 'message': message})


def check_and_abort_active_drawing(command_description="Manual command"):
    global active_drawing_session_id, is_drawing_active_flag, drawing_history, drawing_thread
    if is_drawing_active_flag and active_drawing_session_id:
        logging.warning(f"{command_description} received, attempting to abort active drawing '{active_drawing_session_id}'.")
        # is_drawing_active_flag will be set to False by the drawing thread when it notices and exits.
        # We update the history status here.
        update_drawing_status_in_history(active_drawing_session_id, "aborted_manual_override")
        # The drawing thread should detect this status or a flag and terminate.
        # For immediate UI feedback:
        emit('drawing_status_update', {'active': False, 'message': f"Drawing aborted due to {command_description}.", 'resumable': True, 'drawing_id': active_drawing_session_id, 'progress': get_drawing_from_history(active_drawing_session_id).get('progress',0) if get_drawing_from_history(active_drawing_session_id) else 0})
        emit('drawing_history_updated', get_ui_history_summary(drawing_history))
        # is_drawing_active_flag = False # Do not set this here. Thread should manage it.
        # active_drawing_session_id = None # This is cleared by update_drawing_status if appropriate
        return True
    return False

@socketio.on('send_robot_command')
def handle_send_robot_command(json_data, triggered_by_llm=False):
    # No direct drawing here, so is_drawing_active_flag isn't strictly needed
    # but good for consistency if LLM could trigger complex sequences later.
    # For simple moves, it's fine even if a drawing thread *was* active but now completed.
    # The main check is robot.is_connected.

    command_type = json_data.get('type', 'raw'); command_str = json_data.get('command_str')
    current_drawing_is_active = is_drawing_active_flag # Snapshot

    if current_drawing_is_active:
        if triggered_by_llm:
            if check_and_abort_active_drawing(f"LLM command '{command_type}'"):
                logging.info(f"LLM command '{command_type}' aborted an active drawing.")
                # Wait a brief moment for the abortion to potentially take effect before proceeding with the new command
                socketio.sleep(0.2)
        else: # Manual command during active drawing
            emit('command_response', {'success': False, 'message': 'Drawing active. Cannot send manual commands now.', 'command_sent': command_type})
            return False, "Drawing active."
    elif not triggered_by_llm: # Manual command, no drawing active. Check if a previous *different* drawing needs to be marked aborted if it was interruptible.
        # This logic might be too aggressive if user is just sending manual commands after a normal completion.
        # Let's only abort if a resumable (interrupted) session was the *last* active one and this is a new action.
        # For simplicity, this check is removed as drawing_active_flag is the main gatekeeper.
        pass


    if not robot.is_connected: # Check connection for ANY command type
        conn_success, conn_message = robot.connect_robot(use_real=config.USE_REAL_ROBOT_DEFAULT)
        if not conn_success:
            if not triggered_by_llm: emit('command_response', {'success': False, 'message': f'Robot not connected & conn failed: {conn_message}', 'command_sent': command_type})
            return False, f'Robot not connected & conn failed: {conn_message}'
        emit('robot_connection_status', {'success': True, 'message': conn_message})

    success, message = False, "Invalid command"; actual_command_sent_display = command_type
    if command_type == 'go_home':
        success, message = robot.go_home()
        x_h, z_h, y_h = config.ROBOT_HOME_POSITION_PY; actual_command_sent_display = robot._format_command(x_h, z_h, y_h) + " (Home)"
    elif command_type == 'move_to_safe_center':
        x_s, z_s, y_s = config.SAFE_ABOVE_CENTER_PY; success, message = robot.move_to_position_py(x_s, z_s, y_s)
        actual_command_sent_display = robot._format_command(x_s, z_s, y_s) + " (Safe Center)"
    elif command_type == 'raw' and command_str:
        success, message = robot.send_command_raw(command_str); actual_command_sent_display = command_str

    if not triggered_by_llm: emit('command_response', {'success': success, 'message': message, 'command_sent': actual_command_sent_display})
    # After command, re-emit robot status if it might have changed (e.g., due to error in send_command_raw)
    if not robot.is_connected: emit('robot_connection_status', {'success': False, 'message': 'Disconnected (after command attempt)'})
    return success, message


@socketio.on('direct_image_upload')
def handle_direct_image_upload(data):
    if check_and_abort_active_drawing("New direct image upload"):
        logging.info("Aborted active drawing due to new direct image upload.")

    original_filename = data.get('filename'); base64_data = data.get('fileData')
    if not original_filename or not base64_data: emit('direct_image_upload_response', {'success': False, 'message': 'Missing data.'}); return
    try:
        image_data = base64.b64decode(base64_data); _, f_ext = os.path.splitext(original_filename)
        if f_ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']: emit('direct_image_upload_response', {'success': False, 'message': f"Invalid type: {f_ext}"}); return
        filename_on_server = str(uuid.uuid4()) + f_ext; filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
        with open(filepath_on_server, 'wb') as f: f.write(image_data)
        emit('direct_image_upload_response', { 'success': True, 'message': f"Image '{original_filename}' uploaded.", 'original_filename': original_filename, 'filepath_on_server': filepath_on_server })
        emit('drawing_history_updated', get_ui_history_summary(drawing_history)) # Emit updated history
    except Exception as e: logging.error(f"Error in direct_image_upload: {e}", exc_info=True); emit('direct_image_upload_response', {'success': False, 'message': f"Server error: {e}"})

@socketio.on('audio_chunk')
def handle_audio_chunk(data):
    audio_data_b64 = data.get('audioData'); mime_type = data.get('mimeType', 'audio/webm')
    if not audio_data_b64: emit('transcription_result', {'error': 'No audio data.'}); return
    try:
        audio_bytes = base64.b64decode(audio_data_b64); file_extension = ".webm"
        if 'wav' in mime_type.lower(): file_extension = ".wav"
        elif 'mp3' in mime_type.lower(): file_extension = ".mp3"
        temp_audio_filename = f"voice_cmd_{uuid.uuid4()}{file_extension}"; temp_audio_filepath = os.path.join(app.config['AUDIO_TEMP_FOLDER_PATH'], temp_audio_filename)
        with open(temp_audio_filepath, 'wb') as f: f.write(audio_bytes)
        transcribed_text = transcribe_audio(temp_audio_filepath)
        if transcribed_text is not None: emit('transcription_result', {'text': transcribed_text})
        else: emit('transcription_result', {'error': 'Transcription failed.'})
        try: os.remove(temp_audio_filepath)
        except Exception as e: logging.warning(f"Error removing temp audio: {e}")
    except Exception as e: logging.error(f"Error processing audio: {e}", exc_info=True); emit('transcription_result', {'error': f'Server error processing audio.'})

@socketio.on('submit_text_to_llm')
def handle_submit_text_to_llm(data):
    text_command = data.get('text_command')
    if not text_command: emit('llm_response_chunk', {'error': 'No text command.', 'done': True}); return
    parsed_action_command_from_llm = None
    try:
        for llm_response_part in process_command_with_llm_stream(text_command):
            emit('llm_response_chunk', llm_response_part)
            if llm_response_part.get("done"):
                if llm_response_part.get("parsed_action"): parsed_action_command_from_llm = llm_response_part["parsed_action"]
                break
        if parsed_action_command_from_llm:
            action_type = parsed_action_command_from_llm.get("type"); parameters = parsed_action_command_from_llm.get("parameters", {})
            if action_type in ["move", "move_to_coords"]:
                 if check_and_abort_active_drawing(f"LLM command '{action_type}'"): logging.info(f"LLM command '{action_type}' aborted drawing.")
            if action_type == "move":
                target = parameters.get("target")
                if target == "home": handle_send_robot_command({'type': 'go_home'}, triggered_by_llm=True)
                elif target == "center": handle_send_robot_command({'type': 'move_to_safe_center'}, triggered_by_llm=True)
            elif action_type == "move_to_coords":
                x, y_side, z_depth = parameters.get("x"), parameters.get("y"), parameters.get("z")
                if x is not None and y_side is not None and z_depth is not None:
                    cmd_str_llm = robot._format_command(x, z_depth, y_side) # x, depth, side
                    handle_send_robot_command({'type': 'raw', 'command_str': cmd_str_llm}, triggered_by_llm=True)
            elif action_type == "draw_uploaded_image":
                # Find the most recently uploaded image from history or direct upload path
                # This logic should ideally be more robust, e.g., by having LLM confirm which image.
                # For now, assume the last successfully uploaded one.
                if uploaded_filepath_from_backend: # This state is set by 'direct_image_upload_response' or 'qr_image_received'
                    original_filename_for_draw = "uploaded_image" # Placeholder
                    # Try to get original filename from history if possible or lastUploadedImageInfo
                    recent_uploads = [item for item in drawing_history if item.get('filepath_on_server') == uploaded_filepath_from_backend]
                    if recent_uploads: original_filename_for_draw = recent_uploads[0].get('original_filename', "uploaded_image")
                    
                    logging.info(f"LLM triggered 'draw_uploaded_image' for: {uploaded_filepath_from_backend}")
                    # Re-use existing process_image_for_drawing logic
                    # Need to decide on Canny thresholds - use defaults or allow LLM to suggest? For now, defaults.
                    default_t1 = config.DEFAULT_CANNY_THRESHOLD1
                    default_t2 = config.DEFAULT_CANNY_THRESHOLD2
                    
                    handle_process_image_for_drawing({
                        'filepath': uploaded_filepath_from_backend,
                        'original_filename': original_filename_for_draw,
                        'canny_t1': default_t1,
                        'canny_t2': default_t2
                    })
                else:
                    logging.warning("LLM requested 'draw_uploaded_image' but no uploadedFilePathFromBackend is set.")
                    emit('command_response', {'success': False, 'message': "LLM asked to draw, but I don't have an image path. Please upload first."})


    except Exception as e: logging.error(f"API Error in LLM handler: {e}", exc_info=True); emit('llm_response_chunk', {'error': f'Server error: {e}', 'done': True})

@socketio.on('send_custom_coordinates')
def handle_send_custom_coordinates_event(data):
    if check_and_abort_active_drawing("Manual coordinate input"):
        logging.info("Aborted active drawing due to manual coordinate input.")
        socketio.sleep(0.2) # Brief pause if abortion happened

    if not robot.is_connected: emit('command_response', {'success': False, 'message': 'Robot not connected.'}); return
    try:
        x_val, z_val, y_val = float(data.get('x_py')), float(data.get('z_py')), float(data.get('y_py'))
        success, message = robot.move_to_position_py(x_val, z_val, y_val) # x, depth, side
        emit('command_response', {'success': success, 'message': message, 'command_sent': f'Custom: X={x_val}, Depth={z_val}, Side={y_val}'})
        if not robot.is_connected: emit('robot_connection_status', {'success': False, 'message': 'Disconnected'})
    except Exception as e: logging.error(f"API Error custom coords: {e}", exc_info=True); emit('command_response', {'success': False, 'message': f'Server error: {e}'})

@socketio.on('request_threshold_preview')
def handle_request_threshold_preview(data):
    filepath, t1, t2 = data.get('filepath'), data.get('t1'), data.get('t2')
    if not filepath or not os.path.exists(filepath) or t1 is None or t2 is None: emit('threshold_preview_image_response', {'error': 'Invalid data.'}); return
    try:
        edges_array = get_canny_edges_array(filepath, int(t1), int(t2))
        if edges_array is not None: _, buffer = cv2.imencode('.png', edges_array); img_base64 = base64.b64encode(buffer).decode('utf-8'); emit('threshold_preview_image_response', {'image_base64': img_base64})
        else: emit('threshold_preview_image_response', {'error': 'Failed to generate preview.'})
    except Exception as e: logging.error(f"API Error threshold preview: {e}", exc_info=True); emit('threshold_preview_image_response', {'error': f'Server error: {e}'})


# --- Drawing Execution ---
def _drawing_thread_target(app_context, robot_instance, commands, start_idx, total_cmds,
                           original_fname, drawing_id_from_main_thread, socketio_instance):
    """Target function for the drawing thread."""
    global is_drawing_active_flag, active_drawing_session_id, drawing_history, drawing_history_lock

    current_command_index_in_thread = start_idx # Local counter for this thread's execution of its command segment

    with app_context: # Important for SocketIO emits from thread
        try:
            # Get the specific drawing session data from global history to update its index
            # This is crucial for reconnections to get correct live progress
            session_data_in_global_history = None
            with drawing_history_lock:
                session_data_in_global_history = next((item for item in drawing_history if item.get('drawing_id') == drawing_id_from_main_thread), None)

            if not session_data_in_global_history:
                logging.error(f"Drawing thread: Could not find session '{drawing_id_from_main_thread}' in global history. Aborting thread.")
                # No need to update is_drawing_active_flag or active_drawing_session_id here, as main thread handles it
                return

            for i in range(start_idx, total_cmds): # Iterate through the full command list or remaining part
                if not is_drawing_active_flag: # Check if main thread signalled an abort
                    logging.info(f"Drawing thread for '{original_fname}' (ID: {drawing_id_from_main_thread}) detected abort signal (is_drawing_active_flag is False).")
                    # Status should have been updated by the aborting function (e.g., check_and_abort_active_drawing)
                    return # Exit thread

                # Update current_command_index in the shared global history item
                # This allows reconnections to pick up the true live progress
                if session_data_in_global_history:
                    with drawing_history_lock: # Protect access to the shared history item
                        session_data_in_global_history['current_command_index'] = i
                        session_data_in_global_history['last_updated'] = datetime.now().isoformat()
                        # Optionally, calculate progress here if needed for other backend logic,
                        # but frontend will calculate from index/total.
                    # No need to call save_drawing_history() on every command due to performance.
                    # It will be saved on status changes like completion or interruption.

                current_command_index_in_thread = i # Update local index for this iteration
                x_py, z_py_depth, y_py_side = commands[i]
                formatted_cmd_str = robot_instance._format_command(x_py, z_py_depth, y_py_side)

                progress_msg = f"Drawing '{original_fname}': Cmd {i+1}/{total_cmds}"
                progress_percentage = ((i + 1) / total_cmds) * 100 if total_cmds > 0 else 0

                socketio_instance.emit('drawing_status_update', {
                    'active': True, 'message': progress_msg,
                    'progress': progress_percentage, 'resumable': True,
                    'drawing_id': drawing_id_from_main_thread, 'original_filename': original_fname
                })

                success, msg = robot_instance.send_command_raw(formatted_cmd_str)
                if not success:
                    logging.error(f"Drawing '{original_fname}' failed at command {i+1}: {msg}")
                    update_drawing_status_in_history(drawing_id_from_main_thread, "interrupted", i) # Save current progress
                    socketio_instance.emit('drawing_status_update', {
                        'active': False, 'message': f"Drawing of '{original_fname}' interrupted: {msg}. Ready to resume.",
                        'resumable': True, 'drawing_id': drawing_id_from_main_thread, 'original_filename': original_fname,
                        'progress': (i / total_cmds) * 100 if total_cmds > 0 else 0
                    })
                    # The finally block will handle is_drawing_active_flag
                    return # Exit thread on error

                # time.sleep(0.005) # Optional small delay if needed, but usually not for drawing

            # Drawing completed successfully
            update_drawing_status_in_history(drawing_id_from_main_thread, "completed", total_cmds)
            socketio_instance.emit('command_response', {'success': True, 'message': f"Sent all {total_cmds} commands for '{original_fname}'."})
            socketio_instance.emit('drawing_status_update', {
                'active': False, 'message': f"Drawing of '{original_fname}' complete.",
                'resumable': False, 'drawing_id': drawing_id_from_main_thread, 'progress': 100 # Ensure 100% on complete
            })
            robot_instance.go_home()

        except Exception as e:
            logging.error(f"Exception in drawing thread for '{original_fname}': {e}", exc_info=True)
            # Use the loop's current_command_index_in_thread for error reporting
            update_drawing_status_in_history(drawing_id_from_main_thread, "interrupted_error", current_command_index_in_thread)
            error_progress = (current_command_index_in_thread / total_cmds) * 100 if total_cmds > 0 else 0
            socketio_instance.emit('command_response', {'success': False, 'message': f"Error during drawing: {e}"})
            socketio_instance.emit('drawing_status_update', {
                'active': False, 'message': f"Drawing of '{original_fname}' failed with server error. Ready to resume.",
                'resumable': True, 'drawing_id': drawing_id_from_main_thread, 'original_filename': original_fname,
                'progress': error_progress
            })
        finally:
            # This block runs whether the drawing completes, errors, or is aborted (if abort check is effective)
            # Crucially, set is_drawing_active_flag to False as the thread is now ending.
            if active_drawing_session_id == drawing_id_from_main_thread: # Only if this was the one marked active globally
                is_drawing_active_flag = False
                # active_drawing_session_id is cleared by update_drawing_status_in_history for "completed" or errors
                # or should be cleared if the thread itself is what clears is_drawing_active_flag.
                # If an abort happened, the abort function might have cleared active_drawing_session_id.
                # It's safer to also check and clear it here if the flag implies it was the active session.
                # active_drawing_session_id = None # This is handled by update_drawing_status_in_history for terminal states

            logging.info(f"Drawing thread for '{original_fname}' (ID: {drawing_id_from_main_thread}) finished. is_drawing_active_flag is now False.")
            socketio_instance.emit('drawing_history_updated', get_ui_history_summary(drawing_history))
            # Emit a final status update to ensure UI consistency, especially if it was an error
            # or if the 'completed' one might have been missed.
            final_session_state = get_drawing_from_history(drawing_id_from_main_thread)
            if final_session_state:
                socketio_instance.emit('drawing_status_update', {
                    'active': False, # Thread is done
                    'message': f"Session '{final_session_state.get('original_filename')}' ended with status: {final_session_state.get('status', 'unknown').replace('_',' ')}.",
                    'resumable': final_session_state.get('status') not in ['completed', 'pending_execution', 'pending_restart'],
                    'drawing_id': final_session_state.get('drawing_id'),
                    'original_filename': final_session_state.get('original_filename'),
                    'progress': final_session_state.get('progress', 100 if final_session_state.get('status') == 'completed' else 0)
                })
            else: # Fallback if somehow removed from history
                 socketio_instance.emit('drawing_status_update', {'active': False, 'message': 'Drawing session ended.', 'resumable': False, 'drawing_id': drawing_id_from_main_thread})



def _execute_drawing_commands(drawing_session_id_to_execute):
    global is_drawing_active_flag, active_drawing_session_id, drawing_history, drawing_thread

    session_data = get_drawing_from_history(drawing_session_id_to_execute)
    if not session_data:
        logging.error(f"Drawing session {drawing_session_id_to_execute} not found for execution.")
        # Emit error to frontend if session not found
        socketio.emit('drawing_status_update', {'active': False, 'message': "Error: Drawing session not found.", 'resumable': False, 'drawing_id': drawing_session_id_to_execute})
        return

    # Critical section: Set global flags before starting the thread
    is_drawing_active_flag = True
    active_drawing_session_id = drawing_session_id_to_execute # Set the global active ID

    original_filename = session_data['original_filename']
    commands_to_execute_tuples = list(session_data.get('robot_commands_tuples', [])) # Ensure it's a list

    if session_data.get('is_image_drawing', True):
        if not os.path.exists(SIGNATURE_IMAGE_FULL_PATH):
            logging.error(f"Signature image not found: {SIGNATURE_IMAGE_FULL_PATH}.")
        else:
            try:
                signature_robot_commands = process_image_to_robot_commands_pipeline(
                    SIGNATURE_IMAGE_FULL_PATH, config.SIGNATURE_CANNY_THRESHOLD1, config.SIGNATURE_CANNY_THRESHOLD2, optimize=True)
                if signature_robot_commands:
                    commands_to_execute_tuples.extend(signature_robot_commands)
                    # Update total_commands in the session_data (and thus in history when updated)
                    session_data['total_commands'] = len(commands_to_execute_tuples)
                    # This change to session_data needs to be reflected in the global drawing_history
                    # Update the history item with the new total_commands before starting the thread
                    add_or_update_drawing_in_history(copy.deepcopy(session_data)) # Ensure it's a deepcopy if needed
            except Exception as sig_e:
                logging.error(f"Error processing signature: {sig_e}")

    start_index = session_data.get('current_command_index', 0)
    total_commands_in_session = session_data.get('total_commands', len(commands_to_execute_tuples)) # Use updated total

    if total_commands_in_session == 0 and not commands_to_execute_tuples : # No commands even after potential signature
        logging.warning(f"No commands to execute for '{original_filename}'. Aborting drawing.")
        update_drawing_status_in_history(active_drawing_session_id, "aborted_no_commands", 0)
        socketio.emit('drawing_status_update', {'active': False, 'message': f"No drawing commands generated for '{original_filename}'.", 'resumable': False, 'drawing_id': active_drawing_session_id})
        is_drawing_active_flag = False; active_drawing_session_id = None
        socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history))
        return


    logging.info(f"Preparing to execute/resume drawing '{original_filename}' (ID: {active_drawing_session_id}) from command {start_index + 1}/{total_commands_in_session}")
    new_status = "in_progress_resumed" if start_index > 0 else "in_progress"
    update_drawing_status_in_history(active_drawing_session_id, new_status, start_index)
    socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history)) # Emit updated history

    try:
        if not robot.is_connected:
            use_real = config.USE_REAL_ROBOT_DEFAULT
            if robot.current_target_host == config.REAL_ROBOT_HOST: use_real = True
            conn_success, conn_msg = robot.connect_robot(use_real=use_real)
            if not conn_success:
                socketio.emit('command_response', {'success': False, 'message': f"Robot connection failed: {conn_msg}"})
                is_drawing_active_flag = False; active_drawing_session_id = None
                update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted_robot_connection_failed", start_index)
                socketio.emit('drawing_status_update', {'active': False, 'message': f"Drawing interrupted (robot conn failed).", 'resumable': True, 'drawing_id': drawing_session_id_to_execute})
                socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history))
                return
            socketio.emit('robot_connection_status', {'success': True, 'message': conn_msg})

        if start_index == 0: # Only move to safe start if it's a fresh drawing
            safe_x, safe_z_depth, safe_y_side = config.SAFE_ABOVE_CENTER_PY
            success_safe, msg_safe = robot.move_to_position_py(safe_x, safe_z_depth, safe_y_side)
            if not success_safe:
                is_drawing_active_flag = False; active_drawing_session_id = None
                update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted_safe_start_failed", 0)
                socketio.emit('drawing_status_update', {'active': False, 'message': f"Drawing aborted (safe start failed: {msg_safe}).", 'resumable': True, 'drawing_id': drawing_session_id_to_execute})
                socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history))
                return

        # The thread will use drawing_session_id_to_execute as drawing_id_from_main_thread
        drawing_thread = threading.Thread(
            target=_drawing_thread_target,
            args=(
                app.app_context(),
                robot,
                commands_to_execute_tuples, # Pass the full list
                start_index,                # Where to start in that list
                total_commands_in_session,  # Total commands (including signature if added)
                original_filename,
                drawing_session_id_to_execute, # This is the ID the thread should use
                socketio
            )
        )
        drawing_thread.daemon = True
        drawing_thread.start()

    except Exception as e:
        logging.error(f"Error setting up drawing execution for '{original_filename}': {e}", exc_info=True)
        is_drawing_active_flag = False
        active_drawing_session_id = None
        update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted_error_setup", start_index)
        socketio.emit('command_response', {'success': False, 'message': f"Error setting up drawing: {e}"})
        socketio.emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' failed setup.", 'resumable': True, 'drawing_id': drawing_session_id_to_execute})
        socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history))


@socketio.on('process_image_for_drawing')
def handle_process_image_for_drawing(data):
    global is_drawing_active_flag, active_drawing_session_id # Ensure these are managed

    if is_drawing_active_flag:
        socketio.emit('command_response', {'success': False, 'message': "Another drawing is already active. Cannot start new one."})
        return

    # If there was a previous active_drawing_session_id that was perhaps interrupted,
    # mark it as aborted because a new drawing is being processed.
    if active_drawing_session_id:
        update_drawing_status_in_history(active_drawing_session_id, "aborted_new_drawing")
    active_drawing_session_id = None # Reset for the new drawing

    filepath = data.get('filepath'); original_filename = data.get('original_filename', os.path.basename(filepath or "unknown_image"))
    if not filepath or not os.path.exists(filepath):
        socketio.emit('command_response', {'success': False, 'message': f"File not found: {filepath}"})
        return

    canny_t1, canny_t2 = data.get('canny_t1', config.DEFAULT_CANNY_THRESHOLD1), data.get('canny_t2', config.DEFAULT_CANNY_THRESHOLD2)

    # Emit initial processing status BEFORE starting the potentially long image processing
    socketio.emit('drawing_status_update', {'active': True, 'message': f"Processing '{original_filename}' for drawing...", 'resumable': False, 'progress': 0})
    # is_drawing_active_flag = True # Set this when _execute_drawing_commands is called and thread starts

    try:
        robot_commands_tuples = process_image_to_robot_commands_pipeline(filepath, canny_t1, canny_t2)
        if not robot_commands_tuples:
            socketio.emit('command_response', {'success': False, 'message': f"No drawing commands could be generated for '{original_filename}'."})
            socketio.emit('drawing_status_update', {'active': False, 'message': f"Failed to process '{original_filename}'. No commands.", 'resumable': False})
            # is_drawing_active_flag = False # Reset if processing fails before drawing starts
            return

        drawing_id = f"draw_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        new_drawing_data = {
            'drawing_id': drawing_id, 'filepath_on_server': filepath, 'original_filename': original_filename,
            'robot_commands_tuples': robot_commands_tuples, 'current_command_index': 0,
            'total_commands': len(robot_commands_tuples), 'canny_t1': canny_t1, 'canny_t2': canny_t2,
            'status': 'pending_execution', 'timestamp': datetime.now().isoformat(), 'is_image_drawing': True
        }
        add_or_update_drawing_in_history(new_drawing_data) # Add to history before execution
        socketio.emit('drawing_status_update', {'active': True, 'message': f"Generated {len(robot_commands_tuples)} commands for '{original_filename}'. Preparing to draw.",
                                       'resumable': True, 'drawing_id': drawing_id, 'original_filename': original_filename, 'progress': 0})
        socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history))

        _execute_drawing_commands(drawing_id) # This will set is_drawing_active_flag and active_drawing_session_id

    except Exception as e:
        logging.error(f"Error processing for drawing '{original_filename}': {e}", exc_info=True)
        socketio.emit('command_response', {'success': False, 'message': f"Error processing image: {e}"})
        socketio.emit('drawing_status_update', {'active': False, 'message': f"Error processing '{original_filename}'.", 'resumable': False})
        # Ensure flags are reset if setup fails before thread creation attempt
        if active_drawing_session_id: # If it was set, means it failed during _execute_drawing_commands setup
            update_drawing_status_in_history(active_drawing_session_id, "interrupted_error_processing", 0)
            # active_drawing_session_id = None # _execute_drawing_commands handles this on its internal error
        # is_drawing_active_flag = False # _execute_drawing_commands handles this


@socketio.on('resume_drawing_request')
def handle_resume_drawing_request(data):
    global is_drawing_active_flag, active_drawing_session_id
    drawing_id_to_resume = data.get('drawing_id')
    if is_drawing_active_flag:
        socketio.emit('drawing_status_update', {'active': True, 'message': "Another drawing is already active. Cannot resume."}); return

    session_to_resume = get_drawing_from_history(drawing_id_to_resume)
    if session_to_resume and session_to_resume.get('status') != 'completed':
        logging.info(f"Resuming drawing: {drawing_id_to_resume} from index {session_to_resume.get('current_command_index',0)}")
        # Set active_drawing_session_id here BEFORE calling _execute_drawing_commands
        # active_drawing_session_id = drawing_id_to_resume # _execute_drawing_commands will set this
        # is_drawing_active_flag = True # _execute_drawing_commands will set this
        progress_on_resume = 0
        if session_to_resume.get('total_commands',0) > 0 :
            progress_on_resume = (session_to_resume.get('current_command_index',0) / session_to_resume['total_commands']) * 100

        socketio.emit('drawing_status_update', {
            'active': True, # Optimistically set to true, _execute will manage from here
            'message': f"Resuming drawing of '{session_to_resume['original_filename']}'...",
            'resumable': True, 'drawing_id': drawing_id_to_resume, 'original_filename': session_to_resume['original_filename'],
            'progress': progress_on_resume
        })
        _execute_drawing_commands(drawing_id_to_resume)
    else:
        socketio.emit('drawing_status_update', {'active': False, 'message': "Drawing session not found or already completed.", 'resumable': False})
        logging.warning(f"Could not resume drawing {drawing_id_to_resume}. Session not found or completed.")

@socketio.on('restart_drawing_request')
def handle_restart_drawing_request(data):
    global is_drawing_active_flag, active_drawing_session_id
    drawing_id_to_restart = data.get('drawing_id')
    if is_drawing_active_flag:
        socketio.emit('drawing_status_update', {'active': True, 'message': "Another drawing is already active. Cannot restart."}); return

    session_to_restart = get_drawing_from_history(drawing_id_to_restart)
    if session_to_restart:
        logging.info(f"Restarting drawing: {drawing_id_to_restart}")
        # Reset progress for the restart
        session_to_restart['current_command_index'] = 0
        session_to_restart['status'] = 'pending_restart' # Mark as pending restart
        add_or_update_drawing_in_history(session_to_restart.copy()) # Save the reset state

        # active_drawing_session_id = drawing_id_to_restart # _execute_drawing_commands will set this
        # is_drawing_active_flag = True # _execute_drawing_commands will set this
        socketio.emit('drawing_status_update', {
            'active': True, # Optimistically set to true
            'message': f"Restarting drawing of '{session_to_restart['original_filename']}'...",
            'resumable': True, 'drawing_id': drawing_id_to_restart, 'original_filename': session_to_restart['original_filename'], 'progress': 0
        })
        socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history))
        _execute_drawing_commands(drawing_id_to_restart)
    else:
        socketio.emit('drawing_status_update', {'active': False, 'message': "Drawing session to restart not found.", 'resumable': False})
        logging.warning(f"Could not restart drawing {drawing_id_to_restart}. Session not found.")


if __name__ == '__main__':
    server_port = 5555
    app.config['SERVER_PORT'] = server_port
    logging.info(f"Starting Python backend server (SocketIO with Flask) on port {server_port}...")
    socketio.run(app, host='0.0.0.0', port=server_port, debug=True, use_reloader=False)

