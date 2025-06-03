# backend/api_server.py
from flask import Flask, request, render_template_string, jsonify, send_file
from flask_socketio import SocketIO, emit
from robot_interface import RobotInterface
import config # Your existing config
from image_processing_engine import process_image_to_robot_commands_pipeline, get_canny_edges_array # UPDATED IMPORT
# Import STT and LLM functions from voice_assistant
from voice_assistant import transcribe_audio, load_whisper_model, load_llm_model, process_command_with_llm_stream 

import os
import uuid
import qrcode
from io import BytesIO
import base64 
import socket
import time 
import logging 
import cv2 # For image encoding for preview
import numpy as np # For image processing if needed here

# Configure basic logging with timestamps
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'
)
# Reduce verbosity of external libraries if desired
logging.getLogger('engineio.server').setLevel(logging.WARNING) 
logging.getLogger('socketio.server').setLevel(logging.WARNING) 
logging.getLogger('werkzeug').setLevel(logging.WARNING) 


app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_here!' 
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), config.QR_UPLOAD_FOLDER)
app.config['AUDIO_TEMP_FOLDER_PATH'] = os.path.join(os.path.dirname(__file__), config.AUDIO_TEMP_FOLDER)

# Create upload and audio temp directories if they don't exist
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])
    logging.info(f"Created image upload folder at: {app.config['UPLOAD_FOLDER']}")

if not os.path.exists(app.config['AUDIO_TEMP_FOLDER_PATH']):
    os.makedirs(app.config['AUDIO_TEMP_FOLDER_PATH'])
    logging.info(f"Created temporary audio folder at: {app.config['AUDIO_TEMP_FOLDER_PATH']}")

# Load AI models when the application starts
logging.info("--- Initializing AI Models ---")
whisper_model_loaded = load_whisper_model()
if whisper_model_loaded:
    logging.info("Whisper model loaded successfully during startup.")
else:
    logging.error("Whisper model FAILED to load during startup.")

llm_instance_global = load_llm_model() 
if llm_instance_global: 
    logging.info("LLM model loaded successfully during startup.")
else:
    logging.error("LLM model FAILED to load during startup. Check voice_assistant.py and model path in config.py.")
logging.info("--- AI Model Initialization Complete ---")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet',
                    max_http_buffer_size=10 * 1024 * 1024) 
robot = RobotInterface() 

current_upload_session_id = None
is_drawing_active = False 

