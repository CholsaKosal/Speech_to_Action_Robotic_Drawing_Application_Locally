# backend/api_server.py
from flask import Flask, request, render_template_string, jsonify, send_file
from flask_socketio import SocketIO, emit
from robot_interface import RobotInterface
import config
import os
import uuid
import qrcode
from io import BytesIO
import base64 # Make sure base64 is imported
import socket
import time # For potential delays between commands
from image_processing_engine import process_image_to_robot_commands_pipeline

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_here!'
# Ensure UPLOAD_FOLDER is correctly joined from the config and this file's directory
# The config.QR_UPLOAD_FOLDER is relative to the backend directory,
# and api_server.py is in the backend directory.
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), config.QR_UPLOAD_FOLDER)

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])
    print(f"Created upload folder at: {app.config['UPLOAD_FOLDER']}")
else:
    print(f"Upload folder already exists at: {app.config['UPLOAD_FOLDER']}")


socketio = SocketIO(app, cors_allowed_origins="*")
robot = RobotInterface() # Single instance of RobotInterface

current_upload_session_id = None
is_drawing_active = False # Global flag to prevent concurrent drawing tasks

# HTML template for the phone's upload page (remains the same)
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
            # Sanitize filename for safety, or use UUID without extension if preferred
            # For now, just ensure it's a valid extension and generate a unique name
            if f_ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
                 return jsonify({"error": "Invalid file type."}), 400

            filename_on_server = str(uuid.uuid4()) + f_ext
            filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
            try:
                file.save(filepath_on_server)
                print(f"Image received via QR and saved: {filepath_on_server}")
                socketio.emit('qr_image_received', { # This event name is handled by frontend
                    'success': True,
                    'message': f"Image '{original_filename}' uploaded via QR.",
                    'original_filename': original_filename,
                    'filepath_on_server': filepath_on_server
                })
                current_upload_session_id = None # Invalidate session after successful upload
                return jsonify({"message": f"Image '{original_filename}' uploaded successfully!"}), 200
            except Exception as e:
                print(f"Error saving uploaded file: {e}")
                socketio.emit('qr_image_received', {
                    'success': False,
                    'message': f"Error saving '{original_filename}' on server.",
                    'original_filename': original_filename
                })
                return jsonify({"error": "Failed to save file on server."}), 500
    return render_template_string(UPLOAD_PAGE_TEMPLATE)

# --- SocketIO Event Handlers ---
@socketio.on('connect')
def handle_connect():
    print('Client connected to backend SocketIO')
    emit('response', {'data': 'Connected to Python backend!'})
    emit('robot_connection_status', {'success': robot.is_connected,
                                     'message': 'Connected to robot' if robot.is_connected else 'Not connected to robot'})
    emit('drawing_status_update', {'active': is_drawing_active, 'message': 'Drawing in progress' if is_drawing_active else 'Idle'})


@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected from backend SocketIO')

@socketio.on('robot_connect_request')
def handle_robot_connect_request(json_data):
    global is_drawing_active
    if is_drawing_active:
        emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'})
        return
    print('Received robot_connect_request:', json_data)
    success, message = robot.connect_robot()
    emit('robot_connection_status', {'success': success, 'message': message})

@socketio.on('robot_disconnect_request')
def handle_robot_disconnect_request(json_data):
    global is_drawing_active
    if is_drawing_active:
        emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Cannot connect/disconnect robot while drawing is active.'})
        return
    print('Received robot_disconnect_request:', json_data)
    success, message = robot.disconnect_robot(graceful=True)
    emit('robot_connection_status', {'success': robot.is_connected, 'message': message if success else "Failed to disconnect"})

