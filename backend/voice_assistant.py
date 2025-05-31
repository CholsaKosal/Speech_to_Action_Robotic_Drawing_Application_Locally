# backend/voice_assistant.py
import whisper
import os
import time

# Load the Whisper model
# You can choose different model sizes: "tiny", "base", "small", "medium", "large"
# "base" or "small" are good starting points for development.
# Larger models are more accurate but slower and require more resources.
MODEL_SIZE = "base" 
model = None

def load_model():
    """Loads the Whisper model. Call this once when the server starts."""
    global model
    if model is None:
        print(f"Loading Whisper model ({MODEL_SIZE})... This might take a moment.")
        try:
            model = whisper.load_model(MODEL_SIZE)
            print(f"Whisper model ({MODEL_SIZE}) loaded successfully.")
        except Exception as e:
            print(f"Error loading Whisper model: {e}")
            # Potentially fall back to a smaller model or handle the error appropriately
            # For now, we'll let it be None and transcription will fail.
            model = None 
    return model

def transcribe_audio(audio_filepath):
    """
    Transcribes the given audio file using the loaded Whisper model.
    
    Args:
        audio_filepath (str): The path to the audio file to transcribe.
        
    Returns:
        str: The transcribed text, or None if an error occurred.
    """
    global model
    if model is None:
        print("Whisper model is not loaded. Cannot transcribe.")
        return None
    
    if not os.path.exists(audio_filepath):
        print(f"Audio file not found: {audio_filepath}")
        return None

    try:
        print(f"Transcribing audio file: {audio_filepath} with model {MODEL_SIZE}...")
        start_time = time.time()
        # result = model.transcribe(audio_filepath, fp16=False) # fp16=False can improve CPU compatibility
        result = model.transcribe(audio_filepath) 
        transcription = result["text"]
        end_time = time.time()
        print(f"Transcription complete in {end_time - start_time:.2f} seconds.")
        print(f"Transcription: {transcription}")
        return transcription
    except Exception as e:
        print(f"Error during audio transcription: {e}")
        return None

if __name__ == '__main__':
    # This is for testing the module directly
    # Ensure you have an audio file (e.g., test.wav) in the same directory or provide a full path
    print("Voice Assistant Module - Direct Test")
    load_model() # Load the model for testing
    if model:
        # Create a dummy audio file for testing if you don't have one
        # This requires `soundfile` and `numpy`: pip install soundfile numpy
        # For simplicity, assume a test audio file exists.
        test_audio_path = "test_audio.wav" # Replace with your test audio file
        if os.path.exists(test_audio_path):
            print(f"\nTesting transcription with: {test_audio_path}")
            text = transcribe_audio(test_audio_path)
            if text:
                print(f"\nTest Transcription Result: {text}")
            else:
                print("\nTest transcription failed.")
        else:
            print(f"\nTest audio file '{test_audio_path}' not found. Skipping direct transcription test.")
            print("To test, place an audio file named 'test_audio.wav' in the backend directory or update the path.")
    else:
        print("Model not loaded, cannot run transcription test.")
