# backend/api_server.py
from flask import Flask, request, render_template_string, jsonify, send_file
from flask_socketio import SocketIO, emit
from robot_interface import RobotInterface
import config
import os
import uuid
import qrcode
from io import BytesIO # To serve QR code image from memory
import base64
import socket # <<< --- ADDED IMPORT --- >>>

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_here!' # Change this!
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), config.QR_UPLOAD_FOLDER)

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

socketio = SocketIO(app, cors_allowed_origins="*")
robot = RobotInterface()

current_upload_session_id = None
# latest_uploaded_image_path = None # We'll pass this directly to frontend or handle via new event

# HTML template for the phone's upload page (same as before)
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
            submitButton.disabled = true;
            fileInput.disabled = true;

            try {
                const response = await fetch(window.location.href, {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (response.ok) {
                    messageDiv.textContent = 'Success: ' + result.message + '. You can close this page.';
                    messageDiv.style.color = 'green';
                } else {
                    messageDiv.textContent = 'Error: ' + (result.error || 'Upload failed. Please try again.');
                    messageDiv.style.color = 'red';
                    submitButton.disabled = false;
                    fileInput.disabled = false;
                }
            } catch (error) {
                messageDiv.textContent = 'Network Error: ' + error.message + '. Please try again.';
                messageDiv.style.color = 'red';
                submitButton.disabled = false;
                fileInput.disabled = false;
            }
        });
    </script>
</body>
</html>
"""

@app.route('/qr_upload_page/<session_id>', methods=['GET', 'POST'])
def handle_qr_upload_page(session_id):
    global current_upload_session_id #, latest_uploaded_image_path (removed global as we emit path)
    if session_id != current_upload_session_id:
        return "Invalid or expired upload session.", 403

    if request.method == 'POST':
        if 'image' not in request.files:
            return jsonify({"error": "No image file part"}), 400
        file = request.files['image']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400
        if file:
            _, f_ext = os.path.splitext(file.filename)
            filename = str(uuid.uuid4()) + f_ext
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            try:
                file.save(filepath)
                print(f"Image received via QR and saved: {filepath}")
                socketio.emit('qr_image_received', {
                    'success': True, 
                    'message': f"Image '{file.filename}' uploaded.", 
                    'filename': file.filename, # Send original filename for display
                    'filepath': filepath # Send the actual server path
                })
                current_upload_session_id = None 
                return jsonify({"message": f"Image '{file.filename}' uploaded successfully!"}), 200
            except Exception as e:
                print(f"Error saving uploaded file: {e}")
                return jsonify({"error": "Failed to save file on server."}), 500
    
    return render_template_string(UPLOAD_PAGE_TEMPLATE)

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('response', {'data': 'Connected to Python backend!'})
    emit('robot_connection_status', {'success': robot.is_connected, 
                                     'message': 'Connected to robot' if robot.is_connected else 'Not connected to robot'})

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected from backend')

@socketio.on('robot_connect_request')
def handle_robot_connect_request(json_data):
    print('Received robot_connect_request:', json_data)
    success, message = robot.connect_robot()
    emit('robot_connection_status', {'success': success, 'message': message})

@socketio.on('robot_disconnect_request')
def handle_robot_disconnect_request(json_data):
    print('Received robot_disconnect_request:', json_data)
    success, message = robot.disconnect_robot(graceful=True) 
    emit('robot_connection_status', {'success': robot.is_connected, 'message': message if success else "Failed to disconnect"})

@socketio.on('send_robot_command')
def handle_send_robot_command(json_data):
    command_str = json_data.get('command_str') 
    command_type = json_data.get('type', 'raw') 
    print(f"Received '{command_type}' command event. Data: {json_data}")

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
    global current_upload_session_id
    current_upload_session_id = str(uuid.uuid4())
    
    host_ip = request.host.split(':')[0] 
    if host_ip == '127.0.0.1' or host_ip == 'localhost':
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1) # Prevent indefinite blocking
            s.connect(("8.8.8.8", 80)) 
            host_ip = s.getsockname()[0]
            s.close()
        except Exception as e:
            print(f"Could not determine non-loopback IP, using 127.0.0.1. Error: {e}")
            host_ip = '127.0.0.1' 

    server_port = app.config.get('SERVER_PORT', 5555)
    upload_url = f"http://{host_ip}:{server_port}/qr_upload_page/{current_upload_session_id}"
    print(f"Generated QR code URL for session {current_upload_session_id}: {upload_url}")

    qr_img = qrcode.make(upload_url)
    img_io = BytesIO()
    qr_img.save(img_io, 'PNG')
    img_io.seek(0)
    
    img_base64 = base64.b64encode(img_io.getvalue()).decode('utf-8')
    
    emit('qr_code_data', {'qr_image_base64': img_base64, 'upload_url': upload_url})

# --- New event handler for processing the image ---
@socketio.on('process_image_for_drawing')
def handle_process_image_for_drawing(data):
    filepath = data.get('filepath')
    if not filepath or not os.path.exists(filepath):
        emit('command_response', {'success': False, 'message': f"File not found or path invalid: {filepath}", 'command_sent': 'process_image'})
        return

    print(f"Received request to process image: {filepath}")
    # Here, you would call your image_processing_engine.py functions
    # and then potentially the robot_interface.py to send drawing commands.
    # This is a placeholder for that complex logic.
    
    # Example:
    # from image_processing_engine import process_image_to_robot_commands
    # robot_commands = process_image_to_robot_commands(filepath, selected_threshold_option) # Need threshold too
    # if robot_commands:
    #     for cmd in robot_commands:
    #         # This needs more robust handling, queueing, progress updates etc.
    #         # For now, just a conceptual print
    #         print(f"Would send drawing command: {cmd}") 
    #         # robot.send_command_raw(robot._format_command(cmd[0], cmd[1], cmd[2])) # Example
    #         # time.sleep(0.1) # Small delay between drawing commands
    #     emit('command_response', {'success': True, 'message': f"Image '{os.path.basename(filepath)}' processing started (conceptual).", 'command_sent': 'process_image'})
    # else:
    #     emit('command_response', {'success': False, 'message': f"Failed to generate commands for '{os.path.basename(filepath)}'.", 'command_sent': 'process_image'})

    emit('command_response', {
        'success': True, # For now, just acknowledge receipt
        'message': f"Placeholder: Received request to process '{os.path.basename(filepath)}'. Actual drawing logic not yet implemented.",
        'command_sent': f'process_image: {os.path.basename(filepath)}'
    })