@socketio.on('send_robot_command') # Generic commands like go_home, move_to_safe_center
def handle_send_robot_command(json_data):
    global is_drawing_active
    if is_drawing_active:
        emit('command_response', {'success': False, 'message': 'Cannot send manual commands while drawing is active.', 'command_sent': json_data.get('type', 'N/A')})
        return

    command_type = json_data.get('type', 'raw')
    command_str = json_data.get('command_str') # For raw commands
    print(f"Received '{command_type}' command event. Data: {json_data}")

    if not robot.is_connected and command_type not in ['go_home']: # Allow go_home to attempt connection
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
    if not robot.is_connected: # If command caused disconnect
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
            print(f"Could not determine non-loopback IP, using 127.0.0.1. Error: {e}"); host_ip = '127.0.0.1'
    
    # Get server port from app config if available, otherwise default
    server_port = app.config.get('SERVER_PORT', 5555) 
    
    upload_url = f"http://{host_ip}:{server_port}/qr_upload_page/{current_upload_session_id}"
    print(f"Generated QR code URL for session {current_upload_session_id}: {upload_url}")
    qr_img = qrcode.make(upload_url); img_io = BytesIO(); qr_img.save(img_io, 'PNG'); img_io.seek(0)
    img_base64 = base64.b64encode(img_io.getvalue()).decode('utf-8')
    emit('qr_code_data', {'qr_image_base64': img_base64, 'upload_url': upload_url})

@socketio.on('direct_image_upload')
def handle_direct_image_upload(data):
    """Handles images uploaded directly from the desktop app (e.g., drag & drop, file select)."""
    global is_drawing_active
    if is_drawing_active:
        emit('direct_image_upload_response', {
            'success': False,
            'message': 'Cannot upload image while drawing is active.'
        })
        return

    original_filename = data.get('filename')
    base64_data = data.get('fileData')

    if not original_filename or not base64_data:
        emit('direct_image_upload_response', {
            'success': False,
            'message': 'Missing filename or file data for direct upload.'
        })
        return

    print(f"Received direct image upload request for: {original_filename}")

    try:
        # Decode base64 data
        image_data = base64.b64decode(base64_data)
        
        _, f_ext = os.path.splitext(original_filename)
        if f_ext.lower() not in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
            emit('direct_image_upload_response', {
                'success': False,
                'message': f"Invalid file type: {f_ext}",
                'original_filename': original_filename
            })
            return

        filename_on_server = str(uuid.uuid4()) + f_ext
        filepath_on_server = os.path.join(app.config['UPLOAD_FOLDER'], filename_on_server)
        
        with open(filepath_on_server, 'wb') as f:
            f.write(image_data)
        
        print(f"Image '{original_filename}' (saved as {filename_on_server}) received via direct upload and saved to: {filepath_on_server}")
        emit('direct_image_upload_response', {
            'success': True,
            'message': f"Image '{original_filename}' uploaded directly.",
            'original_filename': original_filename,
            'filepath_on_server': filepath_on_server
        })

    except base64.binascii.Error as b64_error:
        print(f"Base64 decoding error for {original_filename}: {b64_error}")
        emit('direct_image_upload_response', {
            'success': False,
            'message': f"Error decoding image data for '{original_filename}'.",
            'original_filename': original_filename
        })
    except Exception as e:
        print(f"Error saving directly uploaded file '{original_filename}': {e}")
        emit('direct_image_upload_response', {
            'success': False,
            'message': f"Failed to save '{original_filename}' on server.",
            'original_filename': original_filename
        })


