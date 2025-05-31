// frontend/s2a-drawing-ui/src/App.tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import './App.css'; // Make sure to create/update this for styles

const PYTHON_BACKEND_URL = 'http://localhost:5555';

let socket: Socket;
let mediaRecorder: MediaRecorder | null = null;
let audioChunks: Blob[] = [];

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

  // Voice Interaction States
  const [isRecording, setIsRecording] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState('Tap mic to start');
  const [transcribedText, setTranscribedText] = useState('');
  const [llmResponse, setLlmResponse] = useState('');
  const audioStreamRef = useRef<MediaStream | null>(null);


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
      setIsRecording(false); // Reset recording state on disconnect
      setVoiceStatus('Tap mic to start');
    });

    socket.on('response', (data: { data: string }) => {
      setBackendMessage(data.data);
    });

    socket.on('robot_connection_status', (data: { success: boolean, message: string }) => {
      setIsRobotConnected(data.success);
      setRobotStatusMessage(`Robot: ${data.message}`);
    });

    socket.on('command_response', (data: { success: boolean, message: string, command_sent?: string }) => {
      setLastCommandResponse(`Cmd: ${data.command_sent || 'N/A'} -> Resp: ${data.message} (Success: ${data.success})`);
    });

    socket.on('qr_code_data', (data: { qr_image_base64?: string, upload_url?: string, error?: string }) => {
      if (data.error) {
        setQrUploadUrl(`Error generating QR: ${data.error}`);
        setQrCodeImage(null);
      } else if (data.qr_image_base64 && data.upload_url) {
        setQrCodeImage(`data:image/png;base64,${data.qr_image_base64}`);
        setQrUploadUrl(data.upload_url);
        setSelectedFile(null); setImagePreviewUrl(null);
      }
      setLastUploadedImageInfo(''); setUploadedFilePathFromBackend(null);
    });

    const handleImageUploadSuccess = (data: { success: boolean, message: string, original_filename?: string, filepath_on_server?: string}) => {
      if (data.success && data.filepath_on_server) {
        setLastUploadedImageInfo(`Received: ${data.original_filename || 'image'}. Ready for processing.`);
        setUploadedFilePathFromBackend(data.filepath_on_server);
        setQrCodeImage(null); setQrUploadUrl('');
        setSelectedFile(null); setImagePreviewUrl(null);
      } else {
        setLastUploadedImageInfo(`Upload Info: ${data.message}`);
        setUploadedFilePathFromBackend(null);
      }
    };

    socket.on('qr_image_received', handleImageUploadSuccess);
    socket.on('direct_image_upload_response', handleImageUploadSuccess); 

    socket.on('drawing_status_update', (data: { active: boolean, message: string }) => {
      setIsDrawingActive(data.active);
      setDrawingProgressMessage(data.message);
    });

    // Voice related socket events
    socket.on('transcription_result', (data: { text?: string, error?: string }) => {
        if (data.error) {
            setVoiceStatus(`Transcription Error: ${data.error}`);
            setTranscribedText('');
        } else if (data.text) {
            setVoiceStatus('Transcription complete. Waiting for LLM...');
            setTranscribedText(data.text);
        }
        // Potentially stop a "processing" animation here
    });

    socket.on('llm_action_response', (data: { action?: any, message?: string, error?: string }) => {
        if (data.error) {
            setVoiceStatus(`LLM Error: ${data.error}`);
            setLlmResponse('');
        } else if (data.message) { // Assuming LLM might send a textual confirmation or clarification
            setVoiceStatus('LLM processed command.');
            setLlmResponse(data.message);
        } else if (data.action) {
             setVoiceStatus('LLM action received.');
             // Here you would typically trigger further actions based on the LLM's structured output
             // For now, just displaying it.
             setLlmResponse(`Action: ${JSON.stringify(data.action)}`);
        }
        // Potentially stop a "processing" animation here
    });


    return () => { 
        if (socket) socket.disconnect(); 
        if (audioStreamRef.current) {
            audioStreamRef.current.getTracks().forEach(track => track.stop());
        }
    };
  }, []);

  // Robot Control Handlers (no change)
  const handleConnectRobot = () => { if (!isDrawingActive && socket) socket.emit('robot_connect_request', {}); }
  const handleDisconnectRobot = () => { if (!isDrawingActive && socket) socket.emit('robot_disconnect_request', {}); }
  const sendGoHomeCommand = () => { if (!isDrawingActive && socket) socket.emit('send_robot_command', { type: 'go_home' }); }
  const sendSafeCenterCommand = () => { if (!isDrawingActive && socket) socket.emit('send_robot_command', { type: 'move_to_safe_center' }); }
  
  // QR Code Request Handler (no change)
  const requestQrCode = () => {
    if (socket && isConnectedToBackend && !isDrawingActive) {
      setQrCodeImage(null); setQrUploadUrl('Requesting QR Code...');
      setSelectedFile(null); setImagePreviewUrl(null); 
      setLastUploadedImageInfo(''); setUploadedFilePathFromBackend(null);
      socket.emit('request_qr_code', {});
    } else if (isDrawingActive) { alert("Cannot request QR code while drawing is in progress."); }
  };

  // Direct File Input Handlers (no change)
  const processNewFile = (file: File | null) => {
    if (file && file.type.startsWith('image/')) {
      setSelectedFile(file); setImagePreviewUrl(URL.createObjectURL(file));
      setQrCodeImage(null); setQrUploadUrl('');
      setLastUploadedImageInfo(''); setUploadedFilePathFromBackend(null);
    } else {
      setSelectedFile(null); setImagePreviewUrl(null);
      if (file) { alert('Please select/drop an image file.'); }
    }
  };
  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => { processNewFile(event.target.files?.[0] || null); };
  const handleDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault(); event.stopPropagation(); setIsDragging(false);
    processNewFile(event.dataTransfer.files?.[0] || null);
  }, []);
  const handleDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault(); event.stopPropagation();
    if (!isDrawingActive && !isDragging) setIsDragging(true);
  }, [isDrawingActive, isDragging]);
  const handleDragLeave = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault(); event.stopPropagation(); setIsDragging(false);
  }, []);
  const triggerFileInput = () => { if (!isDrawingActive) { fileInputRef.current?.click(); } };
  const sendSelectedFileToBackend = () => {
    if (!selectedFile || !socket || !isConnectedToBackend || isDrawingActive) {
      alert("Cannot send file. Check connection, file selection, or drawing status."); return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const base64Data = (e.target?.result as string)?.split(',')[1];
      if (base64Data) {
        setLastUploadedImageInfo(`Sending ${selectedFile.name} to backend...`);
        socket.emit('direct_image_upload', { filename: selectedFile.name, fileData: base64Data });
      } else { alert("Could not read file data."); setLastUploadedImageInfo("Error reading file.");}
    };
    reader.onerror = () => { alert("Error reading file."); setLastUploadedImageInfo("Error reading file.");};
    reader.readAsDataURL(selectedFile); 
  };

  // Process and Draw (no change)
  const handleProcessAndDrawUploadedImage = () => {
    if (isDrawingActive) { alert("A drawing is already in progress."); return; }
    if (!isRobotConnected) { alert("Please connect to the robot first."); setLastCommandResponse("Error: Robot not connected."); return; }
    if (uploadedFilePathFromBackend) {
      const originalFilename = lastUploadedImageInfo.includes("Received: ") ? lastUploadedImageInfo.split("Received: ")[1].split(". Ready")[0] : "uploaded_image";
      socket.emit('process_image_for_drawing', { filepath: uploadedFilePathFromBackend, original_filename: originalFilename });
      setLastCommandResponse(`Sent request to process & draw: ${originalFilename}`);
      setDrawingProgressMessage("Requesting image processing and drawing..."); 
    } else { alert("No image has been successfully uploaded to the backend yet."); setLastCommandResponse("Error: No backend image path available.");}
  };

  // Voice Recording Handlers
  const startRecording = async () => {
    if (isDrawingActive || !isConnectedToBackend) {
        alert("Cannot record voice while drawing is active or backend is disconnected.");
        return;
    }
    setVoiceStatus('Requesting mic permission...');
    setTranscribedText('');
    setLlmResponse('');
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioStreamRef.current = stream; // Store stream to stop it later
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' }); // or 'audio/wav' if backend prefers
        audioChunks = [];

        mediaRecorder.ondataavailable = (event) => {
            audioChunks.push(event.data);
        };

        mediaRecorder.onstop = () => {
            setVoiceStatus('Processing voice...');
            const audioBlob = new Blob(audioChunks, { type: mediaRecorder?.mimeType });
            
            // Convert Blob to base64 to send over Socket.IO
            const reader = new FileReader();
            reader.onloadend = () => {
                const base64Audio = (reader.result as string).split(',')[1];
                if (socket && base64Audio) {
                    console.log("Frontend: Sending audio_chunk (actually full audio as base64)");
                    socket.emit('audio_chunk', { audioData: base64Audio, mimeType: mediaRecorder?.mimeType });
                } else {
                    setVoiceStatus("Error: Could not send audio data.");
                }
            };
            reader.onerror = () => {
                setVoiceStatus("Error reading audio blob.");
            };
            reader.readAsDataURL(audioBlob);

            // Stop microphone tracks
            if (audioStreamRef.current) {
                audioStreamRef.current.getTracks().forEach(track => track.stop());
                audioStreamRef.current = null;
            }
        };

        mediaRecorder.start();
        setIsRecording(true);
        setVoiceStatus('Recording... Tap mic to stop.');
    } catch (err) {
        console.error("Error accessing microphone:", err);
        setVoiceStatus('Mic permission denied or error.');
        if (audioStreamRef.current) { // Clean up if stream was partially acquired
            audioStreamRef.current.getTracks().forEach(track => track.stop());
            audioStreamRef.current = null;
        }
    }
  };

  const stopRecording = () => {
    if (mediaRecorder && isRecording) {
        mediaRecorder.stop();
        setIsRecording(false);
        // Voice status will be updated by onstop handler
    }
  };

  const handleMicButtonClick = () => {
    if (isRecording) {
        stopRecording();
    } else {
        startRecording();
    }
  };

  return (
    <div className="App">
      <h1>S2A Robotic Drawing Control</h1>
      <p>Backend Connection: {isConnectedToBackend ? 'Connected' : 'Disconnected'}</p>
      <hr />

      {/* Voice Interaction Section */}
      <div className="voice-interaction-section" style={{ padding: '15px', border: '1px solid #555', borderRadius: '8px', marginBottom: '20px', textAlign: 'center' }}>
        <h2>Voice Command</h2>
        <button 
            onClick={handleMicButtonClick} 
            disabled={!isConnectedToBackend || isDrawingActive}
            style={{
                backgroundColor: isRecording ? '#dc3545' : '#007bff', // Red when recording, Blue otherwise
                color: 'white',
                padding: '10px 20px',
                fontSize: '1.2em',
                borderRadius: '50%', // Make it round
                width: '80px',
                height: '80px',
                border: 'none',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center'
            }}
            title={isRecording ? "Stop Recording" : "Start Recording"}
        >
            {/* Simple Mic SVG Icon */}
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" fill="currentColor" viewBox="0 0 16 16">
                <path d="M5 3a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0V3z"/>
                <path d="M3.5 6.5A.5.5 0 0 1 4 7v1a4 4 0 0 0 8 0V7a.5.5 0 0 1 1 0v1a5 5 0 0 1-4.5 4.975V15h3a.5.5 0 0 1 0 1h-7a.5.5 0 0 1 0-1h3v-2.025A5 5 0 0 1 3 8V7a.5.5 0 0 1 .5-.5z"/>
            </svg>
        </button>
        <p style={{ marginTop: '10px', minHeight: '20px' }}>{voiceStatus}</p>
        {transcribedText && <p><b>You said:</b> {transcribedText}</p>}
        {llmResponse && <p><b>LLM:</b> {llmResponse}</p>}
      </div>
      
      <hr />
      <h2>Image Input</h2>
      {/* Image input sections remain the same */}
      <div className="image-input-methods" style={{ display: 'flex', justifyContent: 'space-around', marginBottom: '20px' }}>
        <div className="qr-upload-section" style={{border: '1px solid #555', padding: '15px', borderRadius: '8px', width: '45%'}}>
          <h3>Upload via QR Code</h3>
          <button onClick={requestQrCode} disabled={!isConnectedToBackend || isDrawingActive || isRecording}>
            Get QR Code for Phone Upload
          </button>
          {qrUploadUrl && !qrCodeImage && <p style={{fontSize: '0.8em', wordBreak: 'break-all'}}><small>{qrUploadUrl}</small></p>}
          {qrCodeImage && ( <div> <p><small>Scan to upload. URL: {qrUploadUrl}</small></p> <img src={qrCodeImage} alt="QR Code for Upload" style={{border: "1px solid #ccc", marginTop:"10px", maxWidth: '150px'}} /> </div> )}
        </div>
        <div className="direct-upload-section" style={{ border: isDragging ? '2px dashed #007bff' : '1px solid #555', padding: '15px', borderRadius: '8px', width: '45%', textAlign: 'center', backgroundColor: isDragging ? '#333' : 'transparent', transition: 'background-color 0.2s, border-color 0.2s', opacity: (isDrawingActive || isRecording) ? 0.5 : 1, pointerEvents: (isDrawingActive || isRecording) ? 'none' : 'auto' }}
          onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop} >
          <h3>Upload from Desktop</h3>
          <input type="file" accept="image/*" onChange={handleFileSelect} ref={fileInputRef} style={{ display: 'none' }} disabled={isDrawingActive || isRecording} />
          <button onClick={triggerFileInput} disabled={isDrawingActive || isRecording}> Choose Image File </button>
          <p style={{fontSize: '0.9em', marginTop: '10px'}}>Or drag & drop image here</p>
          {imagePreviewUrl && selectedFile && ( <div style={{marginTop: '15px'}}> <p>Preview:</p> <img src={imagePreviewUrl} alt="Selected preview" style={{maxWidth: '200px', maxHeight: '200px', border: '1px solid #ccc', borderRadius: '4px'}}/> <p style={{fontSize: '0.8em'}}>{selectedFile.name}</p> <button onClick={sendSelectedFileToBackend} disabled={!selectedFile || isDrawingActive || !isConnectedToBackend || isRecording} style={{marginTop: '10px'}} > Upload to Backend </button> </div> )}
        </div>
      </div>
      {lastUploadedImageInfo && <p style={{color: lastUploadedImageInfo.startsWith("Received:") ? "green" : (lastUploadedImageInfo.startsWith("Error") ? "red" : "goldenrod"), fontWeight: 'bold'}}>{lastUploadedImageInfo}</p>}
      {uploadedFilePathFromBackend && ( <button onClick={handleProcessAndDrawUploadedImage} disabled={isDrawingActive || !isRobotConnected || !isConnectedToBackend || isRecording} style={{marginTop: "10px", backgroundColor: '#28a745', color: 'white', padding: '10px 20px', fontSize: '1.1em'}} > Process & Draw Uploaded Image </button> )}
      {isDrawingActive && <p style={{color: "cyan", fontWeight: "bold"}}>Drawing Active: {drawingProgressMessage}</p>}
      {!isDrawingActive && drawingProgressMessage && !lastUploadedImageInfo.startsWith("Received:") && <p>{drawingProgressMessage}</p>}

      <hr />
      <h2>Robot Control</h2>
      {/* Robot control buttons remain the same, but also disabled if recording */}
      <button onClick={handleConnectRobot} disabled={!isConnectedToBackend || isRobotConnected || isDrawingActive || isRecording}> Connect to Robot </button>
      <button onClick={handleDisconnectRobot} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive || isRecording}> Disconnect (Graceful) </button>
      <br />
      <button onClick={sendGoHomeCommand} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive || isRecording} style={{marginTop: '5px'}}> Send Robot to Home </button>
      <button onClick={sendSafeCenterCommand} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive || isRecording} style={{marginTop: '5px', marginLeft: '5px'}}> Send to Safe Center </button>
      <p>{robotStatusMessage}</p>
      <p>Last Command: <span style={{fontSize: '0.9em', color: '#aaa'}}>{lastCommandResponse}</span></p>
    </div>
  );
}

export default App;
