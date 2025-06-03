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

  // Voice & Text Interaction States
  const [isRecording, setIsRecording] = useState(false);
  const [interactionStatus, setInteractionStatus] = useState('Tap mic or type command.'); // General status for voice/text
  const [rawTranscribedText, setRawTranscribedText] = useState(''); // "You said: ..."
  const [editableCommandText, setEditableCommandText] = useState(''); // For editing STT or typing new
  const [llmResponse, setLlmResponse] = useState('');
  const audioStreamRef = useRef<MediaStream | null>(null);


  // Effect for Socket.IO setup and event listeners
  useEffect(() => {
    socket = io(PYTHON_BACKEND_URL, { transports: ['websocket'] });

    socket.on('connect', () => {
      console.log('Frontend: Connected to Python backend via Socket.IO!');
      setIsConnectedToBackend(true);
      setBackendMessage('Connected to Python Backend!');
      setInteractionStatus('Tap mic or type command.');
    });

    socket.on('disconnect', () => {
      console.log('Frontend: Disconnected from Python backend.');
      setIsConnectedToBackend(false);
      setBackendMessage('Disconnected from Python Backend.');
      setIsRobotConnected(false);
      setRobotStatusMessage('Robot: Disconnected (backend offline)');
      setIsDrawingActive(false); 
      setDrawingProgressMessage('');
      setIsRecording(false); 
      setInteractionStatus('Backend offline. Please refresh or check server.');
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

    // Listener for transcription result (text only)
    socket.on('transcription_result', (data: { text?: string, error?: string }) => {
        if (data.error) {
            setInteractionStatus(`Transcription Error: ${data.error}`);
            setRawTranscribedText('');
            setEditableCommandText('');
            setLlmResponse('');
        } else if (data.text) {
            setRawTranscribedText(data.text); // Display "You said: ..."
            setEditableCommandText(data.text); // Populate editable field
            setLlmResponse(''); // Clear previous LLM response
            setInteractionStatus('Edit command below or send to Robotist.');
        }
    });
    
    // Listener for streaming LLM responses
    socket.on('llm_response_chunk', (data: { chunk?: string, error?: string, done: boolean, final_message?: string }) => {
        if (data.error) {
            setLlmResponse(prev => prev + `\n[Error: ${data.error}]`);
            setInteractionStatus('LLM processing error.');
        } else if (data.chunk) {
            setLlmResponse(prev => prev + data.chunk);
            if (!data.done) {
                setInteractionStatus('Robotist is typing...');
            }
        }
        
        if (data.done) {
            if (data.final_message && !data.error) {
                setLlmResponse(data.final_message);
            }
            setInteractionStatus('Ready for next command.');
            if (data.error) {
                 setInteractionStatus(`LLM Error: ${data.error}`);
            } else if (!data.final_message && !data.chunk && llmResponse === "") { // Check if llmResponse was empty before this final empty chunk
                 setInteractionStatus('Robotist finished.');
            }
        }
    });

    return () => { 
        if (socket) socket.disconnect(); 
        if (audioStreamRef.current) {
            audioStreamRef.current.getTracks().forEach(track => track.stop());
        }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Removed llmResponse from dependency array as it caused issues with final message setting

  // Robot Control Handlers
  const handleConnectRobot = () => { if (!isDrawingActive && socket) socket.emit('robot_connect_request', {}); }
  const handleDisconnectRobot = () => { if (!isDrawingActive && socket) socket.emit('robot_disconnect_request', {}); }
  const sendGoHomeCommand = () => { if (!isDrawingActive && socket) socket.emit('send_robot_command', { type: 'go_home' }); }
  const sendSafeCenterCommand = () => { if (!isDrawingActive && socket) socket.emit('send_robot_command', { type: 'move_to_safe_center' }); }
  
  // QR Code Request Handler
  const requestQrCode = () => {
    if (socket && isConnectedToBackend && !isDrawingActive) {
      setQrCodeImage(null); setQrUploadUrl('Requesting QR Code...');
      setSelectedFile(null); setImagePreviewUrl(null); 
      setLastUploadedImageInfo(''); setUploadedFilePathFromBackend(null);
      socket.emit('request_qr_code', {});
    } else if (isDrawingActive) { alert("Cannot request QR code while drawing is in progress."); }
  };

  // Direct File Input Handlers
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

  // Process and Draw
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
    // Clear previous interaction states
    setRawTranscribedText('');
    setEditableCommandText('');
    setLlmResponse('');
    setInteractionStatus('Requesting mic permission...');

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioStreamRef.current = stream; 
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' }); 
        audioChunks = [];

        mediaRecorder.ondataavailable = (event) => {
            audioChunks.push(event.data);
        };

        mediaRecorder.onstop = () => {
            setInteractionStatus('Sending audio for transcription...');
            const audioBlob = new Blob(audioChunks, { type: mediaRecorder?.mimeType });
            
            const reader = new FileReader();
            reader.onloadend = () => {
                const base64Audio = (reader.result as string).split(',')[1];
                if (socket && base64Audio) {
                    console.log("Frontend: Sending audio data for transcription only.");
                    // Backend's 'audio_chunk' should now only transcribe and send text back
                    socket.emit('audio_chunk', { audioData: base64Audio, mimeType: mediaRecorder?.mimeType });
                } else {
                    setInteractionStatus("Error: Could not send audio data.");
                }
            };
            reader.onerror = () => {
                setInteractionStatus("Error reading audio blob.");
            };
            reader.readAsDataURL(audioBlob);

            if (audioStreamRef.current) {
                audioStreamRef.current.getTracks().forEach(track => track.stop());
                audioStreamRef.current = null;
            }
        };

        mediaRecorder.start();
        setIsRecording(true);
        setInteractionStatus('Recording... Tap mic to stop.');
    } catch (err) {
        console.error("Error accessing microphone:", err);
        setInteractionStatus('Mic permission denied or error.');
        if (audioStreamRef.current) { 
            audioStreamRef.current.getTracks().forEach(track => track.stop());
            audioStreamRef.current = null;
        }
    }
  };

  const stopRecording = () => {
    if (mediaRecorder && isRecording) {
        mediaRecorder.stop();
        setIsRecording(false);
        // interactionStatus will be updated by onstop, then by transcription_result
    }
  };

  const handleMicButtonClick = () => {
    if (isRecording) {
        stopRecording();
    } else {
        // Clear text input field when starting new voice recording
        // setEditableCommandText(''); // Keep this if user might want to edit a typed command then speak
        startRecording();
    }
  };

  // Handler for sending the (potentially edited) command text to LLM
  const submitTextToLLM = (text: string) => {
    if (!text.trim()) {
      alert("Command text cannot be empty.");
      setInteractionStatus('Command empty. Tap mic or type command.');
      return;
    }

    // Enhanced Debugging Logs
    console.log('[Frontend DEBUG] Attempting to submit to LLM. Text:', text);
    console.log('[Frontend DEBUG] Socket object available:', !!socket);
    console.log('[Frontend DEBUG] isConnectedToBackend state:', isConnectedToBackend);
    if (socket) {
      console.log('[Frontend DEBUG] Socket connected property:', socket.connected);
    }


    if (socket && isConnectedToBackend && socket.connected) { 
      console.log("Frontend: Sending text to LLM for processing via 'submit_text_to_llm' event. Payload:", { text_command: text });
      socket.emit('submit_text_to_llm', { text_command: text });
      setLlmResponse(''); 
      setInteractionStatus('Robotist is thinking...');
      // Clear raw transcription if this submission didn't originate from it directly
      // or if it's a significantly edited version. 
      if (text !== rawTranscribedText) {
        setRawTranscribedText('');
      }
    } else {
      let debugMessage = "Cannot send command. ";
      if (!socket) debugMessage += "Socket object is null/undefined. ";
      if (!isConnectedToBackend) debugMessage += "isConnectedToBackend state is false. ";
      if (socket && !socket.connected) debugMessage += "socket.connected property is false. ";
      
      console.error('[Frontend DEBUG] ' + debugMessage, { 
        socketExists: !!socket, 
        isConnectedToBackendState: isConnectedToBackend, 
        socketConnectedProp: socket?.connected 
      });
      alert(debugMessage + "Please check backend connection and refresh if necessary.");
      setInteractionStatus('Backend disconnected or socket issue.');
    }
  };

  const handleSendEditableCommand = () => {
    submitTextToLLM(editableCommandText);
  };

  return (
    <div className="App">
      <h1>S2A Robotic Drawing Control</h1>
      <p>Backend Connection: {isConnectedToBackend ? 'Connected' : 'Disconnected'}</p>
      <hr />

      <div className="interaction-section" style={{ padding: '15px', border: '1px solid #555', borderRadius: '8px', marginBottom: '20px', textAlign: 'left' }}>
        <h2>Robotist Interaction</h2>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: '15px' }}>
          <button 
              onClick={handleMicButtonClick} 
              disabled={!isConnectedToBackend || isDrawingActive}
              style={{
                  backgroundColor: isRecording ? '#dc3545' : '#007bff', 
                  color: 'white', padding: '10px', fontSize: '1em',
                  borderRadius: '50%', width: '60px', height: '60px', // Smaller mic button
                  border: 'none', cursor: 'pointer', display: 'flex',
                  alignItems: 'center', justifyContent: 'center', marginRight: '15px'
              }}
              title={isRecording ? "Stop Recording" : "Start Voice Command"}
          >
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16">
                  <path d="M5 3a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0V3z"/>
                  <path d="M3.5 6.5A.5.5 0 0 1 4 7v1a4 4 0 0 0 8 0V7a.5.5 0 0 1 1 0v1a5 5 0 0 1-4.5 4.975V15h3a.5.5 0 0 1 0 1h-7a.5.5 0 0 1 0-1h3v-2.025A5 5 0 0 1 3 8V7a.5.5 0 0 1 .5-.5z"/>
              </svg>
          </button>
          <p style={{ margin: '0', flexGrow: 1 }}>{interactionStatus}</p>
        </div>

        {rawTranscribedText && <p style={{fontSize: '0.9em', color: '#aaa'}}><em>You said: "{rawTranscribedText}"</em></p>}
        
        <textarea 
            value={editableCommandText}
            onChange={(e) => setEditableCommandText(e.target.value)}
            placeholder="Type command or edit transcribed text here..."
            rows={3}
            style={{ width: 'calc(100% - 22px)', padding: '10px', marginBottom: '10px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: '#fff' }}
            disabled={!isConnectedToBackend || isDrawingActive || isRecording}
        />
        <button 
            onClick={handleSendEditableCommand} 
            disabled={!editableCommandText.trim() || !isConnectedToBackend || isDrawingActive || isRecording}
            style={{padding: '10px 15px'}}
        >
            Send Command to Robotist
        </button>

        {llmResponse && (
          <div style={{marginTop: '15px', padding: '10px', border: '1px solid #444', borderRadius: '4px', backgroundColor: '#2a2a2a'}}>
            <p style={{whiteSpace: 'pre-wrap', margin: 0}}><b>Robotist:</b> {llmResponse}</p>
          </div>
        )}
      </div>
      
      <hr />
      <h2>Image Input</h2>
      <div className="image-input-methods" style={{ display: 'flex', justifyContent: 'space-around', marginBottom: '20px', flexWrap: 'wrap', gap: '10px' }}>
        <div className="qr-upload-section" style={{border: '1px solid #555', padding: '15px', borderRadius: '8px', flex: '1 1 300px', minWidth: '280px'}}>
          <h3>Upload via QR Code</h3>
          <button onClick={requestQrCode} disabled={!isConnectedToBackend || isDrawingActive || isRecording}>
            Get QR Code for Phone Upload
          </button>
          {qrUploadUrl && !qrCodeImage && <p style={{fontSize: '0.8em', wordBreak: 'break-all'}}><small>{qrUploadUrl}</small></p>}
          {qrCodeImage && ( <div> <p><small>Scan to upload. URL: {qrUploadUrl}</small></p> <img src={qrCodeImage} alt="QR Code for Upload" style={{border: "1px solid #ccc", marginTop:"10px", maxWidth: '150px'}} /> </div> )}
        </div>
        <div className="direct-upload-section" style={{ border: isDragging ? '2px dashed #007bff' : '1px solid #555', padding: '15px', borderRadius: '8px', flex: '1 1 300px', minWidth: '280px', textAlign: 'center', backgroundColor: isDragging ? '#333' : 'transparent', transition: 'background-color 0.2s, border-color 0.2s', opacity: (isDrawingActive || isRecording) ? 0.5 : 1, pointerEvents: (isDrawingActive || isRecording) ? 'none' : 'auto' }}
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
