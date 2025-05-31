# backend/robot_interface.py
import socket
import time # Make sure time is imported (it likely already is)
import config

class RobotInterface:
    def __init__(self):
        self.robot_socket = None
        self.is_connected = False
        self.target_host = config.REAL_ROBOT_HOST if config.USE_REAL_ROBOT else config.SIMULATION_HOST
        self.target_port = config.REAL_ROBOT_PORT if config.USE_REAL_ROBOT else config.SIMULATION_PORT

    def _format_command(self, x, z, y):
        return f"{x:.2f},{z:.2f},{y:.2f}"

    def connect_robot(self):
        if self.is_connected:
            print("Robot already connected.")
            return True, "Already connected"
        try:
            self.robot_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.robot_socket.settimeout(5)
            print(f"Attempting to connect to robot at {self.target_host}:{self.target_port}...")
            self.robot_socket.connect((self.target_host, self.target_port))
            self.robot_socket.settimeout(None)
            self.is_connected = True
            print("Successfully connected to the robot/simulator.")
            return True, "Successfully connected"
        except socket.error as e:
            self.robot_socket = None
            self.is_connected = False
            print(f"Error connecting to robot: {e}")
            return False, f"Error connecting: {e}"

    def disconnect_robot(self, graceful=True):
        if not self.is_connected:
            print("Robot is not connected.")
            return True, "Was not connected."

        if graceful:
            print("Attempting graceful disconnect (going home first)...")
            home_success, home_msg = self.go_home() # go_home now attempts to connect if not connected.
                                                 # We should only call it if already connected for disconnect.
            if self.is_connected: # Check again, as go_home might have connected if it wasn't
                if not home_success:
                    print(f"Warning: Failed to go home before disconnecting: {home_msg}")
                else:
                    print("Successfully moved to home position.")
                    print("Waiting for 3 seconds before closing socket...")
                    time.sleep(3)

            else: # This case means go_home was called when not connected AND it failed to connect.
                print(f"Cannot complete graceful disconnect (go home) as robot is not connected: {home_msg}")


        if self.robot_socket:
            try:
                self.robot_socket.close()
            except socket.error as e:
                print(f"Error closing socket: {e}")
            finally:
                self.robot_socket = None
                self.is_connected = False
                print("Socket closed. Disconnected from robot.")
        else: # If robot_socket is None but is_connected was somehow true (should not happen with current logic)
            self.is_connected = False 
            print("No active socket to close. Marked as disconnected.")
            
        return True, "Disconnected from robot."

    def send_command_raw(self, command_str):
        if not self.is_connected or not self.robot_socket:
            return False, "Not connected"
        try:
            print(f"Sending command: {command_str}")
            self.robot_socket.sendall(command_str.encode('utf-8'))
            
            response_r = self.robot_socket.recv(1024).decode('utf-8').strip()
            print(f"Received R-phase: '{response_r}'")
            if response_r.upper() != "R":
                return False, f"Robot did not acknowledge (R). Got: {response_r}"

            response_d_or_e = self.robot_socket.recv(1024).decode('utf-8').strip()
            print(f"Received D/E-phase: '{response_d_or_e}'")
            if response_d_or_e.upper() == "D":
                return True, f"Command '{command_str}' successful."
            elif response_d_or_e.upper() == "E":
                return False, f"Command '{command_str}' failed: Robot reported error (E)."
            else:
                return False, f"Robot did not signal done (D) or error (E). Got: {response_d_or_e}"
                
        except socket.timeout:
            print(f"Socket timeout during send/recv for command: {command_str}")
            self.is_connected = False 
            self.robot_socket = None
            return False, "Socket timeout"
        except socket.error as e:
            print(f"Socket error during send/recv: {e}")
            self.is_connected = False
            self.robot_socket = None
            return False, f"Socket error: {e}"
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            self.is_connected = False
            self.robot_socket = None
            return False, f"Unexpected error: {e}"

    def go_home(self):
        if not self.is_connected:
            conn_success, conn_msg = self.connect_robot()
            if not conn_success:
                return False, f"Cannot go home. Connection failed: {conn_msg}"
        
        print("Sending robot to home position...")
        x, z, y = config.ROBOT_HOME_POSITION_PY
        cmd_str = self._format_command(x, z, y)
        return self.send_command_raw(cmd_str)

    def move_to_position_py(self, x_py, z_py, y_py):
        if not self.is_connected:
            conn_success, conn_msg = self.connect_robot()
            if not conn_success:
                return False, f"Cannot move. Connection failed: {conn_msg}"

        cmd_str = self._format_command(x_py, z_py, y_py)
        return self.send_command_raw(cmd_str)

# Main guard for direct testing (if __name__ == '__main__') remains the same
# ... (previous if __name__ == '__main__' block)