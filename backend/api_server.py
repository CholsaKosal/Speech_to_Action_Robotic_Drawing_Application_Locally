# backend/api_server.py
from flask import Flask, request, render_template_string, jsonify
from flask_socketio import SocketIO, emit
import config
from image_processing_engine import process_image_to_robot_commands_pipeline, get_canny_edges_array
from voice_assistant import transcribe_audio, load_whisper_model, load_llm_model, process_command_with_llm_stream

import os
import uuid
import qrcode
from io import BytesIO
import base64
import logging
import cv2
import json
from datetime import datetime
import threading
import queue 
import socket

# --- Basic Setup and Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_here!'
BASE_DIR = os.path.dirname(__file__)
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, config.QR_UPLOAD_FOLDER)
app.config['AUDIO_TEMP_FOLDER_PATH'] = os.path.join(BASE_DIR, config.AUDIO_TEMP_FOLDER)
ASSETS_DIR = os.path.join(BASE_DIR, config.ASSETS_FOLDER_NAME)

for folder_path in [app.config['UPLOAD_FOLDER'], app.config['AUDIO_TEMP_FOLDER_PATH'], ASSETS_DIR]:
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

SIGNATURE_IMAGE_FULL_PATH = os.path.join(ASSETS_DIR, config.SIGNATURE_IMAGE_FILENAME)
DRAWING_HISTORY_FILE = os.path.join(BASE_DIR, "drawing_history.json")
MAX_DRAWING_HISTORY = 10

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=10 * 1024 * 1024)

command_queue = queue.Queue()
result_queue = queue.Queue()

drawing_history = []
active_drawing_session_id = None
is_drawing_flag_for_ui = False

logging.info("--- Initializing AI Models ---")
if load_whisper_model(): logging.info("Whisper model loaded successfully.")
else: logging.error("Whisper model FAILED to load.")
if load_llm_model(): logging.info("LLM model loaded successfully.")
else: logging.error("LLM model FAILED to load.")
logging.info("--- AI Model Initialization Complete ---")

# --- History and Utility Functions (Managed by Fn1) ---
def get_ui_history_summary(history_list):
    """Creates a simplified summary of drawing history for the frontend."""
    summary = []
    for item in history_list:
        total_commands = item.get('total_commands', 1) 
        if total_commands == 0: total_commands = 1
        current_index = item.get('current_command_index', 0)
        progress = (current_index / total_commands) * 100 if item.get('status') != 'completed' else 100
        
        summary.append({
            'drawing_id': item.get('drawing_id'), 
            'original_filename': item.get('original_filename'),
            'status': item.get('status', 'unknown'), 
            'last_updated': item.get('last_updated'),
            'total_commands': item.get('total_commands', 0),
            'progress': progress
        })
    return summary

def save_drawing_history():
    """Saves the current drawing history to a JSON file."""
    global drawing_history
    with threading.Lock():
        try:
            with open(DRAWING_HISTORY_FILE, 'w') as f:
                json.dump(drawing_history, f, indent=4)
        except IOError as e:
            logging.error(f"Error saving drawing history: {e}")

def load_drawing_history():
    """Loads drawing history from a JSON file upon server start."""
    global drawing_history
    if os.path.exists(DRAWING_HISTORY_FILE):
        try:
            with open(DRAWING_HISTORY_FILE, 'r') as f:
                history_data = json.load(f)
                if isinstance(history_data, list):
                    for item in history_data:
                        if item.get('status', '').startswith('in_progress'):
                            item['status'] = 'interrupted_server_restart'
                    drawing_history = history_data[:MAX_DRAWING_HISTORY]
        except Exception as e:
            logging.error(f"Error loading drawing history: {e}.")
            drawing_history = []

def update_drawing_history(drawing_id, status=None, index=None):
    """General purpose function to update a history item."""
    global drawing_history
    item = next((item for item in drawing_history if item.get('drawing_id') == drawing_id), None)
    if item:
        if status is not None:
            item['status'] = status
        if index is not None:
            item['current_command_index'] = index
        item['last_updated'] = datetime.now().isoformat()
        save_drawing_history()
        socketio.emit('drawing_history_updated', get_ui_history_summary(drawing_history))
        return True
    return False

load_drawing_history()

