// frontend/s2a-drawing-ui/src/App.tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import './App.css'; 

// *** UPDATED: Changed URL to HTTPS ***
const PYTHON_BACKEND_URL = 'https://localhost:5555';

let socket: Socket;
let mediaRecorder: MediaRecorder | null = null;
let audioChunks: Blob[] = [];

// --- Helper Components & Types ---
const MicIcon = () => ( <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16"> <path d="M5 3a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0V3z"/> <path d="M3.5 6.5A.5.5 0 0 1 4 7v1a4 4 0 0 0 8 0V7a.5.5 0 0 1 1 0v1a5 5 0 0 1-4.5 4.975V15h3a.5.5 0 0 1 0 1h-7a.5.5 0 0 1 0-1h3v-2.025A5 5 0 0 1 3 8V7a.5.5 0 0 1 .5-.5z"/> </svg> );
const StopIcon = () => ( <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16"> <path d="M5 3.5h6A1.5 1.5 0 0 1 12.5 5v6a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 11V5A1.5 1.5 0 0 1 5 3.5z"/> </svg> );

interface ThresholdOption { key: string; label: string; t1: number; t2: number; }
const THRESHOLD_OPTIONS: ThresholdOption[] = Array.from({ length: 10 }, (_, i) => ({ 
    key: `opt${i + 1}`, 
    label: `Option ${i + 1}`, 
    t1: (i + 1) * 10, 
    t2: (i + 1) * 20 
}));

interface DrawingHistoryItem {
    drawing_id: string;
    original_filename: string;
    status: string;
    last_updated: string;
    total_commands?: number;
    progress?: number;
}

// --- Main App Component ---
function App() {
  const [isConnectedToBackend, setIsConnectedToBackend] = useState(false);
  const [isRobotConnected, setIsRobotConnected] = useState(false);
  const [robotStatusMessage, setRobotStatusMessage] = useState('Robot: Not connected');
  const [lastCommandResponse, setLastCommandResponse] = useState('');
  const [useRealRobot, setUseRealRobot] = useState(false);

  // *** NEW: State for pen down depth ***
  const [penDownZ, setPenDownZ] = useState<number>(-7.0);

  const [qrCodeImage, setQrCodeImage] = useState<string | null>(null);
  const [qrUploadUrl, setQrUploadUrl] = useState<string>('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [lastUploadedImageInfo, setLastUploadedImageInfo] = useState<{message: string, filepath: string | null}>({message: '', filepath: null});

  const [isDrawing, setIsDrawing] = useState(false);
  const [drawingProgress, setDrawingProgress] = useState(0);
  const [drawingStatusText, setDrawingStatusText] = useState('Idle');
  const [drawingHistory, setDrawingHistory] = useState<DrawingHistoryItem[]>([]);

  const [isRecording, setIsRecording] = useState(false);
  const [interactionStatus, setInteractionStatus] = useState('Tap mic or type command.');
  const [rawTranscribedText, setRawTranscribedText] = useState('');
  const [editableCommandText, setEditableCommandText] = useState('');
  const [llmResponse, setLlmResponse] = useState('');
  const audioStreamRef = useRef<MediaStream | null>(null);

  const [xCoord, setXCoord] = useState('');
  const [yCoord, setYCoord] = useState(''); 
  const [zCoord, setZCoord] = useState('');

  const [showThresholdModal, setShowThresholdModal] = useState(false);
  const [selectedThresholdKey, setSelectedThresholdKey] = useState<string>(THRESHOLD_OPTIONS[2].key);
  const [thresholdPreviewImage, setThresholdPreviewImage] = useState<string | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);

  const clearActiveDrawingState = useCallback(() => {
    setIsDrawing(false);
    setDrawingProgress(0);
    setDrawingStatusText('Idle');
  }, []);

  useEffect(() => {
    socket = io(PYTHON_BACKEND_URL, { 
        transports: ['websocket'],
        rejectUnauthorized: false 
    });

    socket.on('connect', () => setIsConnectedToBackend(true));
    socket.on('disconnect', () => {
      setIsConnectedToBackend(false);
      setIsRobotConnected(false);
      setRobotStatusMessage('Robot: Disconnected');
      clearActiveDrawingState();
    });

    socket.on('robot_connection_status', (data: { success: boolean, message: string }) => {
      setIsRobotConnected(data.success);
      setRobotStatusMessage(`Robot: ${data.message}`);
    });

    socket.on('command_response', (data: { success: boolean, message: string, command_sent?: string }) => {
      setLastCommandResponse(`Cmd: ${data.command_sent || 'N/A'} -> Resp: ${data.message}`);
    });

    const handleImageUploadSuccess = (data: { success: boolean, message: string, original_filename?: string, filepath_on_server?: string}) => {
      if (data.success && data.filepath_on_server) {
        setLastUploadedImageInfo({ message: `Received: ${data.original_filename}. Ready.`, filepath: data.filepath_on_server });
        setQrCodeImage(null); setQrUploadUrl('');
        setSelectedFile(null); setImagePreviewUrl(null);
      } else {
        setLastUploadedImageInfo({ message: `Upload Info: ${data.message}`, filepath: null });
      }
    };
    socket.on('qr_image_received', handleImageUploadSuccess);
    socket.on('direct_image_upload_response', handleImageUploadSuccess);
    socket.on('qr_code_data', (data: { qr_image_base64?: string, upload_url?: string, error?: string }) => {
        if (data.error) { setQrUploadUrl(`Error: ${data.error}`); setQrCodeImage(null); }
        else if (data.qr_image_base64) {
            setQrCodeImage(`data:image/png;base64,${data.qr_image_base64}`);
            setQrUploadUrl(data.upload_url || 'N/A');
        }
    });

    socket.on('drawing_status_update', (data: { active: boolean, message: string, progress?: number, drawing_id?: string }) => {
      setIsDrawing(data.active);
      setDrawingStatusText(data.message);
      if (typeof data.progress === 'number') {
          setDrawingProgress(data.progress);
      }
    });

    socket.on('drawing_completed', (data: { drawing_id: string, message: string }) => {
        setIsDrawing(false);
        setDrawingProgress(100);
        setDrawingStatusText(data.message || 'Drawing complete!');
        setTimeout(clearActiveDrawingState, 3000);
    });
    
    socket.on('drawing_aborted', (data: { drawing_id: string, message: string }) => {
        clearActiveDrawingState();
        setDrawingStatusText(data.message || 'Drawing aborted.');
    });

    socket.on('drawing_history_updated', (history: DrawingHistoryItem[]) => {
        setDrawingHistory(history || []);
    });

    socket.on('transcription_result', (data: { text?: string, error?: string }) => {
        if (data.error) setInteractionStatus(`Transcription Error: ${data.error}`);
        else if (data.text) {
            setRawTranscribedText(data.text);
            setEditableCommandText(data.text);
            setLlmResponse('');
            setInteractionStatus('Edit command or send to Robotist.');
        }
    });

    socket.on('llm_response_chunk', (data: { chunk?: string, error?: string, done: boolean, final_message?: string }) => {
        if (data.error) { setLlmResponse(p => p + `\n[Error: ${data.error}]`); setInteractionStatus('LLM error.'); }
        else if (data.chunk) { setLlmResponse(p => p + data.chunk); if (!data.done) setInteractionStatus('Robotist is typing...'); }
        if (data.done) {
            if (data.final_message) setLlmResponse(data.final_message);
            setInteractionStatus('Ready for next command.');
        }
    });

    socket.on('threshold_preview_image_response', (data: { image_base64?: string, error?: string }) => {
        setIsPreviewLoading(false);
        if (data.error) {
            setThresholdPreviewImage(null);
            alert(`Error generating preview: ${data.error}`);
        } else if (data.image_base64) {
            setThresholdPreviewImage(`data:image/png;base64,${data.image_base64}`);
        }
    });

    return () => {
        socket.disconnect();
        if (audioStreamRef.current) {
            audioStreamRef.current.getTracks().forEach(track => track.stop());
        }
    };
  }, [clearActiveDrawingState]);


  // --- Event Handler Functions ---
  const handleConnectRobot = () => {
    if (isDrawing) { alert("Cannot change connection while drawing."); return; }
    if (socket) socket.emit('robot_connect_request', { use_real_robot: useRealRobot });
  }
  const handleDisconnectRobot = () => {
    if (isDrawing) { alert("Cannot change connection while drawing."); return; }
    if (socket) socket.emit('robot_disconnect_request', {});
  }

  const sendRobotMoveCommand = (type: 'go_home' | 'move_to_safe_center') => {
    if (isDrawing) { alert("Cannot move robot while drawing."); return; }
    if (socket) socket.emit('send_robot_command', { type });
  };
  
  const handleSendCustomCoordinates = () => {
    if (isDrawing || !isRobotConnected) { alert("Cannot move: drawing is active or robot is disconnected."); return; }
    const [x, y, z] = [parseFloat(xCoord), parseFloat(yCoord), parseFloat(zCoord)];
    if (isNaN(x) || isNaN(y) || isNaN(z)) { alert("Invalid coordinates."); return; }
    socket.emit('send_custom_coordinates', { x_py: x, z_py: y, y_py: z });
  };

  const requestQrCode = () => {
    if (isDrawing) { alert("Cannot request QR code while drawing."); return; }
    clearActiveDrawingState();
    setQrCodeImage(null); setQrUploadUrl('Requesting...');
    setLastUploadedImageInfo({ message: '', filepath: null });
    socket.emit('request_qr_code', {});
  };

  const processNewFile = (file: File | null) => {
    if (file && file.type.startsWith('image/')) {
        clearActiveDrawingState();
        setSelectedFile(file);
        setImagePreviewUrl(URL.createObjectURL(file));
        setQrCodeImage(null); setQrUploadUrl('');
        setLastUploadedImageInfo({ message: '', filepath: null });
    } else {
        setSelectedFile(null); setImagePreviewUrl(null);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => { processNewFile(e.target.files?.[0] || null); };
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => { e.preventDefault(); e.stopPropagation(); setIsDragging(false); processNewFile(e.dataTransfer.files?.[0] || null); };
  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => { e.preventDefault(); e.stopPropagation(); setIsDragging(true); };
  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => { e.preventDefault(); e.stopPropagation(); setIsDragging(false); };
  const triggerFileInput = () => { fileInputRef.current?.click(); };

  const sendSelectedFileToBackend = () => {
    if (!selectedFile || isDrawing) { alert("Select a file first or wait for drawing to finish."); return; }
    const reader = new FileReader();
    reader.onload = (e) => {
      const base64Data = (e.target?.result as string)?.split(',')[1];
      if (base64Data) {
        setLastUploadedImageInfo({ message: `Sending ${selectedFile.name}...`, filepath: null });
        socket.emit('direct_image_upload', { filename: selectedFile.name, fileData: base64Data });
      }
    };
    reader.readAsDataURL(selectedFile);
  };
  
  const handleProcessAndDraw = () => {
    if (isDrawing) { alert("A drawing is already in progress."); return; }
    if (!isRobotConnected) { alert("Please connect to the robot first."); return; }
    if (lastUploadedImageInfo.filepath) {
        setShowThresholdModal(true);
    } else {
        alert("No image has been successfully uploaded to the backend yet.");
    }
  };

  const confirmAndStartDrawingWithThresholds = () => {
    if (!lastUploadedImageInfo.filepath) { alert("Error: No image path."); return; }
    const selectedOpt = THRESHOLD_OPTIONS.find(opt => opt.key === selectedThresholdKey);
    if (!selectedOpt) { alert("Invalid threshold option."); return; }
    
    const originalFilename = lastUploadedImageInfo.message.includes("Received: ") ? lastUploadedImageInfo.message.split("Received: ")[1].split(".")[0] : "uploaded_image";

    setIsDrawing(true);
    setDrawingStatusText(`Processing image: ${originalFilename}...`);
    setDrawingProgress(0);

    // *** MODIFIED: Send pen_down_z value to backend ***
    socket.emit('process_image_for_drawing', {
        filepath: lastUploadedImageInfo.filepath,
        original_filename: originalFilename,
        canny_t1: selectedOpt.t1,
        canny_t2: selectedOpt.t2,
        pen_down_z: penDownZ
    });
    
    setShowThresholdModal(false);
  };
  
  const handleResumeDrawingFromHistory = (drawingId: string) => {
    if (isDrawing) { alert("Another drawing is already active."); return; }
    if (socket) {
        console.log(`Frontend: Emitting resume_drawing_request for ${drawingId}`);
        socket.emit('resume_drawing_request', { drawing_id: drawingId });
    }
  };
  
  const handleRestartDrawingFromHistory = (drawingId: string) => {
    if (isDrawing) { alert("Another drawing is already active."); return; }
    if (socket) {
        console.log(`Frontend: Emitting restart_drawing_request for ${drawingId}`);
        socket.emit('restart_drawing_request', { drawing_id: drawingId });
    }
  };
  
  const startRecording = async () => { 
    if (isDrawing || !isConnectedToBackend) {
      alert("Cannot record voice while drawing or disconnected.");
      return;
    }
    setRawTranscribedText('');
    setEditableCommandText('');
    setLlmResponse('');
    setInteractionStatus('Requesting mic permission...');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioStreamRef.current = stream;
      mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      audioChunks = [];
      mediaRecorder.ondataavailable = (event) => { audioChunks.push(event.data); };
      mediaRecorder.onstop = () => {
        setInteractionStatus('Transcribing audio...');
        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        const reader = new FileReader();
        reader.onloadend = () => {
          const base64Audio = (reader.result as string)?.split(',')[1];
          if (socket && base64Audio) {
            socket.emit('audio_chunk', { audioData: base64Audio, mimeType: 'audio/webm' });
          }
        };
        reader.readAsDataURL(audioBlob);
        audioStreamRef.current?.getTracks().forEach(track => track.stop());
      };
      mediaRecorder.start();
      setIsRecording(true);
      setInteractionStatus('Recording... Tap mic to stop.');
    } catch (err) {
      console.error("Mic error:", err);
      setInteractionStatus('Microphone access denied.');
    }
  };
  const stopRecording = () => { 
      if (mediaRecorder && isRecording) {
      mediaRecorder.stop();
      setIsRecording(false);
    }
  };
  const handleMicButtonClick = () => { isRecording ? stopRecording() : startRecording(); };
  
  const submitTextToLLM = (text: string) => { 
    if (!text.trim()) {
      alert("Command cannot be empty.");
      return;
    }
    if (socket && isConnectedToBackend) {
      socket.emit('submit_text_to_llm', { text_command: text });
      setLlmResponse(''); 
      setInteractionStatus('Robotist is thinking...');
    }
  };
  const handleSendEditableCommand = () => { submitTextToLLM(editableCommandText); };

  const requestThresholdPreview = useCallback((key: string) => {
    const selectedOpt = THRESHOLD_OPTIONS.find(opt => opt.key === key);
    if (selectedOpt && lastUploadedImageInfo.filepath && socket) {
        setIsPreviewLoading(true);
        setThresholdPreviewImage(null);
        socket.emit('request_threshold_preview', {
            filepath: lastUploadedImageInfo.filepath,
            t1: selectedOpt.t1,
            t2: selectedOpt.t2,
        });
    }
  }, [lastUploadedImageInfo.filepath]);

  useEffect(() => {
    if (showThresholdModal && selectedThresholdKey && lastUploadedImageInfo.filepath) {
        requestThresholdPreview(selectedThresholdKey);
    }
  }, [selectedThresholdKey, showThresholdModal, lastUploadedImageInfo.filepath, requestThresholdPreview]);


  const styles: { [key: string]: React.CSSProperties } = {
     appContainer: { maxWidth: '1400px', margin: '0 auto', padding: '20px', fontFamily: 'Arial, sans-serif', color: '#e0e0e0', backgroundColor: '#1e1e1e' },
    header: { textAlign: 'center' as const, marginBottom: '30px', borderBottom: '1px solid #444', paddingBottom: '20px' },
    mainTitle: { fontSize: '2.5em', color: '#61dafb', margin: '0 0 10px 0' },
    statusText: { fontSize: '0.9em', color: isConnectedToBackend ? '#76ff03' : '#ff5252' },
    mainLayoutContainer: { display: 'flex', flexDirection: 'column', gap: '25px' },
    topRowGrid: { display: 'grid', gridTemplateColumns: '1fr 2fr 1.5fr', gap: '25px', alignItems: 'start', marginBottom: '25px' },
    section: { backgroundColor: '#2a2a2a', padding: '20px', borderRadius: '8px', boxShadow: '0 4px 8px rgba(0,0,0,0.2)', display: 'flex', flexDirection: 'column', minHeight: '300px' },
    sectionTitle: { fontSize: '1.5em', color: '#61dafb', borderBottom: '1px solid #444', paddingBottom: '10px', marginBottom: '15px' },
    button: { backgroundColor: '#007bff', color: 'white', border: 'none', padding: '10px 15px', borderRadius: '5px', cursor: 'pointer', fontSize: '1em', margin: '5px', transition: 'background-color 0.2s ease' },
    buttonDisabled: { backgroundColor: '#555', cursor: 'not-allowed', opacity: 0.6 },
    micButton: { backgroundColor: isRecording ? '#dc3545' : '#007bff', width: '60px', height: '60px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center' },
    textarea: { width: 'calc(100% - 22px)', padding: '10px', marginBottom: '10px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: '#fff', minHeight: '60px' },
    imageUploadContainer: { display: 'flex', flexDirection: 'column', gap: '20px'},
    uploadBox: { border: '1px dashed #555', padding: '20px', borderRadius: '8px', textAlign: 'center' as const, backgroundColor: '#333', transition: 'background-color 0.2s, border-color 0.2s', flex: 1 },
    uploadBoxDragging: { borderColor: '#007bff', backgroundColor: '#3a3a3a' },
    imagePreview: { maxWidth: '100%', maxHeight: '150px', border: '1px solid #444', borderRadius: '4px', marginTop: '10px' },
    progressBarContainer: { width: '100%', backgroundColor: '#444', borderRadius: '4px', overflow: 'hidden', marginTop: '10px' },
    progressBar: { backgroundColor: '#61dafb', height: '20px', textAlign: 'center' as const, lineHeight: '20px', color: '#1e1e1e', transition: 'width 0.3s ease' },
    robotStatus: { padding: '10px', backgroundColor: '#333', borderRadius: '4px', fontSize: '0.9em', marginTop: '10px' },
    llmResponseBox: { marginTop: '15px', padding: '15px', border: '1px solid #444', borderRadius: '4px', backgroundColor: '#333', whiteSpace: 'pre-wrap' as const, maxHeight: '200px', overflowY: 'auto' as const, flexGrow: 1},
    coordInputContainer: { display: 'flex', flexDirection: 'column', gap: '10px', marginTop: 'auto' },
    coordInputGroup: { display: 'flex', alignItems: 'center', gap: '10px' },
    coordLabel: { minWidth: '100px', textAlign: 'right' as const, color: '#bbb' },
    coordInput: { flexGrow: 1, padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: '#fff' },
    checkboxContainer: { display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '10px 0', color: '#ccc' },
    checkboxInput: { marginRight: '8px', accentColor: '#61dafb' },
    modalOverlay: { position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 },
    modalContent: { backgroundColor: '#2a2a2a', padding: '30px', borderRadius: '8px', boxShadow: '0 5px 15px rgba(0,0,0,0.3)', width: 'auto', minWidth: '750px', maxWidth: '900px', color: '#e0e0e0' },
    modalTitle: { fontSize: '1.8em', color: '#61dafb', marginBottom: '20px', textAlign: 'center' as const },
    modalColumns: { display: 'flex', gap: '20px' },
    modalColumn: { flex: 1 },
    modalRadioGroup: { maxHeight: '550px', overflowY: 'auto', paddingRight: '10px' },
    modalRadioLabel: { display: 'block', marginBottom: '8px', cursor: 'pointer', padding: '8px', borderRadius: '4px', transition: 'background-color 0.2s' },
    modalRadioLabelSelected: { backgroundColor: '#007bff', color: 'white' },
    modalPreviewArea: { textAlign: 'center' as const, borderLeft: '1px solid #444', paddingLeft: '20px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' },
    modalPreviewImage: { maxWidth: '350px', maxHeight: '350px', border: '1px solid #555', borderRadius: '4px', backgroundColor: '#1e1e1e', minHeight: '250px' },
    modalActions: { marginTop: '25px', textAlign: 'right' as const },
    historySection: { marginTop: '25px' },
    historyList: { listStyle: 'none', padding: 0, maxHeight: '400px', overflowY: 'auto'},
    historyItem: { backgroundColor: '#333', padding: '15px', borderRadius: '6px', marginBottom: '10px', borderLeft: '5px solid #007bff' },
    historyItemCompleted: { borderLeftColor: '#28a745' },
    historyItemInterrupted: { borderLeftColor: '#ffc107' },
    historyItemInProgress: { borderLeftColor: '#17a2b8' },
    historyDetails: { fontSize: '0.9em', color: '#bbb', marginBottom: '8px' },
    historyActions: { marginTop: '10px' },
  };

  return (
    <div style={styles.appContainer}>
       <header style={styles.header}>
        <h1 style={styles.mainTitle}>CamTech Robotic Drawing Control</h1>
        <p style={styles.statusText}>Backend: {isConnectedToBackend ? 'Connected' : 'Disconnected'}</p>
      </header>

      <div style={styles.mainLayoutContainer}>
        <div style={styles.topRowGrid}>
          {/* Column 1: Robot Control */}
          <section style={styles.section}>
            <h2 style={styles.sectionTitle}>Robot Control</h2>
             <div style={styles.checkboxContainer}>
                <input type="checkbox" id="robotType" checked={useRealRobot} onChange={(e) => setUseRealRobot(e.target.checked)} disabled={isRobotConnected || isDrawing} style={styles.checkboxInput} />
                <label htmlFor="robotType">Use Real Robot</label>
             </div>
             <button onClick={handleConnectRobot} disabled={!isConnectedToBackend || isRobotConnected || isDrawing} style={{...styles.button, ...((!isConnectedToBackend || isRobotConnected || isDrawing) && styles.buttonDisabled)}}>Connect</button>
             <button onClick={handleDisconnectRobot} disabled={!isRobotConnected || isDrawing} style={{...styles.button, backgroundColor: '#dc3545', ...((!isRobotConnected || isDrawing) && styles.buttonDisabled)}}>Disconnect</button>
             <div style={styles.robotStatus}><p style={{margin: 0, color: isRobotConnected ? '#76ff03' : '#ffc107'}}>{robotStatusMessage}</p></div>
             
             <div style={{marginTop: '20px', textAlign: 'center'}}>
                <button onClick={() => sendRobotMoveCommand('go_home')} disabled={!isRobotConnected || isDrawing} style={{...styles.button, ...((!isRobotConnected || isDrawing) && styles.buttonDisabled)}}>Go Home</button>
                <button onClick={() => sendRobotMoveCommand('move_to_safe_center')} disabled={!isRobotConnected || isDrawing} style={{...styles.button, ...((!isRobotConnected || isDrawing) && styles.buttonDisabled)}}>Safe Center</button>
             </div>

             <div style={styles.coordInputContainer}>
                <h3 style={{fontSize: '1.1em', color: '#ccc', textAlign: 'center', marginBottom: '10px'}}>Drawing & Move Settings</h3>
                {/* *** NEW: Pen Down Depth Input *** */}
                <div style={styles.coordInputGroup}>
                    <label style={styles.coordLabel}>Pen Down Depth:</label>
                    <input type="number" value={penDownZ} onChange={e => setPenDownZ(parseFloat(e.target.value) || 0)} style={styles.coordInput} disabled={isDrawing} />
                </div>

                <h4 style={{fontSize: '1em', color: '#bbb', textAlign: 'center', marginTop: '15px', marginBottom: '5px'}}>Custom Move</h4>
                <div style={styles.coordInputGroup}><label style={styles.coordLabel}>X (paper):</label><input type="number" value={xCoord} onChange={e => setXCoord(e.target.value)} style={styles.coordInput} disabled={!isRobotConnected || isDrawing} /></div>
                <div style={styles.coordInputGroup}><label style={styles.coordLabel}>Depth (pen):</label><input type="number" value={yCoord} onChange={e => setYCoord(e.target.value)} style={styles.coordInput} disabled={!isRobotConnected || isDrawing} /></div>
                <div style={styles.coordInputGroup}><label style={styles.coordLabel}>Side (paper):</label><input type="number" value={zCoord} onChange={e => setZCoord(e.target.value)} style={styles.coordInput} disabled={!isRobotConnected || isDrawing} /></div>
                <button onClick={handleSendCustomCoordinates} disabled={!isRobotConnected || isDrawing} style={{...styles.button, marginTop:'10px', ...((!isRobotConnected || isDrawing) && styles.buttonDisabled)}}>Send Coords</button>
             </div>
             {lastCommandResponse && <p style={{fontSize: '0.8em', color: '#aaa', marginTop: '10px', textAlign: 'center'}}>Last Resp: {lastCommandResponse}</p>}
          </section>

          {/* Column 2: Voice/Text Interaction */}
            <section style={styles.section}>
                <h2 style={styles.sectionTitle}>Robotist Interaction</h2>
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: '15px' }}>
                    <button onClick={handleMicButtonClick} disabled={!isConnectedToBackend || isDrawing} style={{...styles.button, ...styles.micButton, ...((!isConnectedToBackend || isDrawing) && styles.buttonDisabled)}} title={isRecording ? "Stop Recording" : "Start Voice Command"}>
                        {isRecording ? <StopIcon /> : <MicIcon />}
                    </button>
                    <p style={{ margin: '0 0 0 15px', flexGrow: 1, color: '#bbbbbb' }}>{interactionStatus}</p>
                </div>
                {rawTranscribedText && <p style={{fontSize: '0.9em', color: '#aaa', fontStyle: 'italic'}}>You said: "{rawTranscribedText}"</p>}
                <textarea value={editableCommandText} onChange={(e) => setEditableCommandText(e.target.value)} placeholder="Type command or edit transcribed text..." style={styles.textarea} disabled={!isConnectedToBackend || isDrawing || isRecording} />
                <button onClick={handleSendEditableCommand} disabled={!editableCommandText.trim() || !isConnectedToBackend || isDrawing || isRecording} style={{...styles.button, ...((!editableCommandText.trim() || !isConnectedToBackend || isDrawing || isRecording) && styles.buttonDisabled)}}>
                    Send Command to Robotist
                </button>
                {llmResponse && (
                    <div style={styles.llmResponseBox}>
                        <p style={{ margin: 0 }}><b>Robotist:</b> {llmResponse}</p>
                    </div>
                )}
            </section>

          {/* Column 3: Image & Drawing */}
          <section style={styles.section}>
             <h2 style={styles.sectionTitle}>Image & Drawing</h2>
             <div style={styles.imageUploadContainer}>
                <div style={styles.uploadBox}>
                  <h3>QR Code Upload</h3>
                  <button onClick={requestQrCode} disabled={isDrawing} style={{...styles.button, ...(isDrawing && styles.buttonDisabled)}}>Get QR</button>
                  {qrCodeImage && <img src={qrCodeImage} alt="QR Code" style={{...styles.imagePreview, marginTop: '10px'}}/>}
                  {qrUploadUrl && !qrCodeImage && <p style={{fontSize: '0.8em', color: '#aaa', wordBreak: 'break-all'}}>{qrUploadUrl}</p>}
                </div>
                <div onDrop={handleDrop} onDragOver={handleDragOver} onDragLeave={handleDragLeave} style={{...styles.uploadBox, ...(isDragging && styles.uploadBoxDragging)}}>
                    <h3>Desktop Upload</h3>
                    <input type="file" accept="image/*" onChange={handleFileSelect} ref={fileInputRef} style={{display: 'none'}} />
                    <button onClick={triggerFileInput} disabled={isDrawing} style={{...styles.button, ...(isDrawing && styles.buttonDisabled)}}>Choose Image</button>
                    <p style={{fontSize:'0.9em', color: '#aaa'}}>or Drag & Drop</p>
                    {imagePreviewUrl && <img src={imagePreviewUrl} alt="Preview" style={styles.imagePreview}/>}
                    {selectedFile && <button onClick={sendSelectedFileToBackend} style={{...styles.button, marginTop: '5px'}}>Upload This Image</button>}
                </div>
             </div>
             <p style={{textAlign: 'center', color: '#76ff03', marginTop: '15px', fontWeight: 'bold'}}>{lastUploadedImageInfo.message}</p>
             {lastUploadedImageInfo.filepath && <button onClick={handleProcessAndDraw} disabled={isDrawing || !isRobotConnected} style={{...styles.button, backgroundColor: '#28a745', margin: '10px auto', display: 'block', padding: '12px 25px', fontSize: '1.1em', ...((isDrawing || !isRobotConnected) && styles.buttonDisabled)}}>Process & Draw</button>}
             
             {/* Drawing Status */}
             <div style={{marginTop: 'auto', paddingTop: '15px', textAlign: 'center'}}>
                <p style={{fontWeight: 'bold', color: isDrawing ? '#61dafb' : '#ccc'}}>{drawingStatusText}</p>
                {(isDrawing || drawingProgress > 0) && (
                  <div style={styles.progressBarContainer}>
                    <div style={{...styles.progressBar, width: `${drawingProgress}%`}}>{drawingProgress.toFixed(0)}%</div>
                  </div>
                )}
             </div>
          </section>
        </div>
        
        {/* Drawing History Section */}
        {drawingHistory.length > 0 && (
            <section style={{...styles.section, ...styles.historySection}}>
                <h2 style={styles.sectionTitle}>Drawing History</h2>
                <ul style={styles.historyList}>
                    {drawingHistory.map(item => (
                        <li
                            key={item.drawing_id}
                            style={{
                                ...styles.historyItem,
                                ...(item.status === 'completed' ? styles.historyItemCompleted : {}),
                                ...(item.status.includes('interrupted') ? styles.historyItemInterrupted : {}),
                                ...(item.status.startsWith('in_progress') ? styles.historyItemInProgress : {}),
                            }}
                        >
                            <p style={{margin: '0 0 5px 0', fontWeight: 'bold', color: '#f0f0f0'}}>{item.original_filename}</p>
                            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
                                <div>
                                    <p style={styles.historyDetails}>Status: {item.status.replace(/_/g, ' ')}</p>
                                    <p style={styles.historyDetails}>Last Update: {new Date(item.last_updated).toLocaleString()}</p>
                                    {item.progress !== undefined && item.status.includes('interrupted') && <div style={{width: '100px', backgroundColor: '#555'}}><div style={{width: `${item.progress}%`, backgroundColor: '#ffc107', height: '5px'}}></div></div>}
                                </div>
                                <div style={styles.historyActions}>
                                    {(item.status.includes('interrupted')) && (
                                        <button
                                            onClick={() => handleResumeDrawingFromHistory(item.drawing_id)}
                                            style={{...styles.button, backgroundColor: '#ffc107', color: '#1e1e1e'}}
                                            disabled={isDrawing || !isRobotConnected}
                                        >
                                            Resume
                                        </button>
                                    )}
                                    <button
                                        onClick={() => handleRestartDrawingFromHistory(item.drawing_id)}
                                        style={{...styles.button, backgroundColor: '#17a2b8'}}
                                        disabled={isDrawing || !isRobotConnected}
                                    >
                                        Restart
                                    </button>
                                </div>
                            </div>
                        </li>
                    ))}
                </ul>
            </section>
        )}
      </div>

       {showThresholdModal && (
        <div style={styles.modalOverlay}>
          <div style={styles.modalContent}>
            <h3 style={styles.modalTitle}>Select Drawing Details & Preview</h3>
            <div style={styles.modalColumns}>
                <div style={{...styles.modalColumn, ...styles.modalRadioGroup}}>
                    {THRESHOLD_OPTIONS.map(option => (
                        <label key={option.key} htmlFor={option.key} style={{...styles.modalRadioLabel, ...(selectedThresholdKey === option.key ? styles.modalRadioLabelSelected : {})}}>
                            <input type="radio" id={option.key} name="threshold" value={option.key} checked={selectedThresholdKey === option.key} onChange={() => setSelectedThresholdKey(option.key)} style={{marginRight: '10px'}}/>
                            {option.label} (T1: {option.t1}, T2: {option.t2})
                        </label>
                    ))}
                </div>
                <div style={{...styles.modalColumn, ...styles.modalPreviewArea}}>
                    <h4>Preview</h4>
                    {isPreviewLoading && <p>Loading...</p>}
                    {!isPreviewLoading && thresholdPreviewImage && <img src={thresholdPreviewImage} alt="Preview" style={styles.modalPreviewImage} />}
                    {!isPreviewLoading && !thresholdPreviewImage && <div style={{...styles.modalPreviewImage, display: 'flex', alignItems: 'center', justifyContent: 'center'}}><span>No Preview</span></div>}
                </div>
            </div>
            <div style={styles.modalActions}>
              <button onClick={() => setShowThresholdModal(false)} style={{...styles.button, backgroundColor: '#6c757d'}}>Cancel</button>
              <button onClick={confirmAndStartDrawingWithThresholds} style={{...styles.button, backgroundColor: '#28a745'}} disabled={isPreviewLoading}>Start Drawing</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
