# backend/api_server.py
from flask import Flask, request, render_template_string, jsonify, send_file
from flask_socketio import SocketIO, emit
from robot_interface import RobotInterface
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

# Configure basic logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'
)
logging.getLogger('engineio.server').setLevel(logging.WARNING) 
logging.getLogger('socketio.server').setLevel(logging.WARNING) 
logging.getLogger('werkzeug').setLevel(logging.WARNING) 


app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_here!' 
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), config.QR_UPLOAD_FOLDER)
app.config['AUDIO_TEMP_FOLDER_PATH'] = os.path.join(os.path.dirname(__file__), config.AUDIO_TEMP_FOLDER)

DRAWING_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "drawing_history.json")
MAX_DRAWING_HISTORY = 5


# Create directories if they don't exist
for folder_path in [app.config['UPLOAD_FOLDER'], app.config['AUDIO_TEMP_FOLDER_PATH']]:
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        logging.info(f"Created folder at: {folder_path}")

# Load AI models
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

def get_ui_history_summary(history_list):
    """Converts raw history items to the summary structure expected by UI."""
    ui_summary = []
    for item in history_list:
        total_commands = item.get('total_commands', 0)
        current_index = item.get('current_command_index', 0)
        status = item.get('status', 'unknown')
        
        progress_val = 0
        if total_commands > 0:
            progress_val = (current_index / total_commands) * 100
        elif status == 'completed':
            progress_val = 100
            
        ui_summary.append({
            'drawing_id': item.get('drawing_id'),
            'original_filename': item.get('original_filename'),
            'status': status,
            'progress': progress_val,
            'last_updated': item.get('last_updated')
        })
    return ui_summary

def save_drawing_history():
    """Saves the drawing history to a file."""
    global drawing_history
    try:
        with open(DRAWING_HISTORY_FILE, 'w') as f:
            json.dump(drawing_history, f, indent=4)
        logging.info(f"Drawing history saved to {DRAWING_HISTORY_FILE}")
    except IOError as e:
        logging.error(f"Error saving drawing history: {e}")

def load_drawing_history():
    """Loads drawing history from file if it exists."""
    global drawing_history
    if os.path.exists(DRAWING_HISTORY_FILE):
        try:
            with open(DRAWING_HISTORY_FILE, 'r') as f:
                history_data = json.load(f)
                if isinstance(history_data, list):
                    valid_history = []
                    for state in history_data:
                        if isinstance(state, dict) and all(k in state for k in ['drawing_id', 'original_filename', 'status']):
                            valid_history.append(state)
                        else:
                            logging.warning(f"Invalid entry found in {DRAWING_HISTORY_FILE}, skipping: {state}")
                    drawing_history = valid_history[:MAX_DRAWING_HISTORY] 
                    logging.info(f"Drawing history loaded with {len(drawing_history)} entries from {DRAWING_HISTORY_FILE}.")
                else:
                    logging.warning(f"Invalid data format in {DRAWING_HISTORY_FILE}. Initializing empty history.")
                    drawing_history = []
                    if os.path.exists(DRAWING_HISTORY_FILE): os.remove(DRAWING_HISTORY_FILE) # remove corrupted file
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Error loading drawing history: {e}. Initializing empty history.")
            drawing_history = []
            if os.path.exists(DRAWING_HISTORY_FILE): os.remove(DRAWING_HISTORY_FILE) # remove corrupted file
    else:
        logging.info("No previous drawing history file found. Initializing empty history.")
        drawing_history = []

def clear_drawing_history_file(save=True):
    """Deletes the drawing history file and optionally saves an empty history."""
    if os.path.exists(DRAWING_HISTORY_FILE):
        try:
            os.remove(DRAWING_HISTORY_FILE)
            logging.info(f"Drawing history file {DRAWING_HISTORY_FILE} deleted.")
        except OSError as e:
            logging.error(f"Error deleting drawing history file: {e}")
    if save: 
        global drawing_history
        drawing_history = [] # Clear the in-memory history too
        save_drawing_history() # This will save an empty list

