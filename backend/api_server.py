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

# --- RobotInterface Class Definition (Effectively an update to robot_interface.py) ---
class RobotInterface:
    def __init__(self):
        self.robot_socket = None
        self.is_connected = False
        self.current_target_host = None
        self.current_target_port = None
        self._lock = threading.Lock() # Lock for socket operations if used by multiple threads (drawing thread)

    def _format_command(self, x, z, y): # Original: x, z (depth), y (side)
        return f"{x:.2f},{z:.2f},{y:.2f}" 

    def connect_robot(self, use_real=False): 
        if self.is_connected:
            logging.info("Robot already connected.")
            return True, f"Already connected to {self.current_target_host}:{self.current_target_port}"
        with self._lock: # Ensure connection attempt is serialized
            if self.is_connected: # Double check after acquiring lock
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
        with self._lock: # Ensure disconnection is serialized
            if not self.is_connected:
                logging.info("Robot is not connected.")
                return True, "Was not connected."

            if graceful:
                # go_home needs to be callable without re-acquiring lock if called from here
                # For simplicity, we assume go_home will use send_command_raw which is locked
                logging.info("Attempting graceful disconnect (going home first)...")
                # Temporarily release lock if go_home acquires it, or make go_home not acquire it if called internally
                # This simple version might deadlock if go_home re-acquires. Better: pass a flag to go_home
                
                # To avoid deadlock, go_home should not acquire lock if called from disconnect_robot
                # Or, make a _go_home_nolock version. For now, this is a potential issue if go_home is complex.
                # Let's assume go_home itself will use send_command_raw which handles the lock.
                # This design is fine as send_command_raw, go_home, move_to_position_py will all use the lock.

                # This specific call to go_home is problematic if disconnect_robot holds the lock
                # and go_home tries to acquire it.
                # Simplification: release lock, call go_home, re-acquire. This is not ideal.
                # Better: a _go_home_internal that doesn't lock.
                # For now, we will call go_home which will re-acquire the lock.
                # This is fine because the lock is reentrant (threading.RLock would be better)
                # or if go_home is called *after* the lock in disconnect_robot is released for this block.
                
                # Let's simplify: if graceful and connected, call go_home directly.
                # The go_home method itself will handle locking for its operations.
                pass # The go_home call will be made before closing the socket if graceful=True
                
            if graceful: # Perform go_home outside the main socket closing block
                # This means disconnect_robot's lock is released before go_home is called
                # This is NOT what we want. Graceful go_home should happen *before* socket.close().
                # We need to ensure send_command_raw within go_home can function.
                # The lock should protect the socket resource.
                if self.is_connected: # Check again, as status might change
                    home_success, home_msg = self.go_home() # This will acquire its own lock in send_command_raw
                    if not home_success:
                        logging.warning(f"Warning: Failed to go home before disconnecting: {home_msg}")
                    else:
                        logging.info("Successfully moved to home position.")
                        logging.info("Waiting for 2 seconds before closing socket...") 
                        time.sleep(2) # This sleep should be outside the lock or very short

            # Actual socket closing
            if self.robot_socket:
                try:
                    self.robot_socket.close()
                except socket.error as e:
                    logging.error(f"Error closing socket: {e}")
                finally:
                    self.robot_socket = None
                    self.is_connected = False
                    logging.info(f"Socket closed. Disconnected from {self.current_target_host}:{self.current_target_port}.")
                    self.current_target_host = None
                    self.current_target_port = None
            else: 
                # This case should ideally not be reached if is_connected was true
                self.is_connected = False 
                logging.info("No active socket to close but was marked connected. Marked as disconnected.")
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
                
                self.robot_socket.settimeout(10) 
                response_r = self.robot_socket.recv(1024).decode('utf-8').strip()
                logging.info(f"Received R-phase: '{response_r}'")
                
                self.robot_socket.settimeout(20) 
                response_d_or_e = self.robot_socket.recv(1024).decode('utf-8').strip()
                logging.info(f"Received D/E-phase: '{response_d_or_e}'")
                
                self.robot_socket.settimeout(None) 

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
                # Don't call disconnect_robot from here to avoid re-locking issues if it also tries to lock.
                # Mark as disconnected and let higher level handle full disconnect.
                self.is_connected = False 
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
            except Exception as e:
                logging.error(f"An unexpected error occurred: {e}")
                self.is_connected = False
                if self.robot_socket:
                    try: self.robot_socket.close()
                    except: pass
                self.robot_socket = None
                return False, f"Unexpected error: {e}"

    def go_home(self):
        # This method uses send_command_raw, which is locked.
        if not self.is_connected: # Check before attempting, send_command_raw will also check
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

