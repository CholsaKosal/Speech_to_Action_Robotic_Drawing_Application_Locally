// frontend/s2a-drawing-ui/src/App.tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import './App.css'; // Ensure this file exists, even if minimal

const PYTHON_BACKEND_URL = 'http://localhost:5555';

let socket: Socket;
let mediaRecorder: MediaRecorder | null = null;
let audioChunks: Blob[] = [];

// Simple Icon Components
const MicIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16">
    <path d="M5 3a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0V3z"/>
    <path d="M3.5 6.5A.5.5 0 0 1 4 7v1a4 4 0 0 0 8 0V7a.5.5 0 0 1 1 0v1a5 5 0 0 1-4.5 4.975V15h3a.5.5 0 0 1 0 1h-7a.5.5 0 0 1 0-1h3v-2.025A5 5 0 0 1 3 8V7a.5.5 0 0 1 .5-.5z"/>
  </svg>
);

const StopIcon = () => (
 <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16">
    <path d="M5 3.5h6A1.5 1.5 0 0 1 12.5 5v6a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 11V5A1.5 1.5 0 0 1 5 3.5z"/>
  </svg>
);

interface ThresholdOption {
  key: string;
  label: string;
  t1: number;
  t2: number;
}

const THRESHOLD_OPTIONS: ThresholdOption[] = Array.from({ length: 10 }, (_, i) => ({
  key: `opt${i + 1}`,
  label: `Style ${i + 1}`,
  t1: (i + 1) * 10 + 20, 
  t2: (i + 1) * 20 + 40, 
}));

interface DrawingHistoryItem {
    drawing_id: string;
    original_filename: string;
    status: string; // e.g., "completed", "interrupted", "in_progress", "aborted_manual_override"
    progress: number; // Percentage
    last_updated: string; // ISO date string
    // The backend might send more, but these are key for UI
    robot_commands_tuples?: any[]; // For potential client-side restart logic if needed, or just for info
    current_command_index?: number;
    total_commands?: number;
}


