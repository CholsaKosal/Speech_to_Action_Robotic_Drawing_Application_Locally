# backend/robot_worker.py
import socket
import time
import logging
import threading
import queue
import config

class RobotWorker:
    """
    Handles all direct socket communication with the robot arm.
    This runs in a separate thread (Fn2) to prevent blocking the main API server.
    """

    def __init__(self, command_queue, result_queue):
        self.command_queue = command_queue
        self.result_queue = result_queue
        self.robot_socket = None
        self.is_connected = False
        self.current_target_host = None
        self.current_target_port = None
        self.is_drawing = False
        self._abort_drawing_flag = threading.Event()

    def _send_result(self, result_type, data):
        """Puts a result onto the queue for the main thread to process."""
        self.result_queue.put({'type': result_type, 'data': data})
        # Reduce log verbosity for progress updates
        if result_type != 'drawing_progress':
            logging.info(f"Fn2 (Worker) sent result to Fn1: Type='{result_type}'")

    def _format_command(self, x, z, y):
        """Formats the coordinate tuple into the string expected by the robot."""
        return f"{x:.3f},{z:.3f},{y:.3f}"

    def _connect_robot(self, use_real=False):
        """Internal method to establish a socket connection."""
        if self.is_connected:
            self._send_result('connection_status', {'success': True, 'message': f"Already connected to {self.current_target_host}"})
            return

        host = config.REAL_ROBOT_HOST if use_real else config.SIMULATION_HOST
        port = config.REAL_ROBOT_PORT if use_real else config.SIMULATION_PORT
        logging.info(f"Worker: Attempting to connect to {host}:{port}...")

        try:
            self.robot_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.robot_socket.settimeout(10)
            self.robot_socket.connect((host, port))
            self.robot_socket.settimeout(None)
            self.is_connected = True
            self.current_target_host = host
            self.current_target_port = port
            logging.info(f"Worker: Successfully connected to {host}:{port}.")
            self._send_result('connection_status', {'success': True, 'message': f"Connected to {('Real Robot' if use_real else 'Simulation')}"})
        except Exception as e:
            self.robot_socket = None
            self.is_connected = False
            error_message = f"Connection to {host}:{port} failed: {e}"
            logging.error(f"Worker: {error_message}")
            self._send_result('connection_status', {'success': False, 'message': error_message})

    def _disconnect_robot(self):
        """Internal method to gracefully disconnect."""
        if not self.is_connected:
            self._send_result('connection_status', {'success': False, 'message': "Was not connected."})
            return
        
        logging.info("Worker: Attempting graceful disconnect (going home first)...")
        home_success, _ = self._execute_single_move(config.ROBOT_HOME_POSITION_PY)
        if not home_success:
            logging.warning("Worker: Failed to go home before disconnecting. Closing socket anyway.")
        
        if self.robot_socket:
            try:
                self.robot_socket.close()
            except socket.error as e:
                logging.error(f"Worker: Error closing socket: {e}")
            finally:
                self.robot_socket = None
                self.is_connected = False
                self.current_target_host = None
                self.current_target_port = None
        
        self._send_result('connection_status', {'success': False, 'message': 'Disconnected'})

    def _send_command_and_get_response(self, command_str):
        """Sends a single command and waits for the 'R' and 'D' responses."""
        if not self.is_connected or not self.robot_socket:
            return False, "Not connected"
        try:
            logging.debug(f"Worker Sending: {command_str}")
            self.robot_socket.sendall(command_str.encode('utf-8'))

            self.robot_socket.settimeout(20)
            response_r = self.robot_socket.recv(1024).decode('utf-8').strip()
            if response_r.upper() != "R":
                msg = f"Protocol Error: Expected 'R', got '{response_r}'"
                logging.error(msg)
                return False, msg

            self.robot_socket.settimeout(60)
            response_d_or_e = self.robot_socket.recv(1024).decode('utf-8').strip()
            self.robot_socket.settimeout(None)
            
            if response_d_or_e.upper() == "D":
                return True, f"Command successful."
            else:
                msg = f"Robot Error: Expected 'D', got '{response_d_or_e}'"
                logging.error(msg)
                return False, msg

        except socket.timeout:
            msg = f"Timeout waiting for robot response on command: {command_str}"
            logging.warning(f"Worker: {msg}")
            return False, msg
            
        except (socket.error, ConnectionResetError) as e:
            error_message = f"Socket error for '{command_str}': {e}"
            logging.error(f"Worker: {error_message}. Assuming disconnection.")
            self.is_connected = False
            if self.robot_socket:
                try: self.robot_socket.close()
                except: pass
            self.robot_socket = None
            self._send_result('connection_status', {'success': False, 'message': f'Disconnected: {e}'})
            return False, error_message

    def _execute_single_move(self, position_tuple):
        x, z_depth, y_side = position_tuple
        cmd_str = self._format_command(x, z_depth, y_side)
        return self._send_command_and_get_response(cmd_str)

    def _execute_drawing(self, commands, drawing_id):
        self.is_drawing = True
        self._abort_drawing_flag.clear()
        
        if not self.is_connected:
            self._send_result('error', {'message': "Cannot start drawing, robot not connected.", 'drawing_id': drawing_id})
            self.is_drawing = False
            return
            
        logging.info(f"Worker: Starting drawing '{drawing_id}' with {len(commands)} commands.")
        
        success, msg = self._execute_single_move(config.SAFE_ABOVE_CENTER_PY)
        if not success:
            self._send_result('error', {'message': f"Failed safe start: {msg}", 'drawing_id': drawing_id})
            self.is_drawing = False
            return

        for i, command_tuple in enumerate(commands):
            if self._abort_drawing_flag.is_set():
                logging.info(f"Worker: Drawing ID '{drawing_id}' aborted.")
                break

            success, msg = self._execute_single_move(command_tuple)
            if not success:
                self._send_result('error', {
                    'message': f"Error at command {i+1}/{len(commands)}: {msg}",
                    'drawing_id': drawing_id
                })
                self.is_drawing = False
                return

            # *** ADDED: Send progress update after each successful command ***
            progress_data = {
                'drawing_id': drawing_id,
                'current_command_index': i + 1,
                'total_commands': len(commands)
            }
            self._send_result('drawing_progress', progress_data)


        if not self._abort_drawing_flag.is_set():
            logging.info(f"Worker: Drawing '{drawing_id}' completed.")
            self._execute_single_move(config.ROBOT_HOME_POSITION_PY)
            self._send_result('drawing_finished', {
                'success': True, 'message': 'Drawing complete. Robot at home.', 'drawing_id': drawing_id
            })

        self.is_drawing = False
        self._abort_drawing_flag.clear()

    def run(self):
        """The main loop of the worker thread."""
        logging.info("Fn2 (RobotWorker) is running and waiting for commands.")
        while True:
            try:
                command_data = self.command_queue.get()
                action = command_data.get('action')
                data = command_data.get('data', {})
                logging.info(f"Fn2 received command from Fn1: Action='{action}'")

                if action == 'connect':
                    self._connect_robot(use_real=data.get('use_real_robot', False))
                elif action == 'disconnect':
                    self._disconnect_robot()
                elif action == 'get_status':
                    msg = f"Connected to {self.current_target_host}" if self.is_connected else "Not connected"
                    self._send_result('connection_status', {'success': self.is_connected, 'message': msg})
                elif action == 'move':
                    command_type = data.get('type')
                    pos_tuple, cmd_display = None, command_type
                    if command_type == 'go_home':
                        pos_tuple = config.ROBOT_HOME_POSITION_PY
                        cmd_display = "Go Home"
                    elif command_type == 'move_to_safe_center':
                        pos_tuple = config.SAFE_ABOVE_CENTER_PY
                        cmd_display = "Move to Safe Center"
                    if pos_tuple:
                        success, msg = self._execute_single_move(pos_tuple)
                        self._send_result('move_completed', {'success': success, 'message': msg, 'command_sent': cmd_display})
                elif action == 'move_custom':
                    try:
                        x, z, y = float(data['x_py']), float(data['z_py']), float(data['y_py'])
                        success, msg = self._execute_single_move((x, z, y))
                        self._send_result('move_completed', {'success': success, 'message': msg, 'command_sent': f'Custom: ({x},{z},{y})'})
                    except (TypeError, ValueError) as e:
                         self._send_result('error', {'message': f"Invalid coordinate data: {e}"})
                elif action == 'draw':
                    self._execute_drawing(data.get('commands'), data.get('drawing_id'))
                elif action == 'abort_drawing':
                    if self.is_drawing:
                        logging.info("Worker: Setting abort flag for current drawing.")
                        self._abort_drawing_flag.set()
                else:
                    logging.warning(f"Worker received unknown action: {action}")
            except Exception as e:
                logging.error(f"Critical error in RobotWorker run loop: {e}", exc_info=True)
                self._send_result('error', {'message': f"Critical worker error: {e}"})
                self.is_connected = False
                self.is_drawing = False