def add_or_update_drawing_in_history(drawing_data):
    global drawing_history, active_drawing_session_id
    if 'drawing_id' not in drawing_data:
        drawing_data['drawing_id'] = f"draw_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    drawing_data['last_updated'] = datetime.now().isoformat()
    found_index = next((i for i, item in enumerate(drawing_history) if item.get('drawing_id') == drawing_data['drawing_id']), -1)
    
    if found_index != -1: 
        drawing_history[found_index] = drawing_data
    else: 
        drawing_history.insert(0, drawing_data) 
        drawing_history = drawing_history[:MAX_DRAWING_HISTORY]
    
    active_drawing_session_id = drawing_data['drawing_id'] 
    save_drawing_history()
    return drawing_data

def get_drawing_from_history(drawing_id):
    global drawing_history
    return next((item for item in drawing_history if item.get('drawing_id') == drawing_id), None)

def update_drawing_status_in_history(drawing_id, status, current_command_index=None):
    global drawing_history, active_drawing_session_id
    item = get_drawing_from_history(drawing_id)
    if item:
        item['status'] = status
        item['last_updated'] = datetime.now().isoformat()
        if current_command_index is not None:
            item['current_command_index'] = current_command_index
        if status in ["completed", "aborted_manual_override", "aborted_new_drawing", "aborted_new_action"]:
            if active_drawing_session_id == drawing_id:
                active_drawing_session_id = None 
        save_drawing_history()
        logging.info(f"Updated status of drawing '{drawing_id}' to '{status}'.")
        return True
    return False

load_drawing_history()