@socketio.on('process_image_for_drawing')
def handle_process_image_for_drawing(data):
    global is_drawing_active
    if is_drawing_active:
        emit('command_response', {'success': False, 'message': "Another drawing is already in progress.", 'command_sent': 'process_image'})
        return

    filepath_on_server = data.get('filepath')
    original_filename = data.get('original_filename', os.path.basename(filepath_on_server or "unknown_image"))

    if not filepath_on_server or not os.path.exists(filepath_on_server):
        emit('command_response', {'success': False, 'message': f"File not found or path invalid: {filepath_on_server}", 'command_sent': f'process_image: {original_filename}'})
        return

    print(f"Received request to process image: {filepath_on_server} (Original: {original_filename})")
    emit('drawing_status_update', {'active': True, 'message': f"Processing '{original_filename}'..."})
    is_drawing_active = True
    
    canny_t1 = data.get('canny_t1', config.DEFAULT_CANNY_THRESHOLD1) 
    canny_t2 = data.get('canny_t2', config.DEFAULT_CANNY_THRESHOLD2)
    
    try:
        robot_commands_tuples = process_image_to_robot_commands_pipeline(
            filepath_on_server,
            canny_thresh1=canny_t1,
            canny_thresh2=canny_t2
        )

        if not robot_commands_tuples:
            print(f"No drawing commands generated for {original_filename}.")
            emit('command_response', {'success': False, 'message': f"No drawing commands generated for '{original_filename}'.", 'command_sent': f'process_image: {original_filename}'})
            is_drawing_active = False # Reset flag
            emit('drawing_status_update', {'active': False, 'message': f"Failed to process '{original_filename}'."})
            return

        num_cmds = len(robot_commands_tuples)
        print(f"Successfully generated {num_cmds} drawing commands for {original_filename}.")
        emit('drawing_status_update', {'active': True, 'message': f"Generated {num_cmds} commands. Preparing to draw '{original_filename}'..."})
        
        if not robot.is_connected:
            conn_success, conn_msg = robot.connect_robot()
            if not conn_success:
                emit('command_response', {'success': False, 'message': f"Cannot start drawing. Robot connection failed: {conn_msg}", 'command_sent': f'draw_image: {original_filename}'})
                is_drawing_active = False
                emit('drawing_status_update', {'active': False, 'message': f"Drawing '{original_filename}' failed (robot connection)."})
                return
            emit('robot_connection_status', {'success': True, 'message': conn_msg})

        for i, cmd_tuple in enumerate(robot_commands_tuples):
            x_py, z_py, y_py = cmd_tuple
            formatted_cmd_str = robot._format_command(x_py, z_py, y_py)
            
            progress_message = f"Drawing '{original_filename}': Sending command {i+1} of {num_cmds} ({formatted_cmd_str})"
            print(progress_message)
            emit('drawing_status_update', {'active': True, 'message': progress_message, 'progress': (i+1)/num_cmds * 100}) # Optional progress percentage
            
            success, msg = robot.send_command_raw(formatted_cmd_str)
            
            if not success:
                error_message = f"Error sending command {i+1} ({formatted_cmd_str}): {msg}. Drawing aborted."
                print(error_message)
                emit('command_response', {'success': False, 'message': error_message, 'command_sent': formatted_cmd_str})
                emit('drawing_status_update', {'active': False, 'message': f"Drawing '{original_filename}' aborted due to error."})
                robot.go_home() 
                is_drawing_active = False
                return 
            
            socketio.sleep(0.05) 

        print(f"Finished sending all drawing commands for {original_filename}.")
        emit('command_response', {'success': True, 'message': f"Successfully sent all {num_cmds} drawing commands for '{original_filename}'.", 'command_sent': f'draw_image: {original_filename}'})
        emit('drawing_status_update', {'active': False, 'message': f"Drawing of '{original_filename}' complete."})

    except Exception as e:
        error_msg_exc = f"Error during image processing/drawing pipeline for {original_filename}: {e}"
        print(error_msg_exc)
        emit('command_response', {'success': False, 'message': error_msg_exc, 'command_sent': f'process_image: {original_filename}'})
        emit('drawing_status_update', {'active': False, 'message': f"Error processing/drawing '{original_filename}'."})
    finally:
        is_drawing_active = False
        # Optionally, attempt to move robot to a safe position after drawing or error
        # if robot.is_connected:
        #     safe_x, safe_z, safe_y = config.SAFE_ABOVE_CENTER_PY
        #     robot.move_to_position_py(safe_x, safe_z, safe_y)
        #     print("Moved robot to safe center after drawing operation.")


if __name__ == '__main__':
    server_port = 5555 
    app.config['SERVER_PORT'] = server_port 

    print(f"Starting Python backend server (SocketIO with Flask) on port {server_port}...")
    print(f"Frontend should connect to ws://localhost:{server_port} (or your machine's IP on the network)")
    print(f"QR code upload page will be accessible via http://<YOUR_LOCAL_IP>:{server_port}/qr_upload_page/<session_id>")
    
    # Consider using 'gevent' or 'gevent_websocket' for production with SocketIO if 'eventlet' causes issues.
    # For development, 'eventlet' or the default Flask dev server (with polling) is often fine.
    # If using Flask's default dev server (socketio.run(app, host='0.0.0.0', port=server_port, debug=True)), 
    # ensure WebSocket transport is supported or use long-polling.
    # Eventlet is generally good for SocketIO.
    socketio.run(app, host='0.0.0.0', port=server_port, debug=True, use_reloader=False, async_mode='eventlet')
    # use_reloader=False is often good with eventlet/gevent to avoid issues.