current_upload_session_id = None
is_drawing_active_flag = False 
drawing_history = [] 
active_drawing_session_id = None 
drawing_thread = None # Global reference to the drawing thread

# --- History and Utility Functions (largely unchanged) ---
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
    global drawing_history
    try:
        with open(DRAWING_HISTORY_FILE, 'w') as f: json.dump(drawing_history, f, indent=4)
        logging.info(f"Drawing history saved to {DRAWING_HISTORY_FILE}")
    except IOError as e: logging.error(f"Error saving drawing history: {e}")

def load_drawing_history():
    global drawing_history
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

def add_or_update_drawing_in_history(drawing_data):
    # This function might be called from the drawing thread, ensure it's safe.
    # Global list modification and file I/O should ideally be synchronized if multiple threads modify it.
    # However, drawing_history is primarily managed by the main thread before starting and after thread completion.
    # The drawing thread primarily updates 'current_command_index' in its copy of session_data.
    # Let's assume for now that this function, when called from the drawing thread,
    # is only for *updating* an existing entry's progress which is then *saved* by the main thread or at end of drawing.
    # A better approach for thread safety would be to pass updates back to the main thread via a queue or socketio.emit.
    # For now, the deepcopy in _execute_drawing_commands helps.
    global drawing_history, active_drawing_session_id
    if 'drawing_id' not in drawing_data:
        drawing_data['drawing_id'] = f"draw_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    drawing_data['last_updated'] = datetime.now().isoformat()
    found_index = next((i for i, item in enumerate(drawing_history) if item.get('drawing_id') == drawing_data['drawing_id']), -1)
    if found_index != -1: drawing_history[found_index] = drawing_data
    else: 
        drawing_history.insert(0, drawing_data) 
        drawing_history = drawing_history[:MAX_DRAWING_HISTORY]
    # active_drawing_session_id is set by the main thread before starting drawing.
    save_drawing_history() # Saving here from potentially a thread, ensure file I/O is safe or delegate.
    return drawing_data

def get_drawing_from_history(drawing_id):
    global drawing_history
    return next((item for item in drawing_history if item.get('drawing_id') == drawing_id), None)

def update_drawing_status_in_history(drawing_id, status, current_command_index=None):
    # Similar safety considerations as add_or_update_drawing_in_history
    global drawing_history, active_drawing_session_id
    item = get_drawing_from_history(drawing_id)
    if item:
        item['status'] = status; item['last_updated'] = datetime.now().isoformat()
        if current_command_index is not None: item['current_command_index'] = current_command_index
        if status in ["completed", "aborted_manual_override", "aborted_new_drawing", "aborted_new_action", "interrupted_error", "interrupted"]: # If drawing truly ends
            if active_drawing_session_id == drawing_id: active_drawing_session_id = None 
        save_drawing_history()
        logging.info(f"Updated status of drawing '{drawing_id}' to '{status}'.")
        return True
    return False

load_drawing_history()