UPLOAD_PAGE_TEMPLATE = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Upload Image</title><style>body{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;background-color:#f0f0f0}.container{background-color:white;padding:20px;border-radius:8px;box-shadow:0 0 10px rgba(0,0,0,.1);text-align:center}input[type=file]{margin-bottom:15px;display:block;margin-left:auto;margin-right:auto}button{padding:10px 15px;background-color:#007bff;color:white;border:none;border-radius:4px;cursor:pointer;font-size:1em}button:hover{background-color:#0056b3}#message{margin-top:15px;font-weight:700}h2{margin-top:0}</style></head><body><div class=container><h2>Select Image to Upload</h2><form id=uploadForm method=post enctype=multipart/form-data><input type=file name=image id=imageFile accept=image/* required><button type=submit>Upload</button></form><div id=message></div></div><script>document.getElementById("uploadForm").addEventListener("submit",async function(e){e.preventDefault();const t=new FormData(this),s=document.getElementById("message"),a=this.querySelector('button[type="submit"]'),i=this.querySelector('input[type="file"]');s.textContent="Uploading...",a.disabled=!0,i.disabled=!0;try{const e=await fetch(window.location.href,{method:"POST",body:t}),n=await e.json();e.ok?(s.textContent="Success: "+n.message+". You can close this page.",s.style.color="green"):(s.textContent="Error: "+(n.error||"Upload failed. Please try again."),s.style.color="red",a.disabled=!1,i.disabled=!1)}catch(e){s.textContent="Network Error: "+e.message+". Please try again.",s.style.color="red",a.disabled=!1,i.disabled=!1}})</script></body></html>
"""

@app.route('/qr_upload_page/<session_id>', methods=['GET', 'POST'])
def handle_qr_upload_page(session_id):
    global current_upload_session_id, active_drawing_session_id
    if session_id != current_upload_session_id:
        return "Invalid or expired upload session.", 403
    if request.method == 'POST':
        if 'image' not in request.files: return jsonify({"error": "No image file part"}), 400
        file = request.files['image']
        if file.filename == '': return jsonify({"error": "No selected file"}), 400
        if file:
            original_filename = file.filename
            _, f_ext = os.path.splitext(original_filename)
            if f_ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
                 return jsonify({"error": "Invalid file type."}), 400
            filename_on_server = str(uuid.uuid4()) + f_ext
            filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
            try:
                file.save(filepath_on_server)
                if active_drawing_session_id:
                    update_drawing_status_in_history(active_drawing_session_id, "aborted_new_action")
                    active_drawing_session_id = None
                socketio.emit('qr_image_received', { 
                    'success': True, 'message': f"Image '{original_filename}' uploaded via QR.",
                    'original_filename': original_filename, 'filepath_on_server': filepath_on_server
                })
                socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history))
                current_upload_session_id = None 
                return jsonify({"message": f"Image '{original_filename}' uploaded successfully!"}), 200
            except Exception as e:
                logging.error(f"Error saving QR uploaded file: {e}", exc_info=True)
                socketio.emit('qr_image_received', {'success': False, 'message': f"Error saving '{original_filename}' on server.", 'original_filename': original_filename })
                return jsonify({"error": "Failed to save file on server."}), 500
    return render_template_string(UPLOAD_PAGE_TEMPLATE)

@socketio.on('connect')
def handle_connect():
    global drawing_history, is_drawing_active_flag, active_drawing_session_id
    logging.info(f"Client connected: {request.sid}")
    emit('response', {'data': 'Connected to Python backend!'})
    emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Connected to robot' if robot.is_connected else 'Not connected to robot'})
    emit('drawing_history_updated', get_ui_history_summary(drawing_history))

    current_active_drawing = get_drawing_from_history(active_drawing_session_id) if active_drawing_session_id else None
    if is_drawing_active_flag and current_active_drawing:
         emit('drawing_status_update', {
            'active': True, 
            'message': f"Drawing of '{current_active_drawing['original_filename']}' is in progress.",
            'resumable': True, 
            'drawing_id': current_active_drawing['drawing_id'],
            'original_filename': current_active_drawing['original_filename'],
            'progress': (current_active_drawing.get('current_command_index', 0) / current_active_drawing.get('total_commands', 1)) * 100 if current_active_drawing.get('total_commands', 0) > 0 else 0
        })
    else: 
        last_interrupt = next((item for item in drawing_history if item.get('status') and 'interrupted' in item.get('status')), None)
        if last_interrupt:
            emit('drawing_status_update', {
                'active': False, 
                'message': f"Interrupted drawing of '{last_interrupt['original_filename']}' is available.",
                'resumable': True,
                'drawing_id': last_interrupt['drawing_id'],
                'original_filename': last_interrupt['original_filename'],
                'progress': (last_interrupt.get('current_command_index', 0) / last_interrupt.get('total_commands', 1)) * 100 if last_interrupt.get('total_commands', 0) > 0 else 0
            })
        else:
            emit('drawing_status_update', {'active': False, 'message': 'Idle', 'resumable': False})

# ... (disconnect, robot_connect_request, robot_disconnect_request are fine) ...
@socketio.on('disconnect')
def handle_disconnect(): logging.info(f"Client disconnected: {request.sid}")
@socketio.on('robot_connect_request')
def handle_robot_connect_request(json_data):
    global is_drawing_active_flag
    if is_drawing_active_flag: emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'}); return
    success, message = robot.connect_robot()
    emit('robot_connection_status', {'success': success, 'message': message})
@socketio.on('robot_disconnect_request')
def handle_robot_disconnect_request(json_data):
    global is_drawing_active_flag
    if is_drawing_active_flag: emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'}); return
    success, message = robot.disconnect_robot(graceful=True)
    emit('robot_connection_status', {'success': robot.is_connected, 'message': message if success else "Failed to disconnect"})

def check_and_abort_active_drawing(command_description="Manual command"):
    global active_drawing_session_id, is_drawing_active_flag, drawing_history
    if active_drawing_session_id:
        logging.warning(f"{command_description} received, aborting active drawing '{active_drawing_session_id}'.")
        update_drawing_status_in_history(active_drawing_session_id, "aborted_manual_override")
        # active_drawing_session_id is cleared by update_drawing_status_in_history
        is_drawing_active_flag = False
        emit('drawing_status_update', {'active': False, 'message': f"Drawing aborted due to {command_description}.", 'resumable': False, 'drawing_id': active_drawing_session_id}) 
        emit('drawing_history_updated', get_ui_history_summary(drawing_history)) 
        return True 
    return False

@socketio.on('send_robot_command') 
def handle_send_robot_command(json_data, triggered_by_llm=False): 
    global is_drawing_active_flag
    if is_drawing_active_flag:
        if triggered_by_llm:
            logging.warning(f"LLM command received while drawing active. Aborting current drawing: {active_drawing_session_id}")
            check_and_abort_active_drawing(f"LLM command '{json_data.get('type', 'N/A')}'")
        else: 
            emit('command_response', {'success': False, 'message': 'Cannot send manual commands while drawing is active.', 'command_sent': json_data.get('type', 'N/A')})
            return False, "Drawing is active."
    elif not triggered_by_llm : 
        check_and_abort_active_drawing(f"Manual command '{json_data.get('type', 'N/A')}'")
    # ... (rest of the function)
    command_type = json_data.get('type', 'raw')
    command_str = json_data.get('command_str') 
    if not robot.is_connected and command_type not in ['go_home']: 
        conn_success, conn_message = robot.connect_robot()
        if not conn_success:
            if not triggered_by_llm: emit('command_response', {'success': False, 'message': f'Robot not connected & connection failed: {conn_message}', 'command_sent': command_type})
            return False, f'Robot not connected & connection failed: {conn_message}' 
        emit('robot_connection_status', {'success': True, 'message': conn_message})
    success, message = False, "Invalid command type"
    actual_command_sent = command_type
    if command_type == 'go_home':
        success, message = robot.go_home()
        x_h, z_h, y_h = config.ROBOT_HOME_POSITION_PY
        actual_command_sent = robot._format_command(x_h, z_h, y_h) + " (Home)"
    elif command_type == 'move_to_safe_center':
        x_s, z_s, y_s = config.SAFE_ABOVE_CENTER_PY
        success, message = robot.move_to_position_py(x_s, z_s, y_s)
        actual_command_sent = robot._format_command(x_s, z_s, y_s) + " (Safe Center)"
    elif command_type == 'raw' and command_str:
        success, message = robot.send_command_raw(command_str)
        actual_command_sent = command_str
    if not triggered_by_llm: emit('command_response', {'success': success, 'message': message, 'command_sent': actual_command_sent})
    if not robot.is_connected: emit('robot_connection_status', {'success': False, 'message': 'Disconnected'})
    return success, message 

@socketio.on('direct_image_upload')
def handle_direct_image_upload(data):
    global active_drawing_session_id
    # ... (rest of the function, but ensure active_drawing_session_id is handled)
    check_and_abort_active_drawing("New direct image upload") # This will also emit history update
    original_filename = data.get('filename')
    base64_data = data.get('fileData')
    if not original_filename or not base64_data: # ... (error handling)
        emit('direct_image_upload_response', {'success': False, 'message': 'Missing filename or file data.'})
        return
    try:
        image_data = base64.b64decode(base64_data)
        _, f_ext = os.path.splitext(original_filename)
        if f_ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']: # ... (error handling)
            emit('direct_image_upload_response', {'success': False, 'message': f"Invalid file type: {f_ext}", 'original_filename': original_filename})
            return
        filename_on_server = str(uuid.uuid4()) + f_ext
        filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
        with open(filepath_on_server, 'wb') as f: f.write(image_data)
        emit('direct_image_upload_response', { 'success': True, 'message': f"Image '{original_filename}' uploaded.", 'original_filename': original_filename, 'filepath_on_server': filepath_on_server })
        # No need to emit history here if check_and_abort did it, or if it's handled by new drawing process
    except Exception as e: # ... (error handling)
        logging.error(f"Error in direct_image_upload: {e}", exc_info=True)
        emit('direct_image_upload_response', {'success': False, 'message': f"Server error: {e}", 'original_filename': original_filename})

# ... (handle_audio_chunk remains the same)
@socketio.on('audio_chunk') 
def handle_audio_chunk(data):
    logging.info(f"--- API: Event 'audio_chunk' RECEIVED with data keys: {list(data.keys())} ---")
    audio_data_b64 = data.get('audioData')
    mime_type = data.get('mimeType', 'audio/webm') 
    if not audio_data_b64: logging.error("API: No audio data (audioData key) in received chunk."); emit('transcription_result', {'error': 'No audio data received.'}); return
    logging.info(f"API: Received audio data. Mime type: {mime_type}. Data length (chars): {len(audio_data_b64)}")
    try:
        audio_bytes = base64.b64decode(audio_data_b64)
        file_extension = ".webm"; mime_type_lower = mime_type.lower()
        if 'wav' in mime_type_lower: file_extension = ".wav"
        elif 'mp3' in mime_type_lower: file_extension = ".mp3"
        temp_audio_filename = f"voice_cmd_{uuid.uuid4()}{file_extension}"
        temp_audio_filepath = os.path.join(app.config['AUDIO_TEMP_FOLDER_PATH'], temp_audio_filename)
        with open(temp_audio_filepath, 'wb') as f: f.write(audio_bytes)
        transcribed_text = transcribe_audio(temp_audio_filepath) 
        if transcribed_text is not None: emit('transcription_result', {'text': transcribed_text}) 
        else: emit('transcription_result', {'error': 'Transcription failed on server.'})
        try: os.remove(temp_audio_filepath)
        except Exception as e: logging.warning(f"API Warning: Error removing temporary audio file {temp_audio_filepath}: {e}")
    except Exception as e: logging.error(f"API Error: Error processing audio chunk: {e}", exc_info=True); emit('transcription_result', {'error': f'Server error processing audio.'})


# ... (handle_submit_text_to_llm needs to call check_and_abort_active_drawing if LLM action is an override)
@socketio.on('submit_text_to_llm')
def handle_submit_text_to_llm(data):
    global is_drawing_active_flag, active_drawing_session_id # Ensure these are accessible
    logging.info(f"--- API: Event 'submit_text_to_llm' RECEIVED with data: {data} ---") 
    text_command = data.get('text_command')
    if not text_command: # ... (error handling)
        logging.error("API: No text_command in 'submit_text_to_llm' event.")
        emit('llm_response_chunk', {'error': 'No text command received by server.', 'done': True})
        return
    logging.info(f"API: Processing text command for LLM: '{text_command}'")
    parsed_action_command_from_llm = None
    try:
        for llm_response_part in process_command_with_llm_stream(text_command): 
            emit('llm_response_chunk', llm_response_part) 
            if llm_response_part.get("done"):
                if llm_response_part.get("parsed_action"):
                    parsed_action_command_from_llm = llm_response_part["parsed_action"]
                break 
        if parsed_action_command_from_llm:
            action_type = parsed_action_command_from_llm.get("type")
            if action_type in ["move", "move_to_coords"]: # Add other LLM actions that should override
                 if check_and_abort_active_drawing(f"LLM command '{action_type}'"):
                    logging.info(f"LLM command '{action_type}' aborted a previous drawing.")
            # ... (rest of LLM action handling)
            parameters = parsed_action_command_from_llm.get("parameters", {})
            if action_type == "move":
                target = parameters.get("target")
                if target == "home": handle_send_robot_command({'type': 'go_home'}, triggered_by_llm=True)
                elif target == "center": handle_send_robot_command({'type': 'move_to_safe_center'}, triggered_by_llm=True)
            elif action_type == "move_to_coords":
                x,y,z = parameters.get("x"), parameters.get("y"), parameters.get("z")
                if x is not None and y is not None and z is not None:
                    handle_send_robot_command({'type': 'raw', 'command_str': robot._format_command(x, y, z)}, triggered_by_llm=True)
    except Exception as e: # ... (error handling)
        logging.error(f"API Error in handle_submit_text_to_llm: {e}", exc_info=True)
        emit('llm_response_chunk', {'error': f'Server error: {e}', 'done': True})


@socketio.on('send_custom_coordinates')
def handle_send_custom_coordinates_event(data):
    logging.info(f"--- API: Event 'send_custom_coordinates' RECEIVED with data: {data} ---")
    check_and_abort_active_drawing("Manual coordinate input")
    if not robot.is_connected: emit('command_response', {'success': False, 'message': 'Robot not connected.'}); return
    try:
        x_py, z_py, y_py = float(data.get('x_py')), float(data.get('z_py')), float(data.get('y_py'))
        success, message = robot.move_to_position_py(x_py, z_py, y_py)
        emit('command_response', {'success': success, 'message': message, 'command_sent': f'Custom Coords: X={x_py}, Depth={z_py}, Side={y_py}'})
        if not robot.is_connected: emit('robot_connection_status', {'success': False, 'message': 'Disconnected'})
    except Exception as e: logging.error(f"API Error in handle_send_custom_coordinates_event: {e}", exc_info=True); emit('command_response', {'success': False, 'message': f'Server error: {e}'})

@socketio.on('request_threshold_preview')
def handle_request_threshold_preview(data):
    logging.info(f"--- API: Event 'request_threshold_preview' RECEIVED with data: {data} ---")
    filepath, t1, t2 = data.get('filepath'), data.get('t1'), data.get('t2')
    if not filepath or not os.path.exists(filepath) or t1 is None or t2 is None: emit('threshold_preview_image_response', {'error': 'Invalid data for preview.'}); return
    try:
        edges_array = get_canny_edges_array(filepath, int(t1), int(t2)) 
        if edges_array is not None:
            _, buffer = cv2.imencode('.png', edges_array); img_base64 = base64.b64encode(buffer).decode('utf-8')
            emit('threshold_preview_image_response', {'image_base64': img_base64})
        else: emit('threshold_preview_image_response', {'error': 'Failed to generate preview.'})
    except Exception as e: logging.error(f"API Error generating threshold preview: {e}", exc_info=True); emit('threshold_preview_image_response', {'error': f'Server error: {e}'})


def _execute_drawing_commands(drawing_session_id_to_execute):
    global is_drawing_active_flag, active_drawing_session_id, drawing_history

    session_data = get_drawing_from_history(drawing_session_id_to_execute)
    if not session_data:
        logging.error(f"Drawing session {drawing_session_id_to_execute} not found in history for execution.")
        emit('drawing_status_update', {'active': False, 'message': "Error: Drawing session not found.", 'resumable': False, 'drawing_id': drawing_session_id_to_execute})
        is_drawing_active_flag = False
        if active_drawing_session_id == drawing_session_id_to_execute: active_drawing_session_id = None
        return

    is_drawing_active_flag = True
    active_drawing_session_id = drawing_session_id_to_execute 
    
    original_filename = session_data['original_filename']
    commands = session_data['robot_commands_tuples']
    start_index = session_data['current_command_index']
    total_commands = session_data['total_commands']
    
    logging.info(f"Executing/Resuming drawing '{original_filename}' (ID: {active_drawing_session_id}) from command {start_index + 1}/{total_commands}")
    update_drawing_status_in_history(active_drawing_session_id, "in_progress" if start_index == 0 else "in_progress_resumed", start_index)
    emit('drawing_history_updated', get_ui_history_summary(drawing_history))


    try:
        if not robot.is_connected:
            conn_success, conn_msg = robot.connect_robot()
            if not conn_success:
                emit('command_response', {'success': False, 'message': f"Robot connection failed: {conn_msg}"})
                is_drawing_active_flag = False; active_drawing_session_id = None
                update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted", start_index)
                emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' interrupted (robot connection failed).", 'resumable': True, 'drawing_id': drawing_session_id_to_execute, 'original_filename': original_filename, 'progress': (start_index / total_commands) * 100 if total_commands > 0 else 0})
                emit('drawing_history_updated', get_ui_history_summary(drawing_history))
                return
            emit('robot_connection_status', {'success': True, 'message': conn_msg})
        
        if start_index == 0: 
            safe_x, safe_z, safe_y = config.SAFE_ABOVE_CENTER_PY
            success_safe, msg_safe = robot.move_to_position_py(safe_x, safe_z, safe_y)
            if not success_safe:
                is_drawing_active_flag = False; active_drawing_session_id = None
                update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted", 0)
                emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' aborted (safe start failed).", 'resumable': True, 'drawing_id': drawing_session_id_to_execute, 'original_filename': original_filename, 'progress': 0})
                emit('drawing_history_updated', get_ui_history_summary(drawing_history))
                robot.go_home(); return

        for i in range(start_index, total_commands):
            session_data['current_command_index'] = i 
            # Status remains 'in_progress' or 'in_progress_resumed'
            add_or_update_drawing_in_history(session_data.copy()) # Persist progress
            
            x_py, z_py, y_py = commands[i] 
            formatted_cmd_str = robot._format_command(x_py, z_py, y_py) 
            progress_message = f"Drawing '{original_filename}': Cmd {i+1}/{total_commands}"
            emit('drawing_status_update', {'active': True, 'message': progress_message, 'progress': ((i+1)/total_commands) * 100, 'resumable': True, 'drawing_id': active_drawing_session_id, 'original_filename': original_filename})
            
            success, msg = robot.send_command_raw(formatted_cmd_str)
            if not success:
                is_drawing_active_flag = False; active_drawing_session_id = None
                update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted", i)
                emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' interrupted. Ready to resume.", 'resumable': True, 'drawing_id': drawing_session_id_to_execute, 'original_filename': original_filename, 'progress': (i / total_commands) * 100 if total_commands > 0 else 0})
                emit('drawing_history_updated', get_ui_history_summary(drawing_history))
                return 
            socketio.sleep(0.0005) 
            
        update_drawing_status_in_history(drawing_session_id_to_execute, "completed", total_commands)
        emit('command_response', {'success': True, 'message': f"Sent all {total_commands} commands for '{original_filename}'."})
        emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' complete.", 'resumable': False, 'drawing_id': drawing_session_id_to_execute})
        emit('drawing_history_updated', get_ui_history_summary(drawing_history))
        robot.go_home() 
        is_drawing_active_flag = False; active_drawing_session_id = None

    except Exception as e:
        logging.error(f"Error during drawing execution for '{original_filename}': {e}", exc_info=True)
        is_drawing_active_flag = False; active_drawing_session_id = None
        current_idx = session_data.get('current_command_index', start_index) # Use last known good index
        update_drawing_status_in_history(drawing_session_id_to_execute, "interrupted_error", current_idx)
        emit('command_response', {'success': False, 'message': f"Error during drawing: {e}"})
        emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' failed with server error. Ready to resume.", 'resumable': True, 'drawing_id': drawing_session_id_to_execute, 'original_filename': original_filename, 'progress': (current_idx / total_commands) * 100 if total_commands > 0 else 0})
        emit('drawing_history_updated', get_ui_history_summary(drawing_history))
    finally:
        if not is_drawing_active_flag and active_drawing_session_id == drawing_session_id_to_execute:
            active_drawing_session_id = None 
        logging.info(f"Drawing execution for '{original_filename}' (ID: {drawing_session_id_to_execute}) ended. Active flag: {is_drawing_active_flag}, Active ID: {active_drawing_session_id}")


@socketio.on('process_image_for_drawing')
def handle_process_image_for_drawing(data):
    global is_drawing_active_flag, active_drawing_session_id
    
    if is_drawing_active_flag: 
        emit('command_response', {'success': False, 'message': "Another drawing is already in progress or active."})
        return

    if active_drawing_session_id: # If there was a previously active (e.g. interrupted) session
        update_drawing_status_in_history(active_drawing_session_id, "aborted_new_drawing")
    active_drawing_session_id = None 

    filepath_on_server = data.get('filepath')
    original_filename = data.get('original_filename', os.path.basename(filepath_on_server or "unknown_image"))
    if not filepath_on_server or not os.path.exists(filepath_on_server): 
        emit('command_response', {'success': False, 'message': f"File not found: {filepath_on_server}"}); return
    
    canny_t1, canny_t2 = data.get('canny_t1', config.DEFAULT_CANNY_THRESHOLD1), data.get('canny_t2', config.DEFAULT_CANNY_THRESHOLD2)
    logging.info(f"API: Processing image for new drawing: '{original_filename}' with T1={canny_t1}, T2={canny_t2}")
    emit('drawing_status_update', {'active': True, 'message': f"Processing '{original_filename}'...", 'resumable': False}) 

    try:
        robot_commands_tuples = process_image_to_robot_commands_pipeline(filepath_on_server, canny_t1, canny_t2)
        if not robot_commands_tuples: 
            emit('command_response', {'success': False, 'message': f"No drawing commands for '{original_filename}'."})
            emit('drawing_status_update', {'active': False, 'message': f"Failed to process '{original_filename}'.", 'resumable': False})
            return
        
        num_cmds = len(robot_commands_tuples)
        drawing_id = f"draw_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        
        new_drawing_data = {
            'drawing_id': drawing_id,
            'filepath_on_server': filepath_on_server,
            'original_filename': original_filename,
            'robot_commands_tuples': robot_commands_tuples,
            'current_command_index': 0,
            'total_commands': num_cmds,
            'canny_t1': canny_t1,
            'canny_t2': canny_t2,
            'status': 'pending_execution', # Initial status before _execute_drawing_commands
            'timestamp': datetime.now().isoformat()
        }
        add_or_update_drawing_in_history(new_drawing_data) 
        
        emit('drawing_status_update', {'active': True, 'message': f"Generated {num_cmds} commands. Preparing to draw '{original_filename}'.", 'resumable': True, 'drawing_id': drawing_id, 'original_filename': original_filename, 'progress': 0})
        emit('drawing_history_updated', get_ui_history_summary(drawing_history))
        
        _execute_drawing_commands(drawing_id) 

    except Exception as e: 
        logging.error(f"Error in initial processing for drawing '{original_filename}': {e}", exc_info=True)
        emit('command_response', {'success': False, 'message': f"Error processing image: {e}"})
        emit('drawing_status_update', {'active': False, 'message': f"Error processing '{original_filename}'.", 'resumable': False})
        active_drawing_session_id = None 
        is_drawing_active_flag = False

@socketio.on('resume_drawing_request')
def handle_resume_drawing_request(data):
    global is_drawing_active_flag, active_drawing_session_id
    drawing_id_to_resume = data.get('drawing_id')
    logging.info(f"--- API: Event 'resume_drawing_request' RECEIVED for drawing_id: {drawing_id_to_resume} ---")

    if is_drawing_active_flag:
        logging.warning(f"Resume requested for {drawing_id_to_resume}, but a drawing ('{active_drawing_session_id}') is already active.")
        emit('drawing_status_update', {'active': True, 'message': "Cannot resume, another drawing is currently active.", 'resumable': True if active_drawing_session_id else False, 'drawing_id': active_drawing_session_id})
        return

    session_to_resume = get_drawing_from_history(drawing_id_to_resume)

    if session_to_resume:
        if session_to_resume.get('status') == 'completed':
            logging.info(f"Drawing '{session_to_resume['original_filename']}' is already completed. Cannot resume.")
            emit('drawing_status_update', {'active': False, 'message': f"Drawing '{session_to_resume['original_filename']}' is already completed.", 'resumable': False, 'drawing_id': drawing_id_to_resume})
            return
        
        logging.info(f"Attempting to resume drawing of '{session_to_resume['original_filename']}' from command {session_to_resume['current_command_index'] + 1}")
        # Status will be updated by _execute_drawing_commands
        emit('drawing_status_update', {
            'active': True, 
            'message': f"Resuming drawing of '{session_to_resume['original_filename']}'...",
            'resumable': True,
            'drawing_id': drawing_id_to_resume,
            'original_filename': session_to_resume['original_filename'],
            'progress': (session_to_resume['current_command_index'] / session_to_resume['total_commands']) * 100 if session_to_resume['total_commands'] > 0 else 0
        })
        # No need to emit history here, _execute_drawing_commands will handle it via add_or_update
        _execute_drawing_commands(drawing_id_to_resume) 
    else:
        logging.warning(f"Resume requested for drawing_id {drawing_id_to_resume}, but no such drawing state found in history.")
        emit('drawing_status_update', {'active': False, 'message': "Drawing session to resume not found.", 'resumable': False})

@socketio.on('restart_drawing_request')
def handle_restart_drawing_request(data):
    global is_drawing_active_flag, active_drawing_session_id, drawing_history
    drawing_id_to_restart = data.get('drawing_id')
    logging.info(f"--- API: Event 'restart_drawing_request' RECEIVED for drawing_id: {drawing_id_to_restart} ---")

    if is_drawing_active_flag:
        logging.warning(f"Restart requested for {drawing_id_to_restart}, but a drawing ('{active_drawing_session_id}') is already active.")
        emit('drawing_status_update', {'active': True, 'message': "Cannot restart, another drawing is currently active.", 'resumable': True if active_drawing_session_id else False, 'drawing_id': active_drawing_session_id})
        return

    session_to_restart = get_drawing_from_history(drawing_id_to_restart)
    if session_to_restart:
        logging.info(f"Restarting drawing of '{session_to_restart['original_filename']}' from the beginning.")
        session_to_restart['current_command_index'] = 0
        session_to_restart['status'] = 'pending_restart' # Mark for restart
        add_or_update_drawing_in_history(session_to_restart.copy()) 

        emit('drawing_status_update', {
            'active': True, 
            'message': f"Restarting drawing of '{session_to_restart['original_filename']}'...",
            'resumable': True, 
            'drawing_id': drawing_id_to_restart,
            'original_filename': session_to_restart['original_filename'],
            'progress': 0
        })
        emit('drawing_history_updated', get_ui_history_summary(drawing_history))
        _execute_drawing_commands(drawing_id_to_restart)
    else:
        logging.warning(f"Restart requested for drawing_id {drawing_id_to_restart}, but no such drawing state found in history.")
        emit('drawing_status_update', {'active': False, 'message': "Drawing session to restart not found.", 'resumable': False})


if __name__ == '__main__':
    server_port = 5555 
    app.config['SERVER_PORT'] = server_port 
    logging.info(f"Starting Python backend server (SocketIO with Flask) on port {server_port}...")
    socketio.run(app, host='0.0.0.0', port=server_port, debug=True, use_reloader=False)