def result_processor_thread():
    """This thread function handles results from the RobotWorker (Fn2)."""
    global is_drawing_flag_for_ui, active_drawing_session_id
    logging.info("Result processor thread started.")
    while True:
        try:
            result = result_queue.get()
            result_type = result.get('type')
            data = result.get('data', {})
            
            if result_type != 'drawing_progress':
                logging.info(f"Fn1 received result from Fn2: Type='{result_type}'")

            with app.app_context():
                if result_type == 'connection_status':
                    socketio.emit('robot_connection_status', data)
                elif result_type == 'move_completed':
                    socketio.emit('command_response', data)
                elif result_type == 'drawing_progress':
                    progress = (data.get('current_command_index', 0) / data.get('total_commands', 1)) * 100
                    socketio.emit('drawing_status_update', {
                        'active': True, 'drawing_id': data.get('drawing_id'),
                        'message': f"Drawing command {data.get('current_command_index')} of {data.get('total_commands')}",
                        'progress': progress
                    })
                elif result_type == 'drawing_finished':
                    drawing_id = data.get('drawing_id')
                    if drawing_id:
                        history_item = next((h for h in drawing_history if h['drawing_id'] == drawing_id), None)
                        if history_item:
                            update_drawing_history(drawing_id, status='completed', index=history_item.get('total_commands', 0))
                    socketio.emit('drawing_completed', {'drawing_id': drawing_id, 'message': data['message']})
                    is_drawing_flag_for_ui = False
                    active_drawing_session_id = None
                elif result_type == 'error':
                    drawing_id = data.get('drawing_id')
                    failed_index = data.get('failed_index')
                    if drawing_id:
                        update_drawing_history(drawing_id, status='interrupted_error', index=failed_index)
                        socketio.emit('drawing_aborted', {'drawing_id': drawing_id, 'message': f"Drawing interrupted: {data.get('message')}"})
                    else:
                         socketio.emit('command_response', {'success': False, 'message': f"Robot Worker Error: {data.get('message')}"})
                    is_drawing_flag_for_ui = False
                    active_drawing_session_id = None
        except Exception as e:
            logging.error(f"Error in result_processor_thread: {e}", exc_info=True)