function App() {
  const [isConnectedToBackend, setIsConnectedToBackend] = useState(false);
  const [isRobotConnected, setIsRobotConnected] = useState(false);
  const [robotStatusMessage, setRobotStatusMessage] = useState('Robot: Not connected');
  const [lastCommandResponse, setLastCommandResponse] = useState('');

  const [qrCodeImage, setQrCodeImage] = useState<string | null>(null);
  const [qrUploadUrl, setQrUploadUrl] = useState<string>('');
  
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [lastUploadedImageInfo, setLastUploadedImageInfo] = useState<string>('');
  const [uploadedFilePathFromBackend, setUploadedFilePathFromBackend] = useState<string | null>(null);

  const [isDrawingActive, setIsDrawingActive] = useState(false); // Tracks if _execute_drawing_commands is running
  const [activeDrawingId, setActiveDrawingId] = useState<string | null>(null); // Tracks the ID of the drawing being executed
  const [drawingProgressMessage, setDrawingProgressMessage] = useState('');
  const [drawingProgressPercent, setDrawingProgressPercent] = useState(0);
  
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

  const clearActiveDrawingState = () => {
    setIsDrawingActive(false);
    setActiveDrawingId(null);
    setDrawingProgressMessage('Idle');
    setDrawingProgressPercent(0);
  };

  useEffect(() => {
    socket = io(PYTHON_BACKEND_URL, { transports: ['websocket'] });

    socket.on('connect', () => {
      console.log('Frontend: Connected to Python backend via Socket.IO!');
      setIsConnectedToBackend(true);
      setInteractionStatus('Tap mic or type command.');
    });

    socket.on('disconnect', () => {
      console.log('Frontend: Disconnected from Python backend.');
      setIsConnectedToBackend(false);
      setIsRobotConnected(false);
      setRobotStatusMessage('Robot: Disconnected (backend offline)');
      // Do not clear drawing active flags on simple disconnect, backend might still be processing or resumable
      setInteractionStatus('Backend offline. Please refresh or check server.');
      setShowThresholdModal(false); 
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

    socket.on('drawing_status_update', (data: { 
        active: boolean, 
        message: string, 
        progress?: number, 
        resumable?: boolean, // This indicates if the specific drawing ID is resumable
        drawing_id?: string,
        original_filename?: string 
      }) => {
      
      setDrawingProgressMessage(data.message);
      if (data.progress !== undefined) {
        setDrawingProgressPercent(data.progress);
      }

      if (data.active) {
        setIsDrawingActive(true);
        if(data.drawing_id) setActiveDrawingId(data.drawing_id);
      } else {
        // If drawing is no longer active for this specific ID, or in general
        if (activeDrawingId === data.drawing_id || !data.drawing_id) {
            clearActiveDrawingState();
        }
      }
    });

    socket.on('drawing_history_updated', (history: DrawingHistoryItem[]) => {
        console.log("Received drawing_history_updated:", history);
        setDrawingHistory(history || []);
    });


    socket.on('transcription_result', (data: { text?: string, error?: string }) => {
        if (data.error) {
            setInteractionStatus(`Transcription Error: ${data.error}`);
            setRawTranscribedText('');
            setEditableCommandText('');
            setLlmResponse('');
        } else if (data.text) {
            setRawTranscribedText(data.text); 
            setEditableCommandText(data.text); 
            setLlmResponse(''); 
            setInteractionStatus('Edit command below or send to Robotist.');
        }
    });
    
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
            } else if (!data.final_message && !data.chunk && llmResponse === "") { 
                 setInteractionStatus('Robotist finished.');
            }
        }
    });

    socket.on('threshold_preview_image_response', (data: { image_base64?: string, error?: string }) => {
        setIsPreviewLoading(false);
        if (data.error) {
            console.error("Error getting threshold preview:", data.error);
            setThresholdPreviewImage(null);
            alert(`Error generating preview: ${data.error}`);
        } else if (data.image_base64) {
            setThresholdPreviewImage(`data:image/png;base64,${data.image_base64}`);
        }
    });

    return () => { 
        if (socket) socket.disconnect(); 
        if (audioStreamRef.current) {
            audioStreamRef.current.getTracks().forEach(track => track.stop());
        }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); 

  const requestThresholdPreview = useCallback((key: string) => {
    const selectedOpt = THRESHOLD_OPTIONS.find(opt => opt.key === key);
    if (selectedOpt && uploadedFilePathFromBackend && socket) {
        setIsPreviewLoading(true);
        setThresholdPreviewImage(null); 
        console.log(`Requesting preview for T1=${selectedOpt.t1}, T2=${selectedOpt.t2}`);
        socket.emit('request_threshold_preview', {
            filepath: uploadedFilePathFromBackend,
            t1: selectedOpt.t1,
            t2: selectedOpt.t2,
        });
    }
  }, [uploadedFilePathFromBackend]);

  useEffect(() => {
    if (showThresholdModal && selectedThresholdKey && uploadedFilePathFromBackend) {
        requestThresholdPreview(selectedThresholdKey);
    }
  }, [selectedThresholdKey, showThresholdModal, uploadedFilePathFromBackend, requestThresholdPreview]); 


  const handleConnectRobot = () => { if (!isDrawingActive && socket) socket.emit('robot_connect_request', {}); }
  const handleDisconnectRobot = () => { if (!isDrawingActive && socket) socket.emit('robot_disconnect_request', {}); }
  const sendGoHomeCommand = () => { 
    if (!isDrawingActive && socket) {
      socket.emit('send_robot_command', { type: 'go_home' }); 
    }
  }
  const sendSafeCenterCommand = () => { 
    if (!isDrawingActive && socket) {
      socket.emit('send_robot_command', { type: 'move_to_safe_center' }); 
    }
  }
  
  const requestQrCode = () => {
    if (socket && isConnectedToBackend && !isDrawingActive) {
      setQrCodeImage(null); setQrUploadUrl('Requesting QR Code...');
      setSelectedFile(null); setImagePreviewUrl(null); 
      setLastUploadedImageInfo(''); setUploadedFilePathFromBackend(null);
      socket.emit('request_qr_code', {});
    } else if (isDrawingActive) { alert("Cannot request QR code while drawing is in progress."); }
  };

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

  const handleProcessAndDrawUploadedImage = () => {
    if (isDrawingActive) { alert("A drawing is already in progress."); return; }
    if (!isRobotConnected) { alert("Please connect to the robot first."); setLastCommandResponse("Error: Robot not connected."); return; }
    if (uploadedFilePathFromBackend) {
      setSelectedThresholdKey(THRESHOLD_OPTIONS[2].key); 
      setThresholdPreviewImage(null); 
      setShowThresholdModal(true); 
    } else { 
      alert("No image has been successfully uploaded to the backend yet."); 
      setLastCommandResponse("Error: No backend image path available.");
    }
  };

  const confirmAndStartDrawingWithThresholds = () => {
    if (!uploadedFilePathFromBackend) {
        alert("Error: No image path available for drawing.");
        setShowThresholdModal(false);
        return;
    }
    const selectedOpt = THRESHOLD_OPTIONS.find(opt => opt.key === selectedThresholdKey);
    if (!selectedOpt) {
        alert("Invalid threshold option selected.");
        return;
    }
    const originalFilename = lastUploadedImageInfo.includes("Received: ") ? lastUploadedImageInfo.split("Received: ")[1].split(". Ready")[0] : "uploaded_image";
    socket.emit('process_image_for_drawing', { 
        filepath: uploadedFilePathFromBackend, 
        original_filename: originalFilename,
        canny_t1: selectedOpt.t1,
        canny_t2: selectedOpt.t2
    });
    setLastCommandResponse(`Sent request to process & draw: ${originalFilename} with T1=${selectedOpt.t1}, T2=${selectedOpt.t2}`);
    setDrawingProgressMessage("Requesting image processing and drawing..."); 
    setDrawingProgressPercent(0);
    setShowThresholdModal(false); 
  };

  const handleResumeDrawingFromHistory = (drawingId: string) => {
    if (isDrawingActive) {
        alert("Another drawing is already active. Cannot resume now.");
        return;
    }
    if (socket && isConnectedToBackend) {
        console.log(`Frontend: Emitting 'resume_drawing_request' for ID: ${drawingId}`);
        socket.emit('resume_drawing_request', { drawing_id: drawingId });
    } else {
        alert("Cannot resume. Backend not connected.");
    }
  };

  const handleRestartDrawingFromHistory = (drawingId: string) => {
    if (isDrawingActive) {
        alert("Another drawing is already active. Cannot restart now.");
        return;
    }
    if (socket && isConnectedToBackend) {
        console.log(`Frontend: Emitting 'restart_drawing_request' for ID: ${drawingId}`);
        socket.emit('restart_drawing_request', { drawing_id: drawingId });
    } else {
        alert("Cannot restart. Backend not connected.");
    }
  };


  const startRecording = async () => {
    if (isDrawingActive || !isConnectedToBackend) {
        alert("Cannot record voice while drawing is active or backend is disconnected.");
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
            setInteractionStatus('Sending audio for transcription...');
            const audioBlob = new Blob(audioChunks, { type: mediaRecorder?.mimeType });
            const reader = new FileReader();
            reader.onloadend = () => {
                const base64Audio = (reader.result as string)?.split(',')[1];
                if (socket && base64Audio) {
                    socket.emit('audio_chunk', { audioData: base64Audio, mimeType: mediaRecorder?.mimeType });
                } else { setInteractionStatus("Error: Could not send audio data."); }
            };
            reader.onerror = () => { setInteractionStatus("Error reading audio blob."); };
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
    }
  };

  const handleMicButtonClick = () => {
    if (isRecording) stopRecording();
    else startRecording();
  };

  const submitTextToLLM = (text: string) => {
    if (!text.trim()) {
      alert("Command text cannot be empty.");
      setInteractionStatus('Command empty. Tap mic or type command.');
      return;
    }
    if (socket && isConnectedToBackend && socket.connected) { 
      socket.emit('submit_text_to_llm', { text_command: text });
      setLlmResponse(''); 
      setInteractionStatus('Robotist is thinking...');
      if (text !== rawTranscribedText) setRawTranscribedText('');
    } else {
      alert("Cannot send command. Backend not connected or socket issue.");
      setInteractionStatus('Backend disconnected or socket issue.');
    }
  };

  const handleSendEditableCommand = () => { submitTextToLLM(editableCommandText); };

  const handleSendCustomCoordinates = () => {
    if (!isRobotConnected) {
        alert("Robot not connected.");
        return;
    }
    if (isDrawingActive) {
        alert("Cannot send coordinates while drawing is active.");
        return;
    }
    const x = parseFloat(xCoord);
    const y = parseFloat(yCoord); 
    const z = parseFloat(zCoord); 

    if (isNaN(x) || isNaN(y) || isNaN(z)) {
        alert("Invalid coordinates. Please enter numbers for X, Y (depth), and Z (side-to-side).");
        return;
    }
    if (socket) {
        socket.emit('send_custom_coordinates', { x_py: x, z_py: y, y_py: z });
        setLastCommandResponse(`Sent custom coords: X=${x}, Depth=${y}, Side=${z}`);
    }
  };

  const styles: { [key: string]: React.CSSProperties } = {
    appContainer: { maxWidth: '1400px', margin: '0 auto', padding: '20px', fontFamily: 'Arial, sans-serif', color: '#e0e0e0', backgroundColor: '#1e1e1e' }, // Wider for history
    header: { textAlign: 'center' as const, marginBottom: '30px', borderBottom: '1px solid #444', paddingBottom: '20px' },
    mainTitle: { fontSize: '2.5em', color: '#61dafb', margin: '0 0 10px 0' },
    statusText: { fontSize: '0.9em', color: isConnectedToBackend ? '#76ff03' : '#ff5252' },
    mainLayoutContainer: { display: 'flex', flexDirection: 'column', gap: '25px' }, // Main sections stack vertically
    topRowGrid: { display: 'grid', gridTemplateColumns: '1fr 2fr 1.5fr', gap: '25px', alignItems: 'start', marginBottom: '25px' }, 
    section: { backgroundColor: '#2a2a2a', padding: '20px', borderRadius: '8px', boxShadow: '0 4px 8px rgba(0,0,0,0.2)', display: 'flex', flexDirection: 'column', minHeight: '300px' }, 
    sectionTitle: { fontSize: '1.5em', color: '#61dafb', borderBottom: '1px solid #444', paddingBottom: '10px', marginBottom: '15px' },
    button: { backgroundColor: '#007bff', color: 'white', border: 'none', padding: '10px 15px', borderRadius: '5px', cursor: 'pointer', fontSize: '1em', margin: '5px', transition: 'background-color 0.2s ease' },
    buttonDisabled: { backgroundColor: '#555', cursor: 'not-allowed' },
    micButton: { backgroundColor: isRecording ? '#dc3545' : '#007bff', width: '60px', height: '60px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center' },
    textarea: { width: 'calc(100% - 22px)', padding: '10px', marginBottom: '10px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: '#fff', minHeight: '60px' },
    imageUploadContainer: { display: 'flex', flexDirection: 'column', gap: '20px'}, 
    uploadBox: { border: '1px dashed #555', padding: '20px', borderRadius: '8px', textAlign: 'center' as const, backgroundColor: '#333', transition: 'background-color 0.2s, border-color 0.2s', flex: 1 },
    uploadBoxDragging: { borderColor: '#007bff', backgroundColor: '#3a3a3a' },
    imagePreview: { maxWidth: '100%', maxHeight: '150px', border: '1px solid #444', borderRadius: '4px', marginTop: '10px' },
    progressBarContainer: { width: '100%', backgroundColor: '#444', borderRadius: '4px', overflow: 'hidden', marginTop: '10px' },
    progressBar: { width: `${drawingProgressPercent}%`, backgroundColor: '#61dafb', height: '20px', textAlign: 'center' as const, lineHeight: '20px', color: '#1e1e1e', transition: 'width 0.3s ease' },
    robotStatus: { padding: '10px', backgroundColor: '#333', borderRadius: '4px', fontSize: '0.9em', marginTop: '10px' },
    llmResponseBox: { marginTop: '15px', padding: '15px', border: '1px solid #444', borderRadius: '4px', backgroundColor: '#333', whiteSpace: 'pre-wrap' as const, maxHeight: '200px', overflowY: 'auto' as const, flexGrow: 1},
    coordInputContainer: { display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '15px', marginBottom: '15px' },
    coordInputGroup: { display: 'flex', alignItems: 'center', gap: '10px' },
    coordLabel: { minWidth: '70px', textAlign: 'right' as const, color: '#bbb' },
    coordInput: { flexGrow: 1, padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: '#fff' },
    modalOverlay: { position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 },
    modalContent: { backgroundColor: '#2a2a2a', padding: '30px', borderRadius: '8px', boxShadow: '0 5px 15px rgba(0,0,0,0.3)', width: 'auto', minWidth: '750px', maxWidth: '900px', color: '#e0e0e0' }, // Increased width
    modalTitle: { fontSize: '1.8em', color: '#61dafb', marginBottom: '20px', textAlign: 'center' as const },
    modalColumns: { display: 'flex', gap: '20px' },
    modalColumn: { flex: 1 },
    modalRadioGroup: { maxHeight: '550px', overflowY: 'auto', paddingRight: '10px' }, // Increased height
    modalRadioLabel: { display: 'block', marginBottom: '8px', cursor: 'pointer', padding: '8px', borderRadius: '4px', transition: 'background-color 0.2s' },
    modalRadioLabelSelected: { backgroundColor: '#007bff', color: 'white' },
    modalPreviewArea: { textAlign: 'center' as const, borderLeft: '1px solid #444', paddingLeft: '20px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' },
    modalPreviewImage: { maxWidth: '350px', maxHeight: '350px', border: '1px solid #555', borderRadius: '4px', backgroundColor: '#1e1e1e', minHeight: '250px' }, // Increased preview image size
    modalActions: { marginTop: '25px', textAlign: 'right' as const },
    historySection: { /* Uses styles.section */ },
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
            <div style={{textAlign: 'center'}}>
              <button onClick={handleConnectRobot} disabled={!isConnectedToBackend || isRobotConnected || isDrawingActive || isRecording} style={{...styles.button, ...((!isConnectedToBackend || isRobotConnected || isDrawingActive || isRecording) && styles.buttonDisabled)}}> Connect to Robot </button>
              <button onClick={handleDisconnectRobot} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive || isRecording} style={{...styles.button, backgroundColor: '#ffc107', color: '#1e1e1e', ...((!isConnectedToBackend || !isRobotConnected || isDrawingActive || isRecording) && styles.buttonDisabled)}}> Disconnect Robot</button>
              <br />
              <button onClick={sendGoHomeCommand} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive || isRecording} style={{...styles.button, marginTop: '10px', ...((!isConnectedToBackend || !isRobotConnected || isDrawingActive || isRecording) && styles.buttonDisabled)}}> Send Robot to Home </button>
              <button onClick={sendSafeCenterCommand} disabled={!isConnectedToBackend || !isRobotConnected || isDrawingActive || isRecording} style={{...styles.button, marginTop: '10px', ...((!isConnectedToBackend || !isRobotConnected || isDrawingActive || isRecording) && styles.buttonDisabled)}}> Send to Safe Center </button>
            </div>

            <div style={styles.coordInputContainer}>
              <h3 style={{fontSize: '1.2em', color: '#ccc', marginBottom: '10px', textAlign: 'center'}}>Move to Specific Position:</h3>
              <div style={styles.coordInputGroup}>
                <label htmlFor="x-coord" style={styles.coordLabel}>X (mm):</label>
                <input type="number" id="x-coord" value={xCoord} onChange={(e) => setXCoord(e.target.value)} placeholder="e.g., 100" style={styles.coordInput} disabled={!isRobotConnected || isDrawingActive} />
              </div>
              <div style={styles.coordInputGroup}>
                <label htmlFor="y-coord" style={styles.coordLabel}>Y/Depth (mm):</label>
                <input type="number" id="y-coord" value={yCoord} onChange={(e) => setYCoord(e.target.value)} placeholder="e.g., -150" style={styles.coordInput} disabled={!isRobotConnected || isDrawingActive} />
              </div>
              <div style={styles.coordInputGroup}>
                <label htmlFor="z-coord" style={styles.coordLabel}>Z/Side (mm):</label>
                <input type="number" id="z-coord" value={zCoord} onChange={(e) => setZCoord(e.target.value)} placeholder="e.g., 50" style={styles.coordInput} disabled={!isRobotConnected || isDrawingActive} />
              </div>
              <button onClick={handleSendCustomCoordinates} disabled={!isRobotConnected || isDrawingActive || !xCoord || !yCoord || !zCoord} style={{...styles.button, marginTop: '10px', backgroundColor: '#17a2b8', ...((!isRobotConnected || isDrawingActive || !xCoord || !yCoord || !zCoord) && styles.buttonDisabled)}}>
                Send Custom Coordinates
              </button>
            </div>

            <div style={styles.robotStatus}>
                <p style={{margin: 0, color: isRobotConnected ? '#76ff03' : '#ffc107'}}>{robotStatusMessage}</p>
            </div>
            {lastCommandResponse && <p style={{fontSize: '0.9em', color: '#aaa', marginTop: '10px', textAlign: 'center'}}>Last Command: {lastCommandResponse}</p>}
          </section>

          {/* Column 2: Robotist Interaction */}
          <section style={styles.section}>
            <h2 style={styles.sectionTitle}>Robotist Interaction</h2>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: '15px' }}>
              <button 
                  onClick={handleMicButtonClick} 
                  disabled={!isConnectedToBackend || isDrawingActive}
                  style={{...styles.button, ...styles.micButton, ...( (!isConnectedToBackend || isDrawingActive) && styles.buttonDisabled) }}
                  title={isRecording ? "Stop Recording" : "Start Voice Command"}
              >
                  {isRecording ? <StopIcon /> : <MicIcon />}
              </button>
              <p style={{ margin: '0 0 0 15px', flexGrow: 1, color: '#bbbbbb' }}>{interactionStatus}</p>
            </div>

            {rawTranscribedText && <p style={{fontSize: '0.9em', color: '#aaa', fontStyle: 'italic', marginBottom: '10px'}}>You said: "{rawTranscribedText}"</p>}
            
            <textarea 
                value={editableCommandText}
                onChange={(e) => setEditableCommandText(e.target.value)}
                placeholder="Type command or edit transcribed text here..."
                style={styles.textarea}
                disabled={!isConnectedToBackend || isDrawingActive || isRecording}
            />
            <button 
                onClick={handleSendEditableCommand} 
                disabled={!editableCommandText.trim() || !isConnectedToBackend || isDrawingActive || isRecording}
                style={{...styles.button, ...( (!editableCommandText.trim() || !isConnectedToBackend || isDrawingActive || isRecording) && styles.buttonDisabled) }}
            >
                Send Command to Robotist
            </button>

            {llmResponse && (
              <div style={styles.llmResponseBox}>
                <p style={{ margin: 0 }}><b>Robotist:</b> {llmResponse}</p>
              </div>
            )}
          </section>
          
          {/* Column 3: Image Input */}
          <section style={styles.section}>
            <h2 style={styles.sectionTitle}>Image Input</h2>
            <div style={styles.imageUploadContainer}> 
              <div style={styles.uploadBox}>
                <h3>Upload via QR Code</h3>
                <button onClick={requestQrCode} disabled={!isConnectedToBackend || isDrawingActive || isRecording} style={{...styles.button, ...((!isConnectedToBackend || isDrawingActive || isRecording) && styles.buttonDisabled)}}>
                  Get QR Code
                </button>
                {qrUploadUrl && !qrCodeImage && <p style={{fontSize: '0.8em', wordBreak: 'break-all', color: '#aaa'}}><small>{qrUploadUrl}</small></p>}
                {qrCodeImage && ( <div> <p style={{fontSize: '0.8em', color: '#aaa'}}><small>Scan to upload. URL: {qrUploadUrl}</small></p> <img src={qrCodeImage} alt="QR Code for Upload" style={styles.imagePreview} /> </div> )}
              </div>
              <div 
                style={{...styles.uploadBox, ...(isDragging && styles.uploadBoxDragging), opacity: (isDrawingActive || isRecording) ? 0.6 : 1, pointerEvents: (isDrawingActive || isRecording) ? 'none' : 'auto' }}
                onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop} 
              >
                <h3>Upload from Desktop</h3>
                <input type="file" accept="image/*" onChange={handleFileSelect} ref={fileInputRef} style={{ display: 'none' }} disabled={isDrawingActive || isRecording} />
                <button onClick={triggerFileInput} disabled={isDrawingActive || isRecording} style={{...styles.button, ...((isDrawingActive || isRecording) && styles.buttonDisabled)}}> Choose Image File </button>
                <p style={{fontSize: '0.9em', marginTop: '10px', color: '#aaa'}}>Or drag & drop image here</p>
                {imagePreviewUrl && selectedFile && ( <div style={{marginTop: '15px'}}> <p style={{color: '#bbb'}}>Preview:</p> <img src={imagePreviewUrl} alt="Selected preview" style={styles.imagePreview}/> <p style={{fontSize: '0.8em', color: '#aaa'}}>{selectedFile.name}</p> <button onClick={sendSelectedFileToBackend} disabled={!selectedFile || isDrawingActive || !isConnectedToBackend || isRecording} style={{...styles.button, marginTop: '10px', ...((!selectedFile || isDrawingActive || !isConnectedToBackend || isRecording) && styles.buttonDisabled)}} > Upload This Image </button> </div> )}
              </div>
            </div>
            {lastUploadedImageInfo && <p style={{color: lastUploadedImageInfo.startsWith("Received:") ? "#76ff03" : (lastUploadedImageInfo.startsWith("Error") ? "#ff5252" : "#ffc107"), fontWeight: 'bold', textAlign: 'center', marginTop: '15px'}}>{lastUploadedImageInfo}</p>}
            {uploadedFilePathFromBackend && ( <button onClick={handleProcessAndDrawUploadedImage} disabled={isDrawingActive || !isRobotConnected || !isConnectedToBackend || isRecording} style={{...styles.button, backgroundColor: '#28a745', display: 'block', margin: '20px auto', padding: '12px 25px', fontSize: '1.1em', ...((isDrawingActive || !isRobotConnected || !isConnectedToBackend || isRecording) && styles.buttonDisabled)}} > Process & Draw Uploaded Image </button> )}
            
            {isDrawingActive && activeDrawingId && (
              <div style={{marginTop: '20px'}}>
                <p style={{color: "#61dafb", fontWeight: "bold", textAlign: 'center'}}>{drawingProgressMessage}</p>
                <div style={styles.progressBarContainer}>
                  <div style={styles.progressBar}>{drawingProgressPercent.toFixed(0)}%</div>
                </div>
              </div>
            )}
            {!isDrawingActive && drawingProgressMessage && !lastUploadedImageInfo.startsWith("Received:") && <p style={{textAlign: 'center', marginTop: '15px', color: '#aaa'}}>{drawingProgressMessage}</p>}
          </section>
        </div>

        {/* Drawing History Section */}
        {drawingHistory.length > 0 && (
            <section style={{...styles.section, ...styles.historySection, marginTop: '25px'}}>
                <h2 style={styles.sectionTitle}>Drawing History (Last {drawingHistory.length})</h2>
                <ul style={styles.historyList}>
                    {drawingHistory.map(item => (
                        <li 
                            key={item.drawing_id} 
                            style={{
                                ...styles.historyItem, 
                                ...(item.status === 'completed' ? styles.historyItemCompleted : {}),
                                ...(item.status === 'interrupted' || item.status === 'interrupted_error' ? styles.historyItemInterrupted : {}),
                                ...(item.status && item.status.startsWith('in_progress') ? styles.historyItemInProgress : {}),
                            }}
                        >
                            <p style={{margin: '0 0 5px 0', fontWeight: 'bold', color: '#f0f0f0'}}>{item.original_filename}</p>
                            <p style={styles.historyDetails}>Status: {item.status.replace(/_/g, ' ')}</p>
                            {(item.status.includes('in_progress') || item.status.includes('interrupted')) && (
                                <p style={styles.historyDetails}>Progress: {item.progress.toFixed(0)}%</p>
                            )}
                            <p style={styles.historyDetails}>Last Update: {new Date(item.last_updated).toLocaleString()}</p>
                            <div style={styles.historyActions}>
                                {(item.status.includes('interrupted') || item.status.includes('in_progress')) && item.status !== 'completed' && (
                                    <button 
                                        onClick={() => handleResumeDrawingFromHistory(item.drawing_id)}
                                        style={{...styles.button, backgroundColor: '#ffc107', color: '#1e1e1e'}}
                                        disabled={isDrawingActive || !isConnectedToBackend || !isRobotConnected}
                                    >
                                        Resume
                                    </button>
                                )}
                                <button 
                                    onClick={() => handleRestartDrawingFromHistory(item.drawing_id)}
                                    style={{...styles.button, backgroundColor: '#17a2b8'}}
                                    disabled={isDrawingActive || !isConnectedToBackend || !isRobotConnected}
                                >
                                    Restart
                                </button>
                            </div>
                        </li>
                    ))}
                </ul>
            </section>
        )}

      </div>

      {/* Threshold Selection Modal */}
      {showThresholdModal && (
        <div style={styles.modalOverlay}>
          <div style={styles.modalContent}>
            <h3 style={styles.modalTitle}>Select Drawing Details</h3>
            <div style={styles.modalColumns}>
                <div style={{...styles.modalColumn, ...styles.modalRadioGroup}}>
                    {THRESHOLD_OPTIONS.map(option => (
                        <label 
                            key={option.key} 
                            htmlFor={option.key}
                            style={{
                                ...styles.modalRadioLabel,
                                ...(selectedThresholdKey === option.key ? styles.modalRadioLabelSelected : {})
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = selectedThresholdKey === option.key ? '#0056b3' : '#444')}
                            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = selectedThresholdKey === option.key ? '#007bff' : 'transparent')}
                        >
                            <input 
                                type="radio" 
                                id={option.key} 
                                name="thresholdOption" 
                                value={option.key}
                                checked={selectedThresholdKey === option.key}
                                onChange={() => setSelectedThresholdKey(option.key)}
                                style={{ marginRight: '10px', accentColor: '#61dafb' }}
                            />
                            {option.label} (T1: {option.t1}, T2: {option.t2})
                        </label>
                    ))}
                </div>
                <div style={{...styles.modalColumn, ...styles.modalPreviewArea}}>
                    <h4>Preview:</h4>
                    {isPreviewLoading && <p style={{color: '#aaa'}}>Loading preview...</p>}
                    {!isPreviewLoading && thresholdPreviewImage && (
                        <img src={thresholdPreviewImage} alt="Edge preview" style={styles.modalPreviewImage} />
                    )}
                    {!isPreviewLoading && !thresholdPreviewImage && (
                        <div style={{...styles.modalPreviewImage, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#777'}}>
                            <span>No preview available</span>
                        </div>
                    )}
                </div>
            </div>
            <div style={styles.modalActions}>
              <button 
                onClick={() => setShowThresholdModal(false)} 
                style={{...styles.button, backgroundColor: '#6c757d', marginRight: '10px'}}
              >
                Cancel
              </button>
              <button 
                onClick={confirmAndStartDrawingWithThresholds} 
                style={{...styles.button, backgroundColor: '#28a745'}}
                disabled={isPreviewLoading}
              >
                Start Drawing
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;