UPLOAD_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Upload Image</title>
    <style>
        body { font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; background-color: #f0f0f0; }
        .container { background-color: white; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); text-align: center; }
        input[type="file"] { margin-bottom: 15px; display: block; margin-left: auto; margin-right: auto; }
        button { padding: 10px 15px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
        button:hover { background-color: #0056b3; }
        #message { margin-top: 15px; font-weight: bold; }
        h2 { margin-top: 0; }
    </style>
</head>
<body>
    <div class="container">
        <h2>Select Image to Upload</h2>
        <form id="uploadForm" method="post" enctype="multipart/form-data">
            <input type="file" name="image" id="imageFile" accept="image/*" required>
            <button type="submit">Upload</button>
        </form>
        <div id="message"></div>
    </div>
    <script>
        document.getElementById('uploadForm').addEventListener('submit', async function(event) {
            event.preventDefault();
            const formData = new FormData(this);
            const messageDiv = document.getElementById('message');
            const submitButton = this.querySelector('button[type="submit"]');
            const fileInput = this.querySelector('input[type="file"]');
            messageDiv.textContent = 'Uploading...';
            submitButton.disabled = true; fileInput.disabled = true;
            try {
                const response = await fetch(window.location.href, { method: 'POST', body: formData });
                const result = await response.json();
                if (response.ok) {
                    messageDiv.textContent = 'Success: ' + result.message + '. You can close this page.';
                    messageDiv.style.color = 'green';
                } else {
                    messageDiv.textContent = 'Error: ' + (result.error || 'Upload failed. Please try again.');
                    messageDiv.style.color = 'red';
                    submitButton.disabled = false; fileInput.disabled = false;
                }
            } catch (error) {
                messageDiv.textContent = 'Network Error: ' + error.message + '. Please try again.';
                messageDiv.style.color = 'red';
                submitButton.disabled = false; fileInput.disabled = false;
            }
        });
    </script>
</body>
</html>
"""

@app.route('/qr_upload_page/<session_id>', methods=['GET', 'POST'])
def handle_qr_upload_page(session_id):
    global current_upload_session_id
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
                socketio.emit('qr_image_received', { 
                    'success': True, 'message': f"Image '{original_filename}' uploaded via QR.",
                    'original_filename': original_filename, 'filepath_on_server': filepath_on_server
                })
                current_upload_session_id = None 
                return jsonify({"message": f"Image '{original_filename}' uploaded successfully!"}), 200
            except Exception as e:
                logging.error(f"Error saving QR uploaded file: {e}", exc_info=True)
                socketio.emit('qr_image_received', {
                    'success': False, 'message': f"Error saving '{original_filename}' on server.",
                    'original_filename': original_filename
                })
                return jsonify({"error": "Failed to save file on server."}), 500
    return render_template_string(UPLOAD_PAGE_TEMPLATE)

@socketio.on('connect')
def handle_connect():
    logging.info(f"Client connected: {request.sid}")
    emit('response', {'data': 'Connected to Python backend!'})
    emit('robot_connection_status', {'success': robot.is_connected,
                                     'message': 'Connected to robot' if robot.is_connected else 'Not connected to robot'})
    emit('drawing_status_update', {'active': is_drawing_active, 'message': 'Drawing in progress' if is_drawing_active else 'Idle'})

@socketio.on('disconnect')
def handle_disconnect():
    logging.info(f"Client disconnected: {request.sid}")

@socketio.on('robot_connect_request')
def handle_robot_connect_request(json_data):
    global is_drawing_active
    if is_drawing_active:
        emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'})
        return
    success, message = robot.connect_robot()
    emit('robot_connection_status', {'success': success, 'message': message})

@socketio.on('robot_disconnect_request')
def handle_robot_disconnect_request(json_data):
    global is_drawing_active
    if is_drawing_active:
        emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'})
        return
    success, message = robot.disconnect_robot(graceful=True)
    emit('robot_connection_status', {'success': robot.is_connected, 'message': message if success else "Failed to disconnect"})

@socketio.on('send_robot_command') 
def handle_send_robot_command(json_data, triggered_by_llm=False): 
    global is_drawing_active
    if is_drawing_active:
        if triggered_by_llm:
            logging.warning("LLM tried to send command while drawing active.")
        else:
            emit('command_response', {'success': False, 'message': 'Cannot send manual commands while drawing is active.', 'command_sent': json_data.get('type', 'N/A')})
        return False, "Drawing is active." 

    command_type = json_data.get('type', 'raw')
    command_str = json_data.get('command_str') 
    
    if not robot.is_connected and command_type not in ['go_home']: 
        conn_success, conn_message = robot.connect_robot()
        if not conn_success:
            if not triggered_by_llm: 
                emit('command_response', {'success': False, 'message': f'Robot not connected & connection failed: {conn_message}', 'command_sent': command_type})
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
    elif command_type == 'raw' and not command_str:
        message = "No command_str provided for raw command."
        actual_command_sent = "N/A"
    
    if not triggered_by_llm: 
        emit('command_response', {'success': success, 'message': message, 'command_sent': actual_command_sent})
    
    if not robot.is_connected: 
         emit('robot_connection_status', {'success': False, 'message': 'Disconnected (possibly due to command error/timeout)'})
    
    return success, message 


@socketio.on('request_qr_code')
def handle_request_qr_code(data):
    global current_upload_session_id, is_drawing_active
    if is_drawing_active:
        emit('qr_code_data', {'error': 'Drawing is currently active. Cannot generate new QR code.'})
        return
    current_upload_session_id = str(uuid.uuid4())
    host_ip = request.host.split(':')[0]
    if host_ip == '127.0.0.1' or host_ip == 'localhost':
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1); s.connect(("8.8.8.8", 80)); host_ip = s.getsockname()[0]; s.close()
        except Exception as e:
            logging.warning(f"Could not determine local network IP, defaulting to 127.0.0.1: {e}")
            host_ip = '127.0.0.1'
    server_port = app.config.get('SERVER_PORT', 5555) 
    upload_url = f"http://{host_ip}:{server_port}/qr_upload_page/{current_upload_session_id}"
    qr_img = qrcode.make(upload_url); img_io = BytesIO(); qr_img.save(img_io, 'PNG'); img_io.seek(0)
    img_base64 = base64.b64encode(img_io.getvalue()).decode('utf-8')
    emit('qr_code_data', {'qr_image_base64': img_base64, 'upload_url': upload_url})

@socketio.on('direct_image_upload')
def handle_direct_image_upload(data):
    global is_drawing_active
    if is_drawing_active:
        emit('direct_image_upload_response', {'success': False, 'message': 'Cannot upload image while drawing is active.'})
        return
    original_filename = data.get('filename')
    base64_data = data.get('fileData')
    if not original_filename or not base64_data:
        emit('direct_image_upload_response', {'success': False, 'message': 'Missing filename or file data.'})
        return
    try:
        image_data = base64.b64decode(base64_data)
        _, f_ext = os.path.splitext(original_filename)
        if f_ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
            emit('direct_image_upload_response', {'success': False, 'message': f"Invalid file type: {f_ext}", 'original_filename': original_filename})
            return
        filename_on_server = str(uuid.uuid4()) + f_ext
        filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
        with open(filepath_on_server, 'wb') as f: f.write(image_data)
        emit('direct_image_upload_response', {
            'success': True, 'message': f"Image '{original_filename}' uploaded.",
            'original_filename': original_filename, 'filepath_on_server': filepath_on_server
        })
    except base64.binascii.Error:
        logging.error(f"Base64 decoding error for direct image upload: {original_filename}", exc_info=True)
        emit('direct_image_upload_response', {'success': False, 'message': f"Error decoding image data.", 'original_filename': original_filename})
    except Exception as e:
        logging.error(f"Error saving direct uploaded file: {e}", exc_info=True)
        emit('direct_image_upload_response', {'success': False, 'message': f"Failed to save file on server.", 'original_filename': original_filename})


@socketio.on('audio_chunk') 
def handle_audio_chunk(data):
    logging.info(f"--- API: Event 'audio_chunk' RECEIVED with data keys: {list(data.keys())} ---")
    
    audio_data_b64 = data.get('audioData')
    mime_type = data.get('mimeType', 'audio/webm') 
    
    if not audio_data_b64:
        logging.error("API: No audio data (audioData key) in received chunk.")
        emit('transcription_result', {'error': 'No audio data received.'})
        return
    
    logging.info(f"API: Received audio data. Mime type: {mime_type}. Data length (chars): {len(audio_data_b64)}")

    try:
        audio_bytes = base64.b64decode(audio_data_b64)
        logging.info(f"API: Base64 decoded. Byte length: {len(audio_bytes)}")
        
        file_extension = ".webm"
        if 'wav' in mime_type: file_extension = ".wav"
        elif 'mp3' in mime_type: file_extension = ".mp3"
        
        temp_audio_filename = f"voice_cmd_{uuid.uuid4()}{file_extension}"
        temp_audio_filepath = os.path.join(app.config['AUDIO_TEMP_FOLDER_PATH'], temp_audio_filename)
        
        with open(temp_audio_filepath, 'wb') as f: f.write(audio_bytes)
        logging.info(f"API: Temporary audio file saved: {temp_audio_filepath}")

        transcribed_text = transcribe_audio(temp_audio_filepath) 

        if transcribed_text is not None:
            logging.info(f"API: Transcription successful: '{transcribed_text}'")
            emit('transcription_result', {'text': transcribed_text}) 
        else: 
            logging.error("API: Transcription failed.")
            emit('transcription_result', {'error': 'Transcription failed on server.'})

        try:
            os.remove(temp_audio_filepath)
            logging.info(f"API: Temporary audio file removed: {temp_audio_filepath}")
        except Exception as e:
            logging.warning(f"API Warning: Error removing temporary audio file {temp_audio_filepath}: {e}")

    except base64.binascii.Error as b64e:
        logging.error(f"API Error: Base64 decoding failed. {b64e}", exc_info=True)
        emit('transcription_result', {'error': 'Invalid audio data format (base64 decode).'})
    except Exception as e:
        logging.error(f"API Error: Error processing audio chunk: {e}", exc_info=True) 
        emit('transcription_result', {'error': f'Server error processing audio.'})


@socketio.on('submit_text_to_llm')
def handle_submit_text_to_llm(data):
    logging.info(f"--- API: Event 'submit_text_to_llm' RECEIVED with data: {data} ---") 
    
    text_command = data.get('text_command')
    if not text_command:
        logging.error("API: No text_command in 'submit_text_to_llm' event.")
        emit('llm_response_chunk', {'error': 'No text command received by server.', 'done': True})
        return

    logging.info(f"API: Processing text command for LLM: '{text_command}'")
    
    parsed_action_command_from_llm = None
    try:
        for llm_response_part in process_command_with_llm_stream(text_command): 
            emit('llm_response_chunk', llm_response_part) 
            if llm_response_part.get("done"):
                logging.info("API: LLM stream finished for text command.")
                if llm_response_part.get("parsed_action"):
                    parsed_action_command_from_llm = llm_response_part["parsed_action"]
                break 
        
        if parsed_action_command_from_llm:
            logging.info(f"API: Processing parsed action from LLM (text_command): {parsed_action_command_from_llm}")
            action_type = parsed_action_command_from_llm.get("type")
            parameters = parsed_action_command_from_llm.get("parameters", {})
            global is_drawing_active 
            if is_drawing_active:
                logging.warning(f"API: Drawing is active. LLM command '{action_type}' from text input will not be executed now.")
            elif action_type == "move":
                target = parameters.get("target")
                if target == "home":
                    logging.info("API: Executing LLM command (text_command): move home")
                    handle_send_robot_command({'type': 'go_home'}, triggered_by_llm=True)
                elif target == "center":
                    logging.info("API: Executing LLM command (text_command): move to safe center")
                    handle_send_robot_command({'type': 'move_to_safe_center'}, triggered_by_llm=True)
                else:
                    logging.warning(f"API: LLM move command (text_command) with unknown target: {target}")
            elif action_type == "move_to_coords":
                x = parameters.get("x")
                y = parameters.get("y") # This is Python Z (depth) from LLM
                z = parameters.get("z") # This is Python Y (side-to-side) from LLM
                logging.info(f"API: Executing LLM command (text_command): move to coords X={x}, Y(depth)={y}, Z(side)={z}")
                if x is not None and y is not None and z is not None:
                    # The robot.move_to_position_py expects (x_py, z_py, y_py)
                    # LLM's "y" is our z_py (depth), LLM's "z" is our y_py (side-to-side)
                    handle_send_robot_command({'type': 'raw', 'command_str': robot._format_command(x, y, z)}, triggered_by_llm=True)
                else:
                    logging.warning(f"API: LLM move_to_coords command missing one or more coordinates: {parameters}")

            elif action_type == "draw_request_clarification": 
                logging.info(f"API: LLM requested drawing clarification: {parameters.get('details')}")
            else:
                logging.info(f"API: LLM (text_command) identified action type '{action_type}', but no specific robot action handler implemented yet.")
    
    except NameError as ne: 
        logging.error(f"API Error in handle_submit_text_to_llm: NameError - {ne}. Check imports.", exc_info=True)
        emit('llm_response_chunk', {'error': f'Server configuration error (NameError). Cannot process command.', 'done': True})
    except Exception as e:
        logging.error(f"API Error in handle_submit_text_to_llm during LLM processing: {e}", exc_info=True)
        emit('llm_response_chunk', {'error': f'Server error processing text command: {e}', 'done': True})

@socketio.on('send_custom_coordinates')
def handle_send_custom_coordinates_event(data):
    logging.info(f"--- API: Event 'send_custom_coordinates' RECEIVED with data: {data} ---")
    global is_drawing_active
    if is_drawing_active:
        emit('command_response', {'success': False, 'message': 'Cannot send custom coordinates while drawing is active.'})
        return

    if not robot.is_connected:
        emit('command_response', {'success': False, 'message': 'Robot not connected.'})
        return

    try:
        x_py = float(data.get('x_py'))
        z_py = float(data.get('z_py')) # This is pen height/depth from frontend Y
        y_py = float(data.get('y_py')) # This is side-to-side on paper from frontend Z

        logging.info(f"API: Attempting to move to custom coordinates: X_py={x_py}, Z_py(depth)={z_py}, Y_py(side)={y_py}")
        
        success, message = robot.move_to_position_py(x_py, z_py, y_py)
        
        emit('command_response', {'success': success, 'message': message, 'command_sent': f'Custom Coords: X={x_py}, Depth={z_py}, Side={y_py}'})
        
        if not robot.is_connected: 
             emit('robot_connection_status', {'success': False, 'message': 'Disconnected (possibly due to command error/timeout)'})

    except (TypeError, ValueError) as e:
        logging.error(f"API Error: Invalid coordinate data received: {data} - {e}", exc_info=True)
        emit('command_response', {'success': False, 'message': f'Invalid coordinate data: {e}'})
    except Exception as e:
        logging.error(f"API Error in handle_send_custom_coordinates_event: {e}", exc_info=True)
        emit('command_response', {'success': False, 'message': f'Server error: {e}'})


@socketio.on('request_threshold_preview')
def handle_request_threshold_preview(data):
    logging.info(f"--- API: Event 'request_threshold_preview' RECEIVED with data: {data} ---")
    filepath = data.get('filepath')
    t1 = data.get('t1')
    t2 = data.get('t2')

    if not filepath or not os.path.exists(filepath):
        logging.error(f"API: Filepath for preview not found or invalid: {filepath}")
        emit('threshold_preview_image_response', {'error': 'File not found for preview.'})
        return
    if t1 is None or t2 is None:
        logging.error(f"API: Thresholds t1 or t2 missing for preview. Got t1={t1}, t2={t2}")
        emit('threshold_preview_image_response', {'error': 'Thresholds not provided for preview.'})
        return
    
    try:
        t1 = int(t1)
        t2 = int(t2)
    except ValueError:
        logging.error(f"API: Invalid threshold values for preview (not integers). t1={t1}, t2={t2}")
        emit('threshold_preview_image_response', {'error': 'Invalid threshold values.'})
        return

    try:
        # Use the new get_canny_edges_array function from image_processing_engine
        edges_array = get_canny_edges_array(filepath, t1, t2) # UPDATED CALL

        if edges_array is not None:
            _, buffer = cv2.imencode('.png', edges_array)
            img_base64 = base64.b64encode(buffer).decode('utf-8')
            logging.info(f"API: Successfully generated preview for T1={t1}, T2={t2}")
            emit('threshold_preview_image_response', {'image_base64': img_base64})
        else:
            logging.error(f"API: Failed to generate Canny edges for preview (returned None). File: {filepath}, T1={t1}, T2={t2}")
            emit('threshold_preview_image_response', {'error': 'Failed to generate preview edges.'})
            
    except Exception as e:
        logging.error(f"API Error generating threshold preview: {e}", exc_info=True)
        emit('threshold_preview_image_response', {'error': f'Server error generating preview: {e}'})


@socketio.on('process_image_for_drawing')
def handle_process_image_for_drawing(data):
    global is_drawing_active
    if is_drawing_active:
        emit('command_response', {'success': False, 'message': "Another drawing is already in progress."})
        return
    filepath_on_server = data.get('filepath')
    original_filename = data.get('original_filename', os.path.basename(filepath_on_server or "unknown_image"))
    if not filepath_on_server or not os.path.exists(filepath_on_server):
        emit('command_response', {'success': False, 'message': f"File not found: {filepath_on_server}"})
        return
    
    emit('drawing_status_update', {'active': True, 'message': f"Processing '{original_filename}'..."})
    is_drawing_active = True
    
    canny_t1 = data.get('canny_t1', config.DEFAULT_CANNY_THRESHOLD1) 
    canny_t2 = data.get('canny_t2', config.DEFAULT_CANNY_THRESHOLD2)
    logging.info(f"API: Processing image for drawing with T1={canny_t1}, T2={canny_t2}")


    try:
        robot_commands_tuples = process_image_to_robot_commands_pipeline(
            filepath_on_server, canny_thresh1=canny_t1, canny_thresh2=canny_t2
        )
        if not robot_commands_tuples:
            emit('command_response', {'success': False, 'message': f"No drawing commands for '{original_filename}'."})
            is_drawing_active = False 
            emit('drawing_status_update', {'active': False, 'message': f"Failed to process '{original_filename}'."})
            return
        
        num_cmds = len(robot_commands_tuples)
        emit('drawing_status_update', {'active': True, 'message': f"Generated {num_cmds} commands. Preparing to draw..."})
        
        if not robot.is_connected:
            conn_success, conn_msg = robot.connect_robot()
            if not conn_success:
                emit('command_response', {'success': False, 'message': f"Robot connection failed: {conn_msg}"})
                is_drawing_active = False
                emit('drawing_status_update', {'active': False, 'message': f"Drawing failed (robot connection)."})
                return
            emit('robot_connection_status', {'success': True, 'message': conn_msg})
        
        safe_x, safe_z, safe_y = config.SAFE_ABOVE_CENTER_PY
        success_safe, msg_safe = robot.move_to_position_py(safe_x, safe_z, safe_y)
        if not success_safe:
            logging.error(f"Failed to move to safe position before drawing: {msg_safe}")
            emit('command_response', {'success': False, 'message': f"Failed to move to safe start: {msg_safe}. Aborted."})
            emit('drawing_status_update', {'active': False, 'message': f"Drawing aborted (safe start failed)."})
            robot.go_home() 
            is_drawing_active = False
            return

        for i, cmd_tuple in enumerate(robot_commands_tuples):
            x_py, z_py, y_py = cmd_tuple 
            formatted_cmd_str = robot._format_command(x_py, z_py, y_py) 
            progress_message = f"Drawing '{original_filename}': Cmd {i+1}/{num_cmds}"
            emit('drawing_status_update', {'active': True, 'message': progress_message, 'progress': (i+1)/num_cmds * 100})
            
            success, msg = robot.send_command_raw(formatted_cmd_str)
            if not success:
                error_message = f"Error cmd {i+1} ({formatted_cmd_str}): {msg}. Aborted."
                emit('command_response', {'success': False, 'message': error_message})
                emit('drawing_status_update', {'active': False, 'message': f"Drawing aborted."})
                robot.go_home() 
                is_drawing_active = False
                return 
            socketio.sleep(0.0005) # Very Small delay between commands for drawing smoothness 
            
        emit('command_response', {'success': True, 'message': f"Sent all {num_cmds} commands for '{original_filename}'."})
        emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' complete."})
        robot.go_home() 

    except Exception as e:
        logging.error(f"Error in drawing pipeline: {e}", exc_info=True)
        emit('command_response', {'success': False, 'message': f"Error in drawing pipeline: {e}"})
        emit('drawing_status_update', {'active': False, 'message': f"Error processing/drawing."})
        robot.go_home() 
    finally:
        is_drawing_active = False


if __name__ == '__main__':
    server_port = 5555 
    app.config['SERVER_PORT'] = server_port 

    logging.info(f"Starting Python backend server (SocketIO with Flask) on port {server_port}...")
    logging.info(f"Frontend should connect to ws://localhost:{server_port}")
    logging.info(f"QR code upload page will be accessible via http://<YOUR_LOCAL_IP>:{server_port}/qr_upload_page/<session_id>")
    
    socketio.run(app, host='0.0.0.0', port=server_port, debug=True, use_reloader=False)