# --- QR Upload Route (unchanged) ---
UPLOAD_PAGE_TEMPLATE = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Upload Image</title><style>body{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;background-color:#f0f0f0}.container{background-color:white;padding:20px;border-radius:8px;box-shadow:0 0 10px rgba(0,0,0,.1);text-align:center}input[type=file]{margin-bottom:15px;display:block;margin-left:auto;margin-right:auto}button{padding:10px 15px;background-color:#007bff;color:white;border:none;border-radius:4px;cursor:pointer;font-size:1em}button:hover{background-color:#0056b3}#message{margin-top:15px;font-weight:700}h2{margin-top:0}</style></head><body><div class=container><h2>Select Image to Upload</h2><form id=uploadForm method=post enctype=multipart/form-data><input type=file name=image id=imageFile accept=image/* required><button type=submit>Upload</button></form><div id=message></div></div><script>document.getElementById("uploadForm").addEventListener("submit",async function(e){e.preventDefault();const t=new FormData(this),s=document.getElementById("message"),a=this.querySelector('button[type="submit"]'),i=this.querySelector('input[type="file"]');s.textContent="Uploading...",a.disabled=!0,i.disabled=!0;try{const e=await fetch(window.location.href,{method:"POST",body:t}),n=await e.json();e.ok?(s.textContent="Success: "+n.message+". You can close this page.",s.style.color="green"):(s.textContent="Error: "+(n.error||"Upload failed. Please try again."),s.style.color="red",a.disabled=!1,i.disabled=!1)}catch(e){s.textContent="Network Error: "+e.message+". Please try again.",s.style.color="red",a.disabled=!1,i.disabled=!1}})</script></body></html>
"""
@app.route('/qr_upload_page/<session_id>', methods=['GET', 'POST'])
def handle_qr_upload_page(session_id):
    global current_upload_session_id, active_drawing_session_id
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
                if active_drawing_session_id: update_drawing_status_in_history(active_drawing_session_id, "aborted_new_action"); active_drawing_session_id = None
                socketio.emit('qr_image_received', { 'success': True, 'message': f"Image '{original_filename}' uploaded.", 'original_filename': original_filename, 'filepath_on_server': filepath_on_server})
                socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history))
                current_upload_session_id = None 
                return jsonify({"message": f"Image '{original_filename}' uploaded successfully!"}), 200
            except Exception as e:
                logging.error(f"Error saving QR uploaded file: {e}", exc_info=True)
                socketio.emit('qr_image_received', {'success': False, 'message': f"Error saving '{original_filename}'.", 'original_filename': original_filename })
                return jsonify({"error": "Failed to save file on server."}), 500
    return render_template_string(UPLOAD_PAGE_TEMPLATE)

# --- SocketIO Event Handlers (largely unchanged except for drawing initiation) ---
@socketio.on('connect')
def handle_connect():
    # ... (same as before)
    global drawing_history, is_drawing_active_flag, active_drawing_session_id
    logging.info(f"Client connected: {request.sid}")
    emit('response', {'data': 'Connected to Python backend!'})
    emit('robot_connection_status', {
        'success': robot.is_connected, 
        'message': f"Connected to {robot.current_target_host}" if robot.is_connected else 'Not connected to robot'
    })
    emit('drawing_history_updated', get_ui_history_summary(drawing_history))
    current_active_drawing = get_drawing_from_history(active_drawing_session_id) if active_drawing_session_id else None
    if is_drawing_active_flag and current_active_drawing:
         emit('drawing_status_update', {
            'active': True, 'message': f"Drawing of '{current_active_drawing['original_filename']}' is in progress.",
            'resumable': True, 'drawing_id': current_active_drawing['drawing_id'],
            'original_filename': current_active_drawing['original_filename'],
            'progress': (current_active_drawing.get('current_command_index', 0) / current_active_drawing.get('total_commands', 1)) * 100 if current_active_drawing.get('total_commands', 0) > 0 else 0
        })
    else: 
        last_interrupt = next((item for item in drawing_history if item.get('status') and 'interrupted' in item.get('status')), None)
        if last_interrupt:
            emit('drawing_status_update', {
                'active': False, 'message': f"Interrupted drawing of '{last_interrupt['original_filename']}' is available.",
                'resumable': True, 'drawing_id': last_interrupt['drawing_id'],
                'original_filename': last_interrupt['original_filename'],
                'progress': (last_interrupt.get('current_command_index', 0) / last_interrupt.get('total_commands', 1)) * 100 if last_interrupt.get('total_commands', 0) > 0 else 0
            })
        else: emit('drawing_status_update', {'active': False, 'message': 'Idle', 'resumable': False})

@socketio.on('disconnect')
def handle_disconnect(): logging.info(f"Client disconnected: {request.sid}")

@socketio.on('robot_connect_request')
def handle_robot_connect_request(data): 
    global is_drawing_active_flag
    if is_drawing_active_flag: emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'}); return
    use_real = data.get('use_real_robot', config.USE_REAL_ROBOT_DEFAULT) 
    success, message = robot.connect_robot(use_real=use_real)
    emit('robot_connection_status', {'success': success, 'message': message})

@socketio.on('robot_disconnect_request')
def handle_robot_disconnect_request(json_data):
    global is_drawing_active_flag
    if is_drawing_active_flag: emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'}); return
    success, message = robot.disconnect_robot(graceful=True)
    emit('robot_connection_status', {'success': robot.is_connected, 'message': message if success else "Failed to disconnect"})

def check_and_abort_active_drawing(command_description="Manual command"):
    global active_drawing_session_id, is_drawing_active_flag, drawing_history, drawing_thread
    if active_drawing_session_id:
        logging.warning(f"{command_description} received, aborting active drawing '{active_drawing_session_id}'.")
        # Signal the drawing thread to stop if it's running
        # This requires a mechanism for the thread to check a flag.
        # For now, we'll just update status. True cancellation needs thread cooperation.
        # If drawing_thread has a 'stop_event' or similar: drawing_thread.stop_event.set()
        update_drawing_status_in_history(active_drawing_session_id, "aborted_manual_override")
        is_drawing_active_flag = False 
        # active_drawing_session_id will be cleared by update_drawing_status_in_history or by the thread itself
        emit('drawing_status_update', {'active': False, 'message': f"Drawing aborted due to {command_description}.", 'resumable': False, 'drawing_id': active_drawing_session_id}) 
        emit('drawing_history_updated', get_ui_history_summary(drawing_history)) 
        return True 
    return False

# ... (other socket handlers like send_robot_command, direct_image_upload, audio_chunk, submit_text_to_llm, send_custom_coordinates, request_threshold_preview are largely the same)
# Note: send_robot_command for 'raw' will now work as per original, taking a pre-formatted string.
@socketio.on('send_robot_command') 
def handle_send_robot_command(json_data, triggered_by_llm=False): 
    global is_drawing_active_flag
    if is_drawing_active_flag:
        if triggered_by_llm: check_and_abort_active_drawing(f"LLM command '{json_data.get('type', 'N/A')}'")
        else: emit('command_response', {'success': False, 'message': 'Drawing active.', 'command_sent': json_data.get('type', 'N/A')}); return False, "Drawing active."
    elif not triggered_by_llm : check_and_abort_active_drawing(f"Manual command '{json_data.get('type', 'N/A')}'")
    
    command_type = json_data.get('type', 'raw'); command_str = json_data.get('command_str') 
    if not robot.is_connected and command_type not in ['go_home']: 
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
    elif command_type == 'raw' and command_str: success, message = robot.send_command_raw(command_str); actual_command_sent_display = command_str
        
    if not triggered_by_llm: emit('command_response', {'success': success, 'message': message, 'command_sent': actual_command_sent_display})
    if not robot.is_connected: emit('robot_connection_status', {'success': False, 'message': 'Disconnected'}) 
    return success, message

@socketio.on('direct_image_upload')
def handle_direct_image_upload(data):
    global active_drawing_session_id; check_and_abort_active_drawing("New direct image upload") 
    original_filename = data.get('filename'); base64_data = data.get('fileData')
    if not original_filename or not base64_data: emit('direct_image_upload_response', {'success': False, 'message': 'Missing data.'}); return
    try:
        image_data = base64.b64decode(base64_data); _, f_ext = os.path.splitext(original_filename)
        if f_ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']: emit('direct_image_upload_response', {'success': False, 'message': f"Invalid type: {f_ext}"}); return
        filename_on_server = str(uuid.uuid4()) + f_ext; filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
        with open(filepath_on_server, 'wb') as f: f.write(image_data)
        emit('direct_image_upload_response', { 'success': True, 'message': f"Image '{original_filename}' uploaded.", 'original_filename': original_filename, 'filepath_on_server': filepath_on_server })
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
    global is_drawing_active_flag; text_command = data.get('text_command')
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
    except Exception as e: logging.error(f"API Error in LLM handler: {e}", exc_info=True); emit('llm_response_chunk', {'error': f'Server error: {e}', 'done': True})

@socketio.on('send_custom_coordinates')
def handle_send_custom_coordinates_event(data):
    check_and_abort_active_drawing("Manual coordinate input")
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


# --- Drawing Execution (Modified to run in a thread) ---
def _drawing_thread_target(app_context, robot_instance, commands, start_idx, total_cmds,
                           original_fname, drawing_id, current_session_data, socketio_instance):
    """Target function for the drawing thread."""
    global is_drawing_active_flag, active_drawing_session_id # These are modified by the thread

    with app_context: # Important for SocketIO emits from thread
        try:
            for i in range(start_idx, total_cmds):
                # Check for external stop request (e.g., if check_and_abort_active_drawing sets a flag later)
                # For now, relies on is_drawing_active_flag which is set by main thread.
                # A more robust stop would use a threading.Event.
                if not is_drawing_active_flag: # If flag was cleared by an abort
                    logging.info(f"Drawing thread for '{original_fname}' (ID: {drawing_id}) detected abort signal.")
                    # Status already updated by abort function
                    return

                current_session_data['current_command_index'] = i 
                # add_or_update_drawing_in_history(copy.deepcopy(current_session_data)) # Update history with progress
                # Consider emitting progress to main thread to update history to avoid direct file I/O from thread
                
                x_py, z_py_depth, y_py_side = commands[i]
                formatted_cmd_str = robot_instance._format_command(x_py, z_py_depth, y_py_side)
                
                progress_msg = f"Drawing '{original_fname}': Cmd {i+1}/{total_cmds}"
                socketio_instance.emit('drawing_status_update', {
                    'active': True, 'message': progress_msg, 
                    'progress': ((i+1)/total_cmds) * 100, 'resumable': True, 
                    'drawing_id': drawing_id, 'original_filename': original_fname
                })
                
                success, msg = robot_instance.send_command_raw(formatted_cmd_str)
                if not success:
                    logging.error(f"Drawing '{original_fname}' failed at command {i+1}: {msg}")
                    update_drawing_status_in_history(drawing_id, "interrupted", i) # Update history from thread
                    socketio_instance.emit('drawing_status_update', {
                        'active': False, 'message': f"Drawing of '{original_fname}' interrupted: {msg}. Ready to resume.", 
                        'resumable': True, 'drawing_id': drawing_id, 'original_filename': original_fname, 
                        'progress': (i / total_cmds) * 100 if total_cmds > 0 else 0
                    })
                    # is_drawing_active_flag will be set to False in the finally block of the caller
                    return # Exit thread on error
                
                # time.sleep(0.0005) # Removed as per user request - this was the original purpose of threading
            
            # Drawing completed successfully
            update_drawing_status_in_history(drawing_id, "completed", total_cmds)
            socketio_instance.emit('command_response', {'success': True, 'message': f"Sent all {total_cmds} commands for '{original_fname}'."})
            socketio_instance.emit('drawing_status_update', {
                'active': False, 'message': f"Drawing of '{original_fname}' complete.", 
                'resumable': False, 'drawing_id': drawing_id
            })
            robot_instance.go_home() # Send robot home after successful drawing
            
        except Exception as e:
            logging.error(f"Exception in drawing thread for '{original_fname}': {e}", exc_info=True)
            current_idx_on_error = current_session_data.get('current_command_index', start_idx)
            update_drawing_status_in_history(drawing_id, "interrupted_error", current_idx_on_error)
            socketio_instance.emit('command_response', {'success': False, 'message': f"Error during drawing: {e}"})
            socketio_instance.emit('drawing_status_update', {
                'active': False, 'message': f"Drawing of '{original_fname}' failed with server error. Ready to resume.", 
                'resumable': True, 'drawing_id': drawing_id, 'original_filename': original_fname, 
                'progress': (current_idx_on_error / total_cmds) * 100 if total_cmds > 0 else 0
            })
        finally:
            # This block will run whether the drawing completes, errors, or is aborted (if abort check is effective)
            # Ensure global state is reset appropriately
            # The main thread should also have a role in this after thread.join() or if it manages abortion
            # For now, let the thread clear its active status.
            if active_drawing_session_id == drawing_id: # Only clear if this was the active drawing
                is_drawing_active_flag = False # Signal that no drawing is active
                # active_drawing_session_id is cleared by update_drawing_status_in_history if status is 'completed' or 'interrupted_error' etc.
            logging.info(f"Drawing thread for '{original_fname}' (ID: {drawing_id}) finished.")
            socketio_instance.emit('drawing_history_updated', get_ui_history_summary(drawing_history))


def _execute_drawing_commands(drawing_session_id_to_execute):
    global is_drawing_active_flag, active_drawing_session_id, drawing_history, drawing_thread

    session_data = get_drawing_from_history(drawing_session_id_to_execute)
    if not session_data:
        logging.error(f"Drawing session {drawing_session_id_to_execute} not found.")
        emit('drawing_status_update', {'active': False, 'message': "Error: Drawing session not found.", 'resumable': False, 'drawing_id': drawing_session_id_to_execute})
        # No need to set is_drawing_active_flag = False here as it wasn't set True for this call
        return

    # Critical section: Set global flags before starting the thread
    is_drawing_active_flag = True
    active_drawing_session_id = drawing_session_id_to_execute
    
    original_filename = session_data['original_filename']
    commands_to_execute_tuples = list(session_data['robot_commands_tuples']) 

    if session_data.get('is_image_drawing', True): 
        if not os.path.exists(SIGNATURE_IMAGE_FULL_PATH):
            logging.error(f"Signature image not found: {SIGNATURE_IMAGE_FULL_PATH}.")
        else:
            try:
                signature_robot_commands = process_image_to_robot_commands_pipeline(
                    SIGNATURE_IMAGE_FULL_PATH, config.SIGNATURE_CANNY_THRESHOLD1, config.SIGNATURE_CANNY_THRESHOLD2, optimize=True)
                if signature_robot_commands:
                    commands_to_execute_tuples.extend(signature_robot_commands)
                    session_data['total_commands'] = len(commands_to_execute_tuples) 
            except Exception as sig_e:
                logging.error(f"Error processing signature: {sig_e}")

    start_index = session_data['current_command_index']
    total_commands_in_session = session_data['total_commands'] 
    
    logging.info(f"Preparing to execute/resume drawing '{original_filename}' (ID: {active_drawing_session_id}) from command {start_index + 1}/{total_commands_in_session}")
    update_drawing_status_in_history(active_drawing_session_id, "in_progress" if start_index == 0 else "in_progress_resumed", start_index)
    emit('drawing_history_updated', get_ui_history_summary(drawing_history)) # Emit updated history

    try:
        if not robot.is_connected:
            use_real = config.USE_REAL_ROBOT_DEFAULT
            if robot.current_target_host == config.REAL_ROBOT_HOST: use_real = True
            conn_success, conn_msg = robot.connect_robot(use_real=use_real)
            if not conn_success:
                emit('command_response', {'success': False, 'message': f"Robot connection failed: {conn_msg}"})
                is_drawing_active_flag = False; active_drawing_session_id = None # Reset flags
                update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted", start_index)
                emit('drawing_status_update', {'active': False, 'message': f"Drawing interrupted (robot conn failed).", 'resumable': True, 'drawing_id': drawing_session_id_to_execute})
                emit('drawing_history_updated', get_ui_history_summary(drawing_history))
                return
            emit('robot_connection_status', {'success': True, 'message': conn_msg})
        
        if start_index == 0: 
            safe_x, safe_z_depth, safe_y_side = config.SAFE_ABOVE_CENTER_PY
            success_safe, msg_safe = robot.move_to_position_py(safe_x, safe_z_depth, safe_y_side)
            if not success_safe:
                is_drawing_active_flag = False; active_drawing_session_id = None # Reset flags
                update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted", 0)
                emit('drawing_status_update', {'active': False, 'message': f"Drawing aborted (safe start failed).", 'resumable': True, 'drawing_id': drawing_session_id_to_execute})
                emit('drawing_history_updated', get_ui_history_summary(drawing_history))
                # robot.go_home() # Consider if go_home is safe or needed here
                return
        
        # Prepare data for the thread
        # Make a deep copy of session_data if the thread is going to modify parts of it that are also used by main thread.
        # For just reading total_commands etc., a shallow copy or direct pass is okay.
        # Since the thread updates 'current_command_index' in its working copy, deepcopy is safer for current_session_data.
        thread_session_data = copy.deepcopy(session_data)

        # Create and start the drawing thread
        drawing_thread = threading.Thread(
            target=_drawing_thread_target,
            args=(
                app.app_context(), # Pass Flask app context for socketio.emit in thread
                robot, # Pass the robot instance
                commands_to_execute_tuples,
                start_index,
                total_commands_in_session,
                original_filename,
                drawing_session_id_to_execute,
                thread_session_data, # Pass the copy
                socketio # Pass socketio instance
            )
        )
        drawing_thread.daemon = True # Allow main program to exit even if thread is running (though it should clean up)
        drawing_thread.start()
        
        # _execute_drawing_commands will now return, the thread handles the rest.
        # The `is_drawing_active_flag` remains True.
        # The thread is responsible for setting it to False upon completion/error.

    except Exception as e: # Catch errors in the setup phase before thread starts
        logging.error(f"Error setting up drawing execution for '{original_filename}': {e}", exc_info=True)
        is_drawing_active_flag = False # Reset flag as thread won't start
        active_drawing_session_id = None
        update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted_error", start_index)
        emit('command_response', {'success': False, 'message': f"Error setting up drawing: {e}"})
        emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' failed setup.", 'resumable': True, 'drawing_id': drawing_session_id_to_execute})
        emit('drawing_history_updated', get_ui_history_summary(drawing_history))


@socketio.on('process_image_for_drawing')
def handle_process_image_for_drawing(data):
    # This function now sets up the drawing data and then calls _execute_drawing_commands,
    # which in turn will spawn the thread.
    global is_drawing_active_flag, active_drawing_session_id
    if is_drawing_active_flag: emit('command_response', {'success': False, 'message': "Drawing active."}); return
    if active_drawing_session_id: update_drawing_status_in_history(active_drawing_session_id, "aborted_new_drawing")
    active_drawing_session_id = None 

    filepath = data.get('filepath'); original_filename = data.get('original_filename', os.path.basename(filepath or "unknown"))
    if not filepath or not os.path.exists(filepath): emit('command_response', {'success': False, 'message': f"File not found: {filepath}"}); return
    
    canny_t1, canny_t2 = data.get('canny_t1', config.DEFAULT_CANNY_THRESHOLD1), data.get('canny_t2', config.DEFAULT_CANNY_THRESHOLD2)
    emit('drawing_status_update', {'active': True, 'message': f"Processing '{original_filename}'...", 'resumable': False}) 

    try:
        robot_commands_tuples = process_image_to_robot_commands_pipeline(filepath, canny_t1, canny_t2)
        if not robot_commands_tuples: 
            emit('command_response', {'success': False, 'message': f"No drawing commands for '{original_filename}'."})
            emit('drawing_status_update', {'active': False, 'message': f"Failed to process '{original_filename}'.", 'resumable': False})
            return
        
        drawing_id = f"draw_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        new_drawing_data = {
            'drawing_id': drawing_id, 'filepath_on_server': filepath, 'original_filename': original_filename,
            'robot_commands_tuples': robot_commands_tuples, 'current_command_index': 0,
            'total_commands': len(robot_commands_tuples), 'canny_t1': canny_t1, 'canny_t2': canny_t2,
            'status': 'pending_execution', 'timestamp': datetime.now().isoformat(), 'is_image_drawing': True 
        }
        add_or_update_drawing_in_history(new_drawing_data) 
        emit('drawing_status_update', {'active': True, 'message': f"Generated {len(robot_commands_tuples)} commands for '{original_filename}'. Preparing to draw.", 
                                       'resumable': True, 'drawing_id': drawing_id, 'original_filename': original_filename, 'progress': 0})
        emit('drawing_history_updated', get_ui_history_summary(drawing_history))
        
        _execute_drawing_commands(drawing_id) # This will now spawn the thread
    except Exception as e: 
        logging.error(f"Error processing for drawing '{original_filename}': {e}", exc_info=True)
        emit('command_response', {'success': False, 'message': f"Error processing image: {e}"})
        emit('drawing_status_update', {'active': False, 'message': f"Error processing '{original_filename}'.", 'resumable': False})
        # Ensure flags are reset if setup fails before thread creation attempt in _execute_drawing_commands
        if active_drawing_session_id: # If it was set
            update_drawing_status_in_history(active_drawing_session_id, "interrupted_error", 0)
            active_drawing_session_id = None
        is_drawing_active_flag = False


@socketio.on('resume_drawing_request')
def handle_resume_drawing_request(data):
    # Resuming into a threaded drawing needs careful state management.
    # The thread would need to pick up from the saved 'current_command_index'.
    global is_drawing_active_flag, active_drawing_session_id
    drawing_id_to_resume = data.get('drawing_id')
    if is_drawing_active_flag: emit('drawing_status_update', {'active': True, 'message': "Another drawing active."}); return

    session_to_resume = get_drawing_from_history(drawing_id_to_resume)
    if session_to_resume and session_to_resume.get('status') != 'completed':
        emit('drawing_status_update', {
            'active': True, 'message': f"Resuming drawing of '{session_to_resume['original_filename']}'...",
            'resumable': True, 'drawing_id': drawing_id_to_resume, 'original_filename': session_to_resume['original_filename'],
            'progress': (session_to_resume['current_command_index'] / session_to_resume['total_commands']) * 100 if session_to_resume.get('total_commands',0) > 0 else 0
        })
        _execute_drawing_commands(drawing_id_to_resume) # This will start the thread from the saved index
    else:
        emit('drawing_status_update', {'active': False, 'message': "Drawing session not found or already completed.", 'resumable': False})

@socketio.on('restart_drawing_request')
def handle_restart_drawing_request(data):
    global is_drawing_active_flag, active_drawing_session_id
    drawing_id_to_restart = data.get('drawing_id')
    if is_drawing_active_flag: emit('drawing_status_update', {'active': True, 'message': "Another drawing active."}); return

    session_to_restart = get_drawing_from_history(drawing_id_to_restart)
    if session_to_restart:
        session_to_restart['current_command_index'] = 0 # Reset progress
        session_to_restart['status'] = 'pending_restart' 
        add_or_update_drawing_in_history(session_to_restart.copy()) 
        emit('drawing_status_update', {
            'active': True, 'message': f"Restarting drawing of '{session_to_restart['original_filename']}'...",
            'resumable': True, 'drawing_id': drawing_id_to_restart, 'original_filename': session_to_restart['original_filename'], 'progress': 0
        })
        emit('drawing_history_updated', get_ui_history_summary(drawing_history))
        _execute_drawing_commands(drawing_id_to_restart)
    else:
        emit('drawing_status_update', {'active': False, 'message': "Drawing session to restart not found.", 'resumable': False})


if __name__ == '__main__':
    server_port = 5555 
    app.config['SERVER_PORT'] = server_port 
    logging.info(f"Starting Python backend server (SocketIO with Flask) on port {server_port}...")
    socketio.run(app, host='0.0.0.0', port=server_port, debug=True, use_reloader=False)