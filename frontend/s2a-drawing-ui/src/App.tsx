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

  const [qrCodeImage, setQrCodeImage] = useState<string | null>(null);
  const [qrUploadUrl, setQrUploadUrl] = useState<string>('');
  const [lastUploadedImageInfo, setLastUploadedImageInfo] = useState<string>('');
  const [uploadedFilePathFromBackend, setUploadedFilePathFromBackend] = useState<string | null>(null);

  const [isDrawingActive, setIsDrawingActive] = useState(false);
  const [drawingProgressMessage, setDrawingProgressMessage] = useState('');


  useEffect(() => {
    socket = io(PYTHON_BACKEND_URL, { transports: ['websocket'] });

    socket.on('connect', () => {
      console.log('Frontend: Connected to Python backend via Socket.IO!');
      setIsConnectedToBackend(true);
      setBackendMessage('Connected to Python Backend!');
      // Backend now emits robot_connection_status and drawing_status_update on connect
    });
    socket.on('disconnect', () => {
      console.log('Frontend: Disconnected from Python backend.');
      setIsConnectedToBackend(false);
      setBackendMessage('Disconnected from Python Backend.');
      setIsRobotConnected(false);
      setRobotStatusMessage('Robot: Disconnected (backend offline)');
      setIsDrawingActive(false); 
      setDrawingProgressMessage('');
    });
    socket.on('response', (data: { data: string }) => {
      console.log('Frontend: Received "response":', data);
      setBackendMessage(data.data);
    });
    socket.on('robot_connection_status', (data: { success: boolean, message: string }) => {
      console.log('Frontend: Received "robot_connection_status":', data);
      setIsRobotConnected(data.success);
      setRobotStatusMessage(`Robot: ${data.message}`);
    });
    socket.on('command_response', (data: { success: boolean, message: string, command_sent?: string }) => {
      console.log('Frontend: Received "command_response":', data);
      setLastCommandResponse(`Cmd: ${data.command_sent || 'N/A'} -> Resp: ${data.message} (Success: ${data.success})`);
    });
    socket.on('qr_code_data', (data: { qr_image_base64?: string, upload_url?: string, error?: string }) => {
      console.log('Frontend: Received "qr_code_data":', data.error ? data.error : data.upload_url);
      if (data.error) {
        setQrUploadUrl(`Error generating QR: ${data.error}`);
        setQrCodeImage(null);
        return;
      }
      if (data.qr_image_base64 && data.upload_url) {
        setQrCodeImage(`data:image/png;base64,${data.qr_image_base64}`);
        setQrUploadUrl(data.upload_url);
      }
      setLastUploadedImageInfo('');
      setUploadedFilePathFromBackend(null);
    });
    socket.on('qr_image_received', (data: { success: boolean, message: string, original_filename?: string, filepath_on_server?: string}) => {
      console.log('Frontend: Received "qr_image_received":', data);
      if (data.success && data.filepath_on_server) {
        setLastUploadedImageInfo(`Received: ${data.original_filename || 'image'}. Ready for processing.`);
        setUploadedFilePathFromBackend(data.filepath_on_server);
        setQrCodeImage(null); setQrUploadUrl('');
      } else {
        setLastUploadedImageInfo(`Upload Info: ${data.message}`);
        setUploadedFilePathFromBackend(null);
        if (data.success && !data.filepath_on_server) {
          console.error("Frontend Error: QR image received successfully, but filepath_on_server is missing from backend data:", data);
          setLastUploadedImageInfo(`Upload Successful, but data error (missing filepath). Check console.`);
        }
      }
    });

    socket.on('drawing_status_update', (data: { active: boolean, message: string }) => {
      console.log("Frontend: Received 'drawing_status_update':", data);
      setIsDrawingActive(data.active);
      setDrawingProgressMessage(data.message);
    });

    return () => { if (socket) socket.disconnect(); };
  }, []);

  const handleConnectRobot = () => {
    console.log("Frontend: 'Connect to Robot' button clicked.");
    if (!isDrawingActive && socket) socket.emit('robot_connect_request', {});
  }
  const handleDisconnectRobot = () => {
    console.log("Frontend: 'Disconnect from Robot' button clicked.");
    if (!isDrawingActive && socket) socket.emit('robot_disconnect_request', {});
  }
  const sendGoHomeCommand = () => {
    console.log("Frontend: 'Send Robot to Home' button clicked.");
    if (!isDrawingActive && socket) socket.emit('send_robot_command', { type: 'go_home' });
  }
  const sendSafeCenterCommand = () => {
    console.log("Frontend: 'Send to Safe Center' button clicked.");
    if (!isDrawingActive && socket) socket.emit('send_robot_command', { type: 'move_to_safe_center' });
  }
  
  const requestQrCode = () => {
    console.log("Frontend: 'Upload Image via QR Code' button clicked.");
    if (socket && isConnectedToBackend && !isDrawingActive) {
      setQrCodeImage(null); setQrUploadUrl('Requesting QR Code...');
      setLastUploadedImageInfo(''); setUploadedFilePathFromBackend(null);
      socket.emit('request_qr_code', {});
    } else if (isDrawingActive) {
      alert("Cannot request QR code while drawing is in progress.");
    } else {
      console.warn("Frontend: Request QR Code button clicked but conditions not met (socket, backend connection, or drawing active).")
    }
  };

  const handleProcessAndDrawUploadedImage = () => {
    console.log("Frontend: 'Process & Draw Uploaded Image' button clicked.");
    if (isDrawingActive) {
      alert("A drawing is already in progress.");
      console.log("Frontend: Drawing is active, aborting process/draw request.");
      return;
    }
    if (!isRobotConnected) {
      alert("Please connect to the robot first before processing and drawing.");
      setLastCommandResponse("Error: Robot not connected.");
      console.log("Frontend: Robot not connected, aborting process/draw request.");
      return;
    }
    if (uploadedFilePathFromBackend) {
      console.log("Frontend: Emitting 'process_image_for_drawing' for:", uploadedFilePathFromBackend);
      const originalFilename = lastUploadedImageInfo.includes("Received: ") ? lastUploadedImageInfo.split("Received: ")[1].split(". Ready")[0] : "uploaded_image";
      socket.emit('process_image_for_drawing', { 
          filepath: uploadedFilePathFromBackend,
          original_filename: originalFilename 
      });
      setLastCommandResponse(`Sent request to process & draw: ${originalFilename}`);
      setDrawingProgressMessage("Requesting image processing and drawing..."); 
    } else {
      alert("No image uploaded via QR yet or filepath not available.");
      setLastCommandResponse("Error: No image path available to process.");
      console.error("Frontend Error: handleProcessAndDrawUploadedImage called but uploadedFilePathFromBackend is null or empty.");
    }
  };

  return (
    <div className="App">
      <h1>S2A Robotic Drawing Control</h1>
      <p>Backend Connection: {isConnectedToBackend ? 'Connected' : 'Disconnected'}</p>
      <hr />
      
      <h2>Image Input</h2>
      <button onClick={requestQrCode} disabled={!isConnectedToBackend || isDrawingActive}>
        Upload Image via QR Code
      </button>
      {qrUploadUrl && !qrCodeImage && <p><small>{qrUploadUrl}</small></p>}
      {qrCodeImage && (
        <div>
          <p><small>Scan to upload. URL (for debugging): {qrUploadUrl}</small></p>
          <img src={qrCodeImage} alt="QR Code for Upload" style={{border: "1px solid #ccc", marginTop:"10px"}} />
        </div>
      )}
      {lastUploadedImageInfo && <p style={{color: lastUploadedImageInfo.startsWith("Received:") ? "green" : "red"}}>{lastUploadedImageInfo}</p>}
      
      {uploadedFilePathFromBackend && (
        <button 
          onClick={handleProcessAndDrawUploadedImage} 
          disabled={isDrawingActive || !isRobotConnected || !isConnectedToBackend} 
          style={{marginTop: "10px"}}
        >
          Process & Draw Uploaded Image
        </button>
      )}
       {isDrawingActive && <p style={{color: "blue", fontWeight: "bold"}}>Drawing Active: {drawingProgressMessage}</p>}
       {!isDrawingActive && drawingProgressMessage && <p>{drawingProgressMessage}</p>}


      <hr />
      <h2>Robot Control</h2>
      <button onClick={handleConnectRobot} disabled={!isConnectedToBackend || isRobotConnected || isDrawingActive}>
        Connect to Robot
      </button>
      <button onClick={handleDisconnectRobot} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive}>
        Disconnect (Graceful)
      </button>
      <br />
      <button onClick={sendGoHomeCommand} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive}>
        Send Robot to Home
      </button>
      <button onClick={sendSafeCenterCommand} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive}>
        Send to Safe Center
      </button>
      <p>{robotStatusMessage}</p>
      <p>Last Command: {lastCommandResponse}</p>
    </div>
  );
}

export default App;
