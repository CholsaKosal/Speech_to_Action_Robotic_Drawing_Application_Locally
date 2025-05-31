// frontend/s2a-drawing-ui/src/App.tsx
import { useState, useEffect } from 'react';
import { io, Socket } from 'socket.io-client';
import './App.css'; // Your existing or new styles

const PYTHON_BACKEND_URL = 'http://localhost:5555';

let socket: Socket;

function App() {
  const [isConnectedToBackend, setIsConnectedToBackend] = useState(false);
  const [backendMessage, setBackendMessage] = useState('');

  const [isRobotConnected, setIsRobotConnected] = useState(false);
  const [robotStatusMessage, setRobotStatusMessage] = useState('Robot: Not connected');
  const [lastCommandResponse, setLastCommandResponse] = useState('');

  const [qrCodeImage, setQrCodeImage] = useState<string | null>(null);
  const [qrUploadUrl, setQrUploadUrl] = useState<string>('');
  const [lastUploadedImageInfo, setLastUploadedImageInfo] = useState<string>('');
  const [uploadedFilePathFromBackend, setUploadedFilePathFromBackend] = useState<string | null>(null);


  useEffect(() => {
    console.log('Attempting to connect to WebSocket server...');
    socket = io(PYTHON_BACKEND_URL, {
      transports: ['websocket'],
    });

    socket.on('connect', () => {
      console.log('Connected to Python backend via Socket.IO!');
      setIsConnectedToBackend(true);
      setBackendMessage('Connected to Python Backend!');
    });

    socket.on('disconnect', () => {
      console.log('Disconnected from Python backend.');
      setIsConnectedToBackend(false);
      setBackendMessage('Disconnected from Python Backend.');
      setIsRobotConnected(false);
      setRobotStatusMessage('Robot: Disconnected (backend offline)');
    });

    socket.on('response', (data: { data: string }) => {
      setBackendMessage(data.data);
    });

    socket.on('robot_connection_status', (data: { success: boolean, message: string }) => {
      setIsRobotConnected(data.success);
      setRobotStatusMessage(`Robot: ${data.message}`);
    });

    socket.on('command_response', (data: { success: boolean, message: string, command_sent?: string }) => {
      setLastCommandResponse(
        `Cmd: ${data.command_sent || 'N/A'} -> Resp: ${data.message} (Success: ${data.success})`
      );
    });

    socket.on('qr_code_data', (data: { qr_image_base64: string, upload_url: string }) => {
      console.log('Received QR Code data');
      setQrCodeImage(`data:image/png;base64,${data.qr_image_base64}`);
      setQrUploadUrl(data.upload_url);
      setLastUploadedImageInfo(''); 
      setUploadedFilePathFromBackend(null);
    });

    socket.on('qr_image_received', (data: { success: boolean, message: string, filename?: string, filepath?: string}) => {
      console.log('Image received via QR:', data);
      if (data.success && data.filepath) {
        setLastUploadedImageInfo(`Received: ${data.filename || 'image'}. Ready for processing.`);
        setUploadedFilePathFromBackend(data.filepath); // Store the filepath
        setQrCodeImage(null); 
        setQrUploadUrl('');
      } else {
        setLastUploadedImageInfo(`Upload Error: ${data.message}`);
        setUploadedFilePathFromBackend(null);
      }
    });

    return () => {
      if (socket) {
        socket.disconnect();
      }
    };
  }, []);

  const handleConnectRobot = () => socket.emit('robot_connect_request', {});
  const handleDisconnectRobot = () => socket.emit('robot_disconnect_request', {});
  const sendGoHomeCommand = () => socket.emit('send_robot_command', { type: 'go_home' });
  const sendSafeCenterCommand = () => socket.emit('send_robot_command', { type: 'move_to_safe_center' });
  
  const requestQrCode = () => {
    if (socket && isConnectedToBackend) {
      setQrCodeImage(null); 
      setQrUploadUrl('Requesting QR Code...');
      setLastUploadedImageInfo('');
      setUploadedFilePathFromBackend(null);
      socket.emit('request_qr_code', {});
    }
  };

  // Placeholder for actually drawing the uploaded image
  const handleProcessAndDrawUploadedImage = () => {
    if (uploadedFilePathFromBackend) { 
        console.log("Requesting to draw image: ", uploadedFilePathFromBackend);
        // Emit an event to the backend to process this specific file path
        socket.emit('process_image_for_drawing', { filepath: uploadedFilePathFromBackend });
        setLastCommandResponse(`Sent request to process: ${uploadedFilePathFromBackend.split(/[/\\]/).pop()}`);
    } else {
        alert("No image uploaded or ready for processing via QR yet.");
        setLastCommandResponse("Error: No image path available to process.");
    }
  };

  return (
    <div className="App">
      <h1>S2A Robotic Drawing Control</h1>
      <p>Backend Connection: {isConnectedToBackend ? 'Connected' : 'Disconnected'}</p>
      <p>Backend Message: {backendMessage}</p>
      <hr />
      
      <h2>Image Input</h2>
      <button onClick={requestQrCode} disabled={!isConnectedToBackend}>
        Upload Image via QR Code
      </button>
      {qrUploadUrl && <p><small>Scan to upload. URL (for debugging): {qrUploadUrl}</small></p>}
      {qrCodeImage && <img src={qrCodeImage} alt="QR Code for Upload" style={{border: "1px solid #ccc", marginTop:"10px"}} />}
      {lastUploadedImageInfo && <p style={{color: "green"}}>{lastUploadedImageInfo}</p>}
      
      {/* Button to trigger drawing of the uploaded image */}
      {uploadedFilePathFromBackend && (
        <button onClick={handleProcessAndDrawUploadedImage} style={{marginTop: "10px"}}>
          Process & Draw Uploaded Image
        </button>
      )}

      <hr />
      <h2>Robot Control</h2>
      <button onClick={handleConnectRobot} disabled={!isConnectedToBackend || isRobotConnected}>
        Connect to Robot
      </button>
      <button onClick={handleDisconnectRobot} disabled={!isConnectedToBackend || !isRobotConnected}>
        Disconnect (Graceful)
      </button>
      <br />
      <button onClick={sendGoHomeCommand} disabled={!isConnectedToBackend || !isRobotConnected}>
        Send Robot to Home
      </button>
      <button onClick={sendSafeCenterCommand} disabled={!isConnectedToBackend || !isRobotConnected}>
        Send to Safe Center
      </button>
      <p>{robotStatusMessage}</p>
      <p>Last Command: {lastCommandResponse}</p>
    </div>
  );
}

export default App;
