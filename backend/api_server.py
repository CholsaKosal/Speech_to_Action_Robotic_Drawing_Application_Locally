# backend/api_server.py
from flask import Flask
from flask_socketio import SocketIO, emit
from robot_interface import RobotInterface # Import your RobotInterface class
import config # Import config

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_here!' # Change this!
socketio = SocketIO(app, cors_allowed_origins="*") # Allow all origins for dev

robot = RobotInterface() # Create an instance of your robot interface

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('response', {'data': 'Connected to Python backend!'})
    # Send current robot connection status if already known
    emit('robot_connection_status', {'success': robot.is_connected, 
                                     'message': 'Connected to robot' if robot.is_connected else 'Not connected to robot'})


@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected from backend')
    # Decide on a strategy: e.g., disconnect robot if no clients are connected for a while
    # For now, we won't auto-disconnect robot when frontend disconnects from backend.

@socketio.on('robot_connect_request')
def handle_robot_connect_request(json_data):
    print('Received robot_connect_request:', json_data)
    success, message = robot.connect_robot()
    emit('robot_connection_status', {'success': success, 'message': message})

@socketio.on('robot_disconnect_request')
def handle_robot_disconnect_request(json_data):
    print('Received robot_disconnect_request:', json_data)
    # Using graceful disconnect by default
    success, message = robot.disconnect_robot(graceful=True) 
    emit('robot_connection_status', {'success': success, 'message': message}) # Message will be "Disconnected from robot."
    # After a successful disconnect, the robot.is_connected will be False.
    # We can re-emit the status to reflect this.
    emit('robot_connection_status', {'success': robot.is_connected, 'message': 'Disconnected from robot.'})


@socketio.on('send_robot_command') # This can be a generic command sender
def handle_send_robot_command(json_data):
    command_str = json_data.get('command_str') # Expecting a raw command string "X,Z,Y"
    command_type = json_data.get('type', 'raw') # e.g., 'raw', 'go_home', 'move_to_safe_center'

    print(f"Received '{command_type}' command event. Data: {json_data}")

    if not robot.is_connected and command_type != 'go_home': # go_home will try to connect
        # Try to connect first if not connected for most commands
        conn_success, conn_message = robot.connect_robot()
        if not conn_success:
            emit('command_response', {'success': False, 'message': f'Robot not connected & connection failed: {conn_message}', 'command_sent': command_type})
            return
        emit('robot_connection_status', {'success': True, 'message': conn_message})

    success, message = False, "Invalid command type"
    actual_command_sent = command_type

    if command_type == 'go_home':
        success, message = robot.go_home()
        # For go_home, the command string is generated internally
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
        success, message = False, "No command_str provided for raw command."
        actual_command_sent = "N/A"
        
    emit('command_response', {'success': success, 'message': message, 'command_sent': actual_command_sent})
    # Update robot connection status in case a command caused a disconnect
    if not robot.is_connected:
         emit('robot_connection_status', {'success': False, 'message': 'Disconnected (possibly due to command error/timeout)'})