current_upload_session_id = None
UPLOAD_PAGE_TEMPLATE = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Upload Image</title><style>body{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;background-color:#f0f0f0}.container{background-color:white;padding:20px;border-radius:8px;box-shadow:0 0 10px rgba(0,0,0,.1);text-align:center}input[type=file]{margin-bottom:15px;display:block;margin-left:auto;margin-right:auto}button{padding:10px 15px;background-color:#007bff;color:white;border:none;border-radius:4px;cursor:pointer;font-size:1em}button:hover{background-color:#0056b3}#message{margin-top:15px;font-weight:700}h2{margin-top:0}</style></head><body><div class=container><h2>Select Image to Upload</h2><form id=uploadForm method=post enctype=multipart/form-data><input type=file name=image id=imageFile accept=image/* required><button type=submit>Upload</button></form><div id=message></div></div><script>document.getElementById("uploadForm").addEventListener("submit",async function(e){e.preventDefault();const t=new FormData(this),s=document.getElementById("message"),a=this.querySelector('button[type="submit"]'),i=this.querySelector('input[type="file"]');s.textContent="Uploading...",a.disabled=!0,i.disabled=!0;try{const e=await fetch(window.location.href,{method:"POST",body:t}),n=await e.json();e.ok?(s.textContent="Success: "+n.message+". You can close this page.",s.style.color="green"):(s.textContent="Error: "+(n.error||"Upload failed. Please try again."),s.style.color="red",a.disabled=!1,i.disabled=!1)}catch(e){s.textContent="Network Error: "+e.message+". Please try again.",s.style.color="red",a.disabled=!1,i.disabled=!1}})</script></body></html>
"""
@app.route('/qr_upload_page/<session_id>', methods=['GET', 'POST'])
def handle_qr_upload_page(session_id):
    global current_upload_session_id
    if session_id != current_upload_session_id: return "Invalid or expired upload session.", 403
    if request.method == 'POST':
        file = request.files.get('image')
        if not file or file.filename == '': return jsonify({"error": "No selected file"}), 400
        original_filename = file.filename
        _, f_ext = os.path.splitext(original_filename)
        if f_ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']: return jsonify({"error": "Invalid file type."}), 400
        filename_on_server = str(uuid.uuid4()) + f_ext
        filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
        try:
            file.save(filepath_on_server)
            if is_drawing_flag_for_ui:
                command_queue.put({'action': 'abort_drawing', 'data': {'reason': 'new_image_upload'}})
            socketio.emit('qr_image_received', { 'success': True, 'message': f"Image '{original_filename}' uploaded.", 'original_filename': original_filename, 'filepath_on_server': filepath_on_server})
            current_upload_session_id = None
            return jsonify({"message": f"Image '{original_filename}' uploaded successfully!"}), 200
        except Exception as e:
            socketio.emit('qr_image_received', {'success': False, 'message': f"Error saving '{original_filename}'."})
            return jsonify({"error": "Failed to save file on server."}), 500
    return render_template_string(UPLOAD_PAGE_TEMPLATE)

@socketio.on('connect')
def handle_connect():
    logging.info(f"Client connected: {request.sid}")
    emit('response', {'data': 'Connected to Python backend!'})
    command_queue.put({'action': 'get_status'})
    emit('drawing_history_updated', get_ui_history_summary(drawing_history))

@socketio.on('disconnect')
def handle_disconnect():
    logging.info(f"Client disconnected: {request.sid}")

@socketio.on('robot_connect_request')
def handle_robot_connect_request(data):
    if is_drawing_flag_for_ui:
        emit('robot_connection_status', {'success': False, 'message': 'Cannot connect while drawing is active.'})
        return
    command_queue.put({'action': 'connect', 'data': data})

@socketio.on('robot_disconnect_request')
def handle_robot_disconnect_request(data):
    if is_drawing_flag_for_ui:
        emit('robot_connection_status', {'success': False, 'message': 'Cannot disconnect while drawing is active.'})
        return
    command_queue.put({'action': 'disconnect', 'data': data})

def check_and_abort_active_drawing(reason="Manual command override"):
    global is_drawing_flag_for_ui, active_drawing_session_id
    if is_drawing_flag_for_ui:
        logging.warning(f"{reason} received, aborting '{active_drawing_session_id}'.")
        command_queue.put({'action': 'abort_drawing', 'data': {'reason': reason}})
        is_drawing_flag_for_ui = False
        active_drawing_session_id = None
        return True
    return False

@socketio.on('send_robot_command')
def handle_send_robot_command(json_data):
    check_and_abort_active_drawing("manual_move_command")
    command_queue.put({'action': 'move', 'data': json_data})

@socketio.on('send_custom_coordinates')
def handle_send_custom_coordinates(data):
    check_and_abort_active_drawing("manual_coordinate_input")
    command_queue.put({'action': 'move_custom', 'data': data})

@socketio.on('direct_image_upload')
def handle_direct_image_upload(data):
    check_and_abort_active_drawing("new_direct_image_upload")
    original_filename = data.get('filename')
    base64_data = data.get('fileData')
    if not original_filename or not base64_data:
        emit('direct_image_upload_response', {'success': False, 'message': 'Missing data.'})
        return
    try:
        image_data = base64.b64decode(base64_data)
        _, f_ext = os.path.splitext(original_filename)
        filename_on_server = str(uuid.uuid4()) + f_ext
        filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
        with open(filepath_on_server, 'wb') as f: f.write(image_data)
        emit('direct_image_upload_response', { 'success': True, 'message': f"Image '{original_filename}' uploaded.", 'original_filename': original_filename, 'filepath_on_server': filepath_on_server })
    except Exception as e:
        emit('direct_image_upload_response', {'success': False, 'message': f"Server error: {e}"})

def _get_commands_for_drawing_from_file(filepath, canny_t1, canny_t2):
    """Helper to generate full command list (drawing + signature) from a file."""
    try:
        robot_commands = process_image_to_robot_commands_pipeline(filepath, canny_t1, canny_t2, optimize=True)
        if os.path.exists(SIGNATURE_IMAGE_FULL_PATH):
            signature_commands = process_image_to_robot_commands_pipeline(
                SIGNATURE_IMAGE_FULL_PATH, 
                config.SIGNATURE_CANNY_THRESHOLD1, 
                config.SIGNATURE_CANNY_THRESHOLD2, 
                optimize=True
            )
            if signature_commands:
                robot_commands.extend(signature_commands)
        return robot_commands
    except Exception as e:
        logging.error(f"Failed to generate commands from file {filepath}: {e}")
        return None

def _get_commands_for_drawing(drawing_id):
    """Helper to retrieve or regenerate commands for a drawing from history."""
    history_item = next((item for item in drawing_history if item.get('drawing_id') == drawing_id), None)
    if not history_item: 
        logging.error(f"Could not find drawing_id {drawing_id} in history.")
        return None
    filepath = history_item.get('filepath_on_server')
    canny_t1 = history_item.get('canny_t1', config.DEFAULT_CANNY_THRESHOLD1)
    canny_t2 = history_item.get('canny_t2', config.DEFAULT_CANNY_THRESHOLD2)
    if not filepath or not os.path.exists(filepath):
        logging.error(f"Filepath '{filepath}' for drawing {drawing_id} not found.")
        return None
    return _get_commands_for_drawing_from_file(filepath, canny_t1, canny_t2)

@socketio.on('resume_drawing_request')
def handle_resume_drawing(data):
    global is_drawing_flag_for_ui, active_drawing_session_id
    if is_drawing_flag_for_ui:
        emit('command_response', {'success': False, 'message': "Another drawing is active."}); return
    drawing_id = data.get('drawing_id')
    history_item = next((item for item in drawing_history if item.get('drawing_id') == drawing_id), None)
    if not history_item:
        emit('command_response', {'success': False, 'message': f"Drawing ID {drawing_id} not found."}); return
    commands = _get_commands_for_drawing(drawing_id)
    if not commands:
        emit('command_response', {'success': False, 'message': f"Could not get commands for {drawing_id}."}); return
    start_index = history_item.get('current_command_index', 0)
    logging.info(f"Resuming drawing '{drawing_id}' from index {start_index}.")
    is_drawing_flag_for_ui = True
    active_drawing_session_id = drawing_id
    update_drawing_history(drawing_id, status='in_progress_resumed')
    command_queue.put({'action': 'draw', 'data': {'commands': commands, 'drawing_id': drawing_id, 'start_index': start_index}})

@socketio.on('restart_drawing_request')
def handle_restart_drawing(data):
    global is_drawing_flag_for_ui, active_drawing_session_id
    if is_drawing_flag_for_ui:
        emit('command_response', {'success': False, 'message': "Another drawing is active."}); return
    drawing_id = data.get('drawing_id')
    commands = _get_commands_for_drawing(drawing_id)
    if not commands:
        emit('command_response', {'success': False, 'message': f"Could not get commands for {drawing_id}."}); return
    logging.info(f"Restarting drawing '{drawing_id}'.")
    is_drawing_flag_for_ui = True
    active_drawing_session_id = drawing_id
    update_drawing_history(drawing_id, status='in_progress_restarted', index=0)
    command_queue.put({'action': 'draw', 'data': {'commands': commands, 'drawing_id': drawing_id, 'start_index': 0}})

@socketio.on('process_image_for_drawing')
def handle_process_image_for_drawing(data):
    global is_drawing_flag_for_ui, active_drawing_session_id, drawing_history
    if check_and_abort_active_drawing("new_drawing_request"):
        socketio.sleep(0.5)
    filepath = data.get('filepath')
    original_filename = data.get('original_filename', os.path.basename(filepath or "unknown"))
    canny_t1, canny_t2 = data.get('canny_t1'), data.get('canny_t2')
    if not filepath or not os.path.exists(filepath):
        emit('command_response', {'success': False, 'message': f"File not found: {filepath}"}); return
    try:
        robot_commands = _get_commands_for_drawing_from_file(filepath, canny_t1, canny_t2)
        if not robot_commands:
            emit('command_response', {'success': False, 'message': f"No drawing paths found in '{original_filename}'."}); return
        
        total_commands = len(robot_commands)
        drawing_id = f"draw_{int(datetime.now().timestamp())}"
        active_drawing_session_id = drawing_id
        is_drawing_flag_for_ui = True
        
        history_item = {
            'drawing_id': drawing_id, 'filepath_on_server': filepath, 'original_filename': original_filename,
            'status': 'in_progress', 'total_commands': total_commands, 'canny_t1': canny_t1, 'canny_t2': canny_t2,
            'current_command_index': 0
        }
        drawing_history.insert(0, history_item)
        drawing_history = drawing_history[:MAX_DRAWING_HISTORY]
        update_drawing_history(drawing_id, status='in_progress')

        command_queue.put({'action': 'draw', 'data': {'commands': robot_commands, 'drawing_id': drawing_id, 'start_index': 0}})
    except Exception as e:
        logging.error(f"Error processing image for drawing: {e}", exc_info=True)
        emit('command_response', {'success': False, 'message': f"Server error during image processing: {e}"})
        is_drawing_flag_for_ui = False; active_drawing_session_id = None

@socketio.on('request_qr_code')
def handle_request_qr_code(data):
    global current_upload_session_id
    if is_drawing_flag_for_ui:
        emit('qr_code_data', {'error': 'A drawing is currently in progress.'}); return
    check_and_abort_active_drawing("new_qr_request")
    session_id = uuid.uuid4().hex
    current_upload_session_id = session_id
    host_ip = '127.0.0.1'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); host_ip = s.getsockname()[0]; s.close()
    except Exception: pass

    # *** UPDATED: Generate URL with https scheme ***
    protocol = "https" if os.path.exists('cert.pem') else "http"
    upload_url = f"{protocol}://{host_ip}:{app.config.get('SERVER_PORT', 5555)}/qr_upload_page/{session_id}"

    qr_img = qrcode.make(upload_url)
    buffered = BytesIO()
    qr_img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    emit('qr_code_data', {'qr_image_base64': img_str, 'upload_url': upload_url})

@socketio.on('request_threshold_preview')
def handle_request_threshold_preview(data):
    filepath, t1, t2 = data.get('filepath'), data.get('t1'), data.get('t2')
    if not filepath or not os.path.exists(filepath): emit('threshold_preview_image_response', {'error': 'Invalid data.'}); return
    try:
        edges_array = get_canny_edges_array(filepath, int(t1), int(t2))
        if edges_array is not None:
            _, buffer = cv2.imencode('.png', edges_array); img_base64 = base64.b64encode(buffer).decode('utf-8')
            emit('threshold_preview_image_response', {'image_base64': img_base64})
        else: emit('threshold_preview_image_response', {'error': 'Failed to generate preview.'})
    except Exception as e:
        emit('threshold_preview_image_response', {'error': f'Server error: {e}'})

@socketio.on('audio_chunk')
def handle_audio_chunk(data):
    audio_data_b64 = data.get('audioData')
    if not audio_data_b64: return
    try:
        audio_bytes = base64.b64decode(audio_data_b64)
        temp_audio_filepath = os.path.join(app.config['AUDIO_TEMP_FOLDER_PATH'], f"voice_cmd_{uuid.uuid4()}.webm")
        with open(temp_audio_filepath, 'wb') as f: f.write(audio_bytes)
        transcribed_text = transcribe_audio(temp_audio_filepath)
        if transcribed_text is not None: emit('transcription_result', {'text': transcribed_text})
        else: emit('transcription_result', {'error': 'Transcription failed.'})
        try: os.remove(temp_audio_filepath)
        except Exception as e: logging.warning(f"Could not remove temp audio file: {e}")
    except Exception as e:
        logging.error(f"Error processing audio chunk: {e}", exc_info=True)

@socketio.on('submit_text_to_llm')
def handle_submit_text_to_llm(data):
    text_command = data.get('text_command')
    if not text_command: return
    try:
        for llm_response_part in process_command_with_llm_stream(text_command):
            if llm_response_part.get("done") and llm_response_part.get("parsed_action"):
                parsed_action = llm_response_part["parsed_action"]
                action_type = parsed_action.get("type")
                if action_type == "move":
                    target = parsed_action.get("parameters", {}).get("target")
                    handle_send_robot_command({'type': 'go_home' if target == 'home' else 'move_to_safe_center'})
                elif action_type == "move_to_coords":
                    handle_send_custom_coordinates(parsed_action.get("parameters", {}))
            emit('llm_response_chunk', llm_response_part)
    except Exception as e:
        logging.error(f"API Error in LLM handler: {e}", exc_info=True)
