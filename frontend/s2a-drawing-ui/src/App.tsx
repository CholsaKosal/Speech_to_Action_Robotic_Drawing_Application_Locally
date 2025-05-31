// frontend/s2a-drawing-ui/src/App.tsx
import { useState, useEffect } from 'react';
import { io, Socket } from 'socket.io-client';
import './App.css';

const PYTHON_BACKEND_URL = 'http://localhost:5555'; 

let socket: Socket;

function App() {
  const [isConnectedToBackend, setIsConnectedToBackend] = useState(false);
  const [backendMessage, setBackendMessage] = useState('');
  
  const [isRobotConnected, setIsRobotConnected] = useState(false);
  const [robotStatusMessage, setRobotStatusMessage] = useState('Robot: Not connected');
  const [lastCommandResponse, setLastCommandResponse] = useState('');

  useEffect(() => {
    console.log('Attempting to connect to WebSocket server...');
    socket = io(PYTHON_BACKEND_URL, {
      transports: ['websocket'],
    });

    socket.on('connect', () => {
      console.log('Connected to Python backend via Socket.IO!');
      setIsConnectedToBackend(true);
      setBackendMessage('Connected to Python Backend!');
      // Request initial robot status when backend connects
      // (The backend now sends this on its 'connect' event)
    });

    socket.on('disconnect', () => {
      console.log('Disconnected from Python backend.');
      setIsConnectedToBackend(false);
      setBackendMessage('Disconnected from Python Backend.');
      setIsRobotConnected(false); // Assume robot connection is lost if backend disconnects
      setRobotStatusMessage('Robot: Disconnected (backend offline)');
    });

    socket.on('response', (data: { data: string }) => {
      console.log('Message from server (response):', data);
      setBackendMessage(data.data);
    });
    
    socket.on('robot_connection_status', (data: { success: boolean, message: string }) => {
      console.log('Robot connection status update:', data);
      setIsRobotConnected(data.success);
      setRobotStatusMessage(`Robot: ${data.message}`);
    });

    socket.on('command_response', (data: { success: boolean, message: string, command_sent?: string }) => {
      console.log('Command response:', data);
      setLastCommandResponse(
        `Cmd: ${data.command_sent || 'N/A'} -> Response: ${data.message} (Success: ${data.success})`
      );
      // If a command fails and causes disconnect, robot_connection_status should update
    });

    return () => {
      if (socket) {
        console.log('Disconnecting socket...');
        socket.disconnect();
      }
    };
  }, []);

  const handleConnectRobot = () => {
    if (socket && isConnectedToBackend) {
      setRobotStatusMessage('Robot: Sending connect request...');
      socket.emit('robot_connect_request', { data: 'please connect to robot' });
    }
  };
  
  const handleDisconnectRobot = () => {
    if (socket && isConnectedToBackend) {
      setRobotStatusMessage('Robot: Sending disconnect request (graceful)...');
      socket.emit('robot_disconnect_request', { data: 'please disconnect robot gracefully' });
    }
  };

  const sendGoHomeCommand = () => {
    if (socket && isConnectedToBackend) {
      setLastCommandResponse('Sending command: Go Home');
      socket.emit('send_robot_command', { type: 'go_home' });
    }
  };
  
  const sendSafeCenterCommand = () => {
    if (socket && isConnectedToBackend) {
      setLastCommandResponse('Sending command: Move to Safe Center');
      socket.emit('send_robot_command', { type: 'move_to_safe_center' });
    }
  };

  return (
    <>
      <h1>S2A Robotic Drawing Control</h1>
      <p>Backend Connection: {isConnectedToBackend ? 'Connected' : 'Disconnected'}</p>
      <p>Backend Message: {backendMessage}</p>
      <hr />
      <h2>Robot Control</h2>
      <button onClick={handleConnectRobot} disabled={!isConnectedToBackend || isRobotConnected}>
        Connect to Robot
      </button>
      <button onClick={handleDisconnectRobot} disabled={!isConnectedToBackend || !isRobotConnected}>
        Disconnect from Robot (Graceful)
      </button>
      <br />
      <button onClick={sendGoHomeCommand} disabled={!isConnectedToBackend || !isRobotConnected}>
        Send Robot to Home
      </button>
      <button onClick={sendSafeCenterCommand} disabled={!isConnectedToBackend || !isRobotConnected}>
        Send to Safe Center
      </button>
      <p>{robotStatusMessage}</p>
      <p>Last Command Response: {lastCommandResponse}</p>
    </>
  );
}

export default App;