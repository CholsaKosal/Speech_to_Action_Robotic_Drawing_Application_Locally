# backend/voice_assistant.py
import whisper
import os
import time
from llama_cpp import Llama
import config # Import your project's config
import logging # For better logging

# --- Whisper STT Model ---
WHISPER_MODEL_SIZE = "base" 
whisper_model = None # Global variable for the Whisper model instance

def load_whisper_model():
    """Loads the Whisper model. Call this once when the server starts."""
    global whisper_model # Declare that we are using the global variable
    if whisper_model is None:
        logging.info(f"Attempting to load Whisper model ({WHISPER_MODEL_SIZE})...")
        try:
            whisper_model = whisper.load_model(WHISPER_MODEL_SIZE)
            logging.info(f"Whisper model ({WHISPER_MODEL_SIZE}) loaded successfully.")
            return whisper_model # Return the loaded model
        except Exception as e:
            logging.error(f"Error loading Whisper model: {e}", exc_info=True)
            whisper_model = None 
            return None
    logging.info("Whisper model already loaded.")
    return whisper_model

def transcribe_audio(audio_filepath):
    """Transcribes the given audio file using the loaded Whisper model."""
    global whisper_model # Ensure we're using the global instance
    if whisper_model is None:
        logging.error("Whisper model is not loaded. Cannot transcribe.")
        return None
    
    if not os.path.exists(audio_filepath):
        logging.error(f"Audio file not found for transcription: {audio_filepath}")
        return None

    try:
        logging.info(f"Transcribing audio file: {audio_filepath} with Whisper model {WHISPER_MODEL_SIZE}...")
        start_time = time.time()
        result = whisper_model.transcribe(audio_filepath, fp16=False) 
        transcription = result["text"]
        end_time = time.time()
        logging.info(f"Transcription complete in {end_time - start_time:.2f} seconds.")
        logging.info(f"Transcription: {transcription}")
        return transcription
    except Exception as e:
        logging.error(f"Error during audio transcription: {e}", exc_info=True)
        return None

# --- Llama LLM ---
llm_instance = None # Global variable for the Llama model instance
llm_chat_history = [] 

def load_llm_model():
    """Loads the Llama GGUF model. Call this once when the server starts."""
    global llm_instance # Declare that we are using the global variable
    if llm_instance is None:
        model_filename = config.LLM_MODEL_FILENAME
        model_path = os.path.join(os.path.dirname(__file__), "models", model_filename)
        
        logging.info(f"Attempting to load LLM model: {model_filename} from path: {model_path}")
        if not os.path.exists(model_path):
            logging.error(f"LLM model file NOT FOUND at: {model_path}")
            logging.error("Please ensure the model is downloaded and LLM_MODEL_FILENAME in config.py is correct.")
            llm_instance = None
            return None
        
        logging.info(f"LLM model file found. Initializing Llama from: {model_path} ...")
        try:
            llm_instance = Llama(
                model_path=model_path,
                n_ctx=config.LLM_N_CTX,         
                n_gpu_layers=config.LLM_N_GPU_LAYERS, 
                verbose=True # Llama.cpp internal logging
            )
            logging.info(f"LLM model ({model_filename}) loaded successfully into llm_instance.")
            return llm_instance # Return the loaded model
        except Exception as e:
            logging.error(f"Error loading LLM model from {model_path}: {e}", exc_info=True)
            llm_instance = None
            return None
    logging.info("LLM model already loaded.")
    return llm_instance

def process_command_with_llm(text_input):
    """
    Processes the transcribed text with the LLM to get a response or action.
    """
    global llm_instance, llm_chat_history # Ensure we're using the global instance
    if llm_instance is None:
        logging.error("LLM model (llm_instance) is not loaded. Cannot process command.")
        # Attempt to load it now as a fallback, though it should be loaded at startup
        if load_llm_model() is None: # This will try to set the global llm_instance
             return {"error": "LLM not available (failed to load)."}
        # If load_llm_model() succeeded, llm_instance is now set.

    MAX_HISTORY_TURNS = 4 
    if len(llm_chat_history) > MAX_HISTORY_TURNS * 2:
        llm_chat_history = llm_chat_history[-(MAX_HISTORY_TURNS * 2):]

    llm_chat_history.append({"role": "user", "content": text_input})
    
    messages_for_llm = [
        {"role": "system", "content": (
            "You are a helpful assistant controlling a robot arm capable of drawing. "
            "Understand user commands related to drawing, robot movement, or general conversation. "
            "If a command is for drawing, try to identify the action (e.g., 'draw', 'sketch'), "
            "the object/shape (e.g., 'square', 'circle', 'house'), and any parameters (e.g., 'red', 'big'). "
            "If it's a robot movement command, identify the target (e.g., 'home', 'center'). "
            "Keep responses concise. If asked to perform an action, confirm it. "
            "Example commands: 'Draw a circle', 'Go to the home position', 'What can you do?'."
            "If a drawing command is given, respond with the action and shape. For example if user says 'Draw a star', you can respond with 'Okay, I will draw a star.' "
            "If it is a simple conversational message, respond naturally."
        )},
    ] + llm_chat_history

    logging.info(f"\nSending to LLM ({config.LLM_MODEL_FILENAME}):")
    # for msg in messages_for_llm[-3:]: logging.info(f"  {msg['role']}: {msg['content']}") # Log last few

    try:
        start_time = time.time()
        # Use the global llm_instance
        response = llm_instance.create_chat_completion(
            messages=messages_for_llm,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE,
        )
        llm_output_text = response['choices'][0]['message']['content'].strip()
        end_time = time.time()
        
        logging.info(f"LLM response received in {end_time - start_time:.2f} seconds.")
        logging.info(f"LLM Raw Output: {llm_output_text}")

        llm_chat_history.append({"role": "assistant", "content": llm_output_text})
        return {"message": llm_output_text} 
    
    except Exception as e:
        logging.error(f"Error during LLM processing: {e}", exc_info=True)
        if llm_chat_history and llm_chat_history[-1]["role"] == "user":
            llm_chat_history.pop()
        return {"error": f"LLM processing failed."} # Simplified error message for client

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logging.info("Voice Assistant Module - Direct Test")
    load_whisper_model() 
    loaded_llm = load_llm_model() 
    
    if loaded_llm: # Check the return value, not the global directly in this test scope
        logging.info("\n--- Testing LLM directly ---")
        test_inputs = [
            "Hello there!",
            "Can you draw a square for me?",
        ]
        for test_input in test_inputs:
            logging.info(f"\nUser Input: {test_input}")
            # For direct test, ensure llm_instance is used if process_command_with_llm relies on global
            # Or pass loaded_llm to a test-specific version of process_command_with_llm
            # The current process_command_with_llm uses global llm_instance, which load_llm_model sets.
            llm_chat_history.clear() # Clear history for isolated test
            result = process_command_with_llm(test_input) 
            if result.get("message"):
                logging.info(f"LLM Response: {result['message']}")
            elif result.get("error"):
                logging.info(f"LLM Error: {result['error']}")
        llm_chat_history.clear() 
    else:
        logging.error("LLM model not loaded, skipping LLM direct test.")
