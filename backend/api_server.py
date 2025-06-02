# backend/api_server.py
from flask import Flask, request, render_template_string, jsonify, send_file
from flask_socketio import SocketIO, emit
from robot_interface import RobotInterface
import config # Your existing config
from image_processing_engine import process_image_to_robot_commands_pipeline
# Import STT and LLM functions from voice_assistant
from voice_assistant import transcribe_audio, load_whisper_model, load_llm_model, process_command_with_llm_stream # Updated import

import os
import uuid
import qrcode
from io import BytesIO
import base64 
import socket
import time 
import logging # For more controlled logging

# Configure basic logging
logging.basicConfig(level=logging.INFO) 
logging.getLogger('engineio.server').setLevel(logging.WARNING) 
logging.getLogger('socketio.server').setLevel(logging.WARNING) 


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

llm_instance = load_llm_model() 
if llm_instance:
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
def handle_send_robot_command(json_data):
    global is_drawing_active
    if is_drawing_active:
        emit('command_response', {'success': False, 'message': 'Cannot send manual commands while drawing is active.', 'command_sent': json_data.get('type', 'N/A')})
        return

    command_type = json_data.get('type', 'raw')
    command_str = json_data.get('command_str') 
    
    if not robot.is_connected and command_type not in ['go_home']:
        conn_success, conn_message = robot.connect_robot()
        if not conn_success:
            emit('command_response', {'success': False, 'message': f'Robot not connected & connection failed: {conn_message}', 'command_sent': command_type})
            return
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
        
    emit('command_response', {'success': success, 'message': message, 'command_sent': actual_command_sent})
    if not robot.is_connected: 
         emit('robot_connection_status', {'success': False, 'message': 'Disconnected (possibly due to command error/timeout)'})


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
        emit('direct_image_upload_response', {'success': False, 'message': f"Error decoding image data.", 'original_filename': original_filename})
    except Exception:
        emit('direct_image_upload_response', {'success': False, 'message': f"Failed to save file on server.", 'original_filename': original_filename})


@socketio.on('audio_chunk') 
def handle_audio_chunk(data):
    logging.info("--- API: 'audio_chunk' event received ---")
    global is_drawing_active
    if is_drawing_active: 
        logging.warning("API: Drawing is active, ignoring audio chunk.")
        emit('transcription_result', {'error': 'Drawing is active, cannot process voice command now.'})
        # Also inform frontend that LLM processing won't happen
        emit('llm_response_chunk', {'error': 'Drawing is active.', 'done': True})
        return

    audio_data_b64 = data.get('audioData')
    mime_type = data.get('mimeType', 'audio/webm') 
    
    if not audio_data_b64:
        logging.error("API: No audio data (audioData key) in received chunk.")
        emit('transcription_result', {'error': 'No audio data received.'})
        emit('llm_response_chunk', {'error': 'No audio data for LLM.', 'done': True})
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
            emit('transcription_result', {'text': transcribed_text}) # Send full transcription once
            
            logging.info(f"API: Streaming transcribed text to LLM: '{transcribed_text}'")
            # Iterate through the generator from process_command_with_llm_stream
            for llm_response_part in process_command_with_llm_stream(transcribed_text):
                # llm_response_part will be like {"chunk": "text part", "done": False} or {"error": "...", "done": True}
                # logging.debug(f"API: Emitting LLM chunk: {llm_response_part}") # Can be very verbose
                emit('llm_response_chunk', llm_response_part) # New event for streaming
                if llm_response_part.get("done"):
                    logging.info("API: LLM stream finished or error occurred.")
                    break # Exit loop once done or error
        else:
            logging.error("API: Transcription failed (transcribe_audio returned None).")
            emit('transcription_result', {'error': 'Transcription failed on server.'})
            emit('llm_response_chunk', {'error': 'Cannot process with LLM, transcription failed.', 'done': True})

        try:
            os.remove(temp_audio_filepath)
            logging.info(f"API: Temporary audio file removed: {temp_audio_filepath}")
        except Exception as e:
            logging.warning(f"API Warning: Error removing temporary audio file {temp_audio_filepath}: {e}")

    except base64.binascii.Error as b64e:
        logging.error(f"API Error: Base64 decoding failed. {b64e}")
        emit('transcription_result', {'error': 'Invalid audio data format (base64 decode).'})
        emit('llm_response_chunk', {'error': 'Cannot process with LLM, audio data error.', 'done': True})
    except Exception as e:
        logging.error(f"API Error: Error processing audio chunk: {e}", exc_info=True) 
        emit('transcription_result', {'error': f'Server error processing audio.'})
        emit('llm_response_chunk', {'error': f'Cannot process with LLM, server error.', 'done': True})


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
            socketio.sleep(0.05) 

        emit('command_response', {'success': True, 'message': f"Sent all {num_cmds} commands for '{original_filename}'."})
        emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' complete."})

    except Exception as e:
        emit('command_response', {'success': False, 'message': f"Error in drawing pipeline: {e}"})
        emit('drawing_status_update', {'active': False, 'message': f"Error processing/drawing."})
    finally:
        is_drawing_active = False


if __name__ == '__main__':
    server_port = 5555 
    app.config['SERVER_PORT'] = server_port 

    logging.info(f"Starting Python backend server (SocketIO with Flask) on port {server_port}...")
    logging.info(f"Frontend should connect to ws://localhost:{server_port}")
    logging.info(f"QR code upload page will be accessible via http://<YOUR_LOCAL_IP>:{server_port}/qr_upload_page/<session_id>")
    
    socketio.run(app, host='0.0.0.0', port=server_port, debug=True, use_reloader=False)
