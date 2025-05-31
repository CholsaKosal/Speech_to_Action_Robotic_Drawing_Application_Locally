// frontend/s2a-drawing-ui/src/App.tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
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

  // QR Code Upload States
  const [qrCodeImage, setQrCodeImage] = useState<string | null>(null);
  const [qrUploadUrl, setQrUploadUrl] = useState<string>('');
  
  // Direct File Upload States
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Common states for uploaded image info (from QR or Direct)
  const [lastUploadedImageInfo, setLastUploadedImageInfo] = useState<string>('');
  const [uploadedFilePathFromBackend, setUploadedFilePathFromBackend] = useState<string | null>(null);

  const [isDrawingActive, setIsDrawingActive] = useState(false);
  const [drawingProgressMessage, setDrawingProgressMessage] = useState('');


  // Effect for Socket.IO setup and event listeners
  useEffect(() => {
    socket = io(PYTHON_BACKEND_URL, { transports: ['websocket'] });

    socket.on('connect', () => {
      console.log('Frontend: Connected to Python backend via Socket.IO!');
      setIsConnectedToBackend(true);
      setBackendMessage('Connected to Python Backend!');
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
      // Clear other upload methods' states
      setSelectedFile(null);
      setImagePreviewUrl(null);
      setLastUploadedImageInfo('');
      setUploadedFilePathFromBackend(null);
    });

    // Listener for when an image is received (either from QR or direct desktop upload)
    // The backend should emit a consistent event, e.g., 'image_upload_success'
    const handleImageUploadSuccess = (data: { success: boolean, message: string, original_filename?: string, filepath_on_server?: string}) => {
      console.log('Frontend: Received image upload confirmation:', data);
      if (data.success && data.filepath_on_server) {
        setLastUploadedImageInfo(`Received: ${data.original_filename || 'image'}. Ready for processing.`);
        setUploadedFilePathFromBackend(data.filepath_on_server);
        // Clear QR and local file states as we now have a backend path
        setQrCodeImage(null); setQrUploadUrl('');
        setSelectedFile(null); setImagePreviewUrl(null);
      } else {
        setLastUploadedImageInfo(`Upload Info: ${data.message}`);
        setUploadedFilePathFromBackend(null);
        if (data.success && !data.filepath_on_server) {
          console.error("Frontend Error: Image upload successful, but filepath_on_server is missing:", data);
          setLastUploadedImageInfo(`Upload Successful, but data error (missing filepath). Check console.`);
        }
      }
    };

    socket.on('qr_image_received', handleImageUploadSuccess);
    socket.on('direct_image_upload_response', handleImageUploadSuccess); // New event for direct uploads

    socket.on('drawing_status_update', (data: { active: boolean, message: string }) => {
      console.log("Frontend: Received 'drawing_status_update':", data);
      setIsDrawingActive(data.active);
      setDrawingProgressMessage(data.message);
    });

    return () => { if (socket) socket.disconnect(); };
  }, []);

  // Robot Control Handlers
  const handleConnectRobot = () => {
    if (!isDrawingActive && socket) socket.emit('robot_connect_request', {});
  }
  const handleDisconnectRobot = () => {
    if (!isDrawingActive && socket) socket.emit('robot_disconnect_request', {});
  }
  const sendGoHomeCommand = () => {
    if (!isDrawingActive && socket) socket.emit('send_robot_command', { type: 'go_home' });
  }
  const sendSafeCenterCommand = () => {
    if (!isDrawingActive && socket) socket.emit('send_robot_command', { type: 'move_to_safe_center' });
  }
  
  // QR Code Request Handler
  const requestQrCode = () => {
    if (socket && isConnectedToBackend && !isDrawingActive) {
      setQrCodeImage(null); setQrUploadUrl('Requesting QR Code...');
      setSelectedFile(null); setImagePreviewUrl(null); // Clear direct upload state
      setLastUploadedImageInfo(''); setUploadedFilePathFromBackend(null);
      socket.emit('request_qr_code', {});
    } else if (isDrawingActive) {
      alert("Cannot request QR code while drawing is in progress.");
    }
  };

  // Direct File Input Handlers
  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file && file.type.startsWith('image/')) {
      setSelectedFile(file);
      setImagePreviewUrl(URL.createObjectURL(file));
      // Clear QR upload state
      setQrCodeImage(null); setQrUploadUrl('');
      setLastUploadedImageInfo(''); setUploadedFilePathFromBackend(null);
    } else {
      setSelectedFile(null);
      setImagePreviewUrl(null);
      alert('Please select an image file.');
    }
  };

  const handleDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file && file.type.startsWith('image/')) {
      setSelectedFile(file);
      setImagePreviewUrl(URL.createObjectURL(file));
      // Clear QR upload state
      setQrCodeImage(null); setQrUploadUrl('');
      setLastUploadedImageInfo(''); setUploadedFilePathFromBackend(null);
    } else {
      setSelectedFile(null);
      setImagePreviewUrl(null);
      alert('Please drop an image file.');
    }
  }, []);

  const handleDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!isDragging) setIsDragging(true);
  }, [isDragging]);

  const handleDragLeave = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(false);
  }, []);

  const triggerFileInput = () => {
    fileInputRef.current?.click();
  };

  // Handler to send the locally selected file to the backend
  const sendSelectedFileToBackend = () => {
    if (!selectedFile || !socket || !isConnectedToBackend || isDrawingActive) {
      alert("Cannot send file. Check connection, file selection, or drawing status.");
      return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
      const base64Data = (e.target?.result as string)?.split(',')[1];
      if (base64Data) {
        setLastUploadedImageInfo(`Sending ${selectedFile.name} to backend...`);
        socket.emit('direct_image_upload', {
          filename: selectedFile.name,
          fileData: base64Data, // Send as base64
        });
      } else {
        alert("Could not read file data.");
        setLastUploadedImageInfo("Error reading file.");
      }
    };
    reader.onerror = () => {
        alert("Error reading file.");
        setLastUploadedImageInfo("Error reading file.");
    };
    reader.readAsDataURL(selectedFile); // Reads as base64 data URL
  };


  // Process and Draw (Common for QR or Direct Upload)
  const handleProcessAndDrawUploadedImage = () => {
    if (isDrawingActive) {
      alert("A drawing is already in progress."); return;
    }
    if (!isRobotConnected) {
      alert("Please connect to the robot first."); 
      setLastCommandResponse("Error: Robot not connected."); return;
    }
    if (uploadedFilePathFromBackend) {
      const originalFilename = lastUploadedImageInfo.includes("Received: ") 
        ? lastUploadedImageInfo.split("Received: ")[1].split(". Ready")[0] 
        : "uploaded_image";
      socket.emit('process_image_for_drawing', { 
          filepath: uploadedFilePathFromBackend,
          original_filename: originalFilename 
      });
      setLastCommandResponse(`Sent request to process & draw: ${originalFilename}`);
      setDrawingProgressMessage("Requesting image processing and drawing..."); 
    } else {
      alert("No image has been successfully uploaded to the backend yet.");
      setLastCommandResponse("Error: No backend image path available.");
    }
  };

  return (
    <div className="App">
      <h1>S2A Robotic Drawing Control</h1>
      <p>Backend Connection: {isConnectedToBackend ? 'Connected' : 'Disconnected'}</p>
      <hr />
      
      <h2>Image Input</h2>
      <div className="image-input-methods" style={{ display: 'flex', justifyContent: 'space-around', marginBottom: '20px' }}>
        {/* QR Code Upload Section */}
        <div className="qr-upload-section" style={{border: '1px solid #555', padding: '15px', borderRadius: '8px', width: '45%'}}>
          <h3>Upload via QR Code</h3>
          <button onClick={requestQrCode} disabled={!isConnectedToBackend || isDrawingActive || !!selectedFile}>
            Get QR Code for Phone Upload
          </button>
          {qrUploadUrl && !qrCodeImage && <p style={{fontSize: '0.8em', wordBreak: 'break-all'}}><small>{qrUploadUrl}</small></p>}
          {qrCodeImage && (
            <div>
              <p><small>Scan to upload. URL: {qrUploadUrl}</small></p>
              <img src={qrCodeImage} alt="QR Code for Upload" style={{border: "1px solid #ccc", marginTop:"10px", maxWidth: '150px'}} />
            </div>
          )}
        </div>

        {/* Direct File Upload Section */}
        <div 
          className="direct-upload-section"
          style={{
            border: isDragging ? '2px dashed #007bff' : '1px solid #555',
            padding: '15px',
            borderRadius: '8px',
            width: '45%',
            textAlign: 'center',
            backgroundColor: isDragging ? '#333' : 'transparent',
            transition: 'background-color 0.2s, border-color 0.2s'
          }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          <h3>Upload from Desktop</h3>
          <input 
            type="file" 
            accept="image/*" 
            onChange={handleFileSelect} 
            ref={fileInputRef} 
            style={{ display: 'none' }} 
            disabled={isDrawingActive || !!qrCodeImage}
          />
          <button onClick={triggerFileInput} disabled={isDrawingActive || !!qrCodeImage}>
            Choose Image File
          </button>
          <p style={{fontSize: '0.9em', marginTop: '10px'}}>Or drag & drop image here</p>
          
          {imagePreviewUrl && selectedFile && (
            <div style={{marginTop: '15px'}}>
              <p>Preview:</p>
              <img src={imagePreviewUrl} alt="Selected preview" style={{maxWidth: '200px', maxHeight: '200px', border: '1px solid #ccc', borderRadius: '4px'}}/>
              <p style={{fontSize: '0.8em'}}>{selectedFile.name}</p>
              <button 
                onClick={sendSelectedFileToBackend} 
                disabled={!selectedFile || isDrawingActive || !isConnectedToBackend}
                style={{marginTop: '10px'}}
              >
                Upload to Backend
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Status and Process Button - Common for both upload methods */}
      {lastUploadedImageInfo && <p style={{color: lastUploadedImageInfo.startsWith("Received:") ? "green" : (lastUploadedImageInfo.startsWith("Error") ? "red" : "goldenrod"), fontWeight: 'bold'}}>{lastUploadedImageInfo}</p>}
      
      {uploadedFilePathFromBackend && (
        <button 
          onClick={handleProcessAndDrawUploadedImage} 
          disabled={isDrawingActive || !isRobotConnected || !isConnectedToBackend} 
          style={{marginTop: "10px", backgroundColor: '#28a745', color: 'white', padding: '10px 20px', fontSize: '1.1em'}}
        >
          Process & Draw Uploaded Image
        </button>
      )}
       {isDrawingActive && <p style={{color: "cyan", fontWeight: "bold"}}>Drawing Active: {drawingProgressMessage}</p>}
       {!isDrawingActive && drawingProgressMessage && !lastUploadedImageInfo.startsWith("Received:") && <p>{drawingProgressMessage}</p>}


      <hr />
      <h2>Robot Control</h2>
      <button onClick={handleConnectRobot} disabled={!isConnectedToBackend || isRobotConnected || isDrawingActive}>
        Connect to Robot
      </button>
      <button onClick={handleDisconnectRobot} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive}>
        Disconnect (Graceful)
      </button>
      <br />
      <button onClick={sendGoHomeCommand} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive} style={{marginTop: '5px'}}>
        Send Robot to Home
      </button>
      <button onClick={sendSafeCenterCommand} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive} style={{marginTop: '5px', marginLeft: '5px'}}>
        Send to Safe Center
      </button>
      <p>{robotStatusMessage}</p>
      <p>Last Command: <span style={{fontSize: '0.9em', color: '#aaa'}}>{lastCommandResponse}</span></p>
    </div>
  );
}

export default App;
