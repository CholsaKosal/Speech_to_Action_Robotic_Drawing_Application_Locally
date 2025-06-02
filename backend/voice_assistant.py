# backend/voice_assistant.py
import whisper
import os
import time
from llama_cpp import Llama
import config # Import your project's config

# --- Whisper STT Model ---
WHISPER_MODEL_SIZE = "base" 
whisper_model = None

def load_whisper_model():
    """Loads the Whisper model. Call this once when the server starts."""
    global whisper_model
    if whisper_model is None:
        print(f"Loading Whisper model ({WHISPER_MODEL_SIZE})...")
        try:
            whisper_model = whisper.load_model(WHISPER_MODEL_SIZE)
            print(f"Whisper model ({WHISPER_MODEL_SIZE}) loaded successfully.")
        except Exception as e:
            print(f"Error loading Whisper model: {e}")
            whisper_model = None 
    return whisper_model

def transcribe_audio(audio_filepath):
    """Transcribes the given audio file using the loaded Whisper model."""
    global whisper_model
    if whisper_model is None:
        print("Whisper model is not loaded. Cannot transcribe.")
        return None
    
    if not os.path.exists(audio_filepath):
        print(f"Audio file not found: {audio_filepath}")
        return None

    try:
        print(f"Transcribing audio file: {audio_filepath} with model {WHISPER_MODEL_SIZE}...")
        start_time = time.time()
        result = whisper_model.transcribe(audio_filepath, fp16=False) # fp16=False for CPU
        transcription = result["text"]
        end_time = time.time()
        print(f"Transcription complete in {end_time - start_time:.2f} seconds.")
        print(f"Transcription: {transcription}")
        return transcription
    except Exception as e:
        print(f"Error during audio transcription: {e}")
        return None

# --- Llama LLM ---
llm = None
llm_chat_history = [] # Simple way to maintain some context

def load_llm_model():
    """Loads the Llama GGUF model. Call this once when the server starts."""
    global llm
    if llm is None:
        model_path = os.path.join(os.path.dirname(__file__), "models", config.LLM_MODEL_FILENAME)
        if not os.path.exists(model_path):
            print(f"LLM model file not found at: {model_path}")
            print("Please ensure the model is downloaded and LLM_MODEL_FILENAME in config.py is correct.")
            llm = None
            return None
        
        print(f"Loading LLM model from: {model_path} ...")
        try:
            llm = Llama(
                model_path=model_path,
                n_ctx=config.LLM_N_CTX,         # Context window size
                n_gpu_layers=config.LLM_N_GPU_LAYERS, # Number of layers to offload to GPU (0 for CPU)
                verbose=True                    # Set to True for more detailed Llama.cpp output
            )
            print(f"LLM model loaded successfully ({config.LLM_MODEL_FILENAME}).")
        except Exception as e:
            print(f"Error loading LLM model: {e}")
            llm = None
    return llm

def process_command_with_llm(text_input):
    """
    Processes the transcribed text with the LLM to get a response or action.
    """
    global llm, llm_chat_history
    if llm is None:
        print("LLM model is not loaded. Cannot process command.")
        return {"error": "LLM not available."}

    # Simple chat history management (keep last N turns, e.g., 4 user + 4 assistant = 8 messages)
    MAX_HISTORY_TURNS = 4 
    if len(llm_chat_history) > MAX_HISTORY_TURNS * 2:
        llm_chat_history = llm_chat_history[-(MAX_HISTORY_TURNS * 2):]

    # Append user message to history
    llm_chat_history.append({"role": "user", "content": text_input})
    
    # For Phi-3, the prompt format is typically a list of messages
    # System prompt can guide the LLM's behavior
    # We might need to adjust the system prompt based on observed behavior
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
    ] + llm_chat_history # Add the current conversation history

    print(f"\nSending to LLM ({config.LLM_MODEL_FILENAME}):")
    for msg in messages_for_llm[-3:]: # Log last few messages for brevity
        print(f"  {msg['role']}: {msg['content']}")

    try:
        start_time = time.time()
        response = llm.create_chat_completion(
            messages=messages_for_llm,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE,
            # stop=["\nUser:", "\nAssistant:"] # Optional stop tokens
        )
        llm_output_text = response['choices'][0]['message']['content'].strip()
        end_time = time.time()
        
        print(f"LLM response received in {end_time - start_time:.2f} seconds.")
        print(f"LLM Raw Output: {llm_output_text}")

        # Append assistant's response to history
        llm_chat_history.append({"role": "assistant", "content": llm_output_text})
        
        # For now, we return the raw text.
        # Later, we might parse this for specific actions.
        # e.g., if llm_output_text contains "action:draw, shape:circle", parse it.
        return {"message": llm_output_text} # Changed from 'action' to 'message' for now
    
    except Exception as e:
        print(f"Error during LLM processing: {e}")
        # Remove the last user message if processing failed to avoid resending a broken state
        if llm_chat_history and llm_chat_history[-1]["role"] == "user":
            llm_chat_history.pop()
        return {"error": f"LLM processing failed: {str(e)}"}


if __name__ == '__main__':
    print("Voice Assistant Module - Direct Test")
    load_whisper_model() 
    load_llm_model() # Load LLM for testing
    
    if llm:
        print("\n--- Testing LLM directly ---")
        test_inputs = [
            "Hello there!",
            "Can you draw a square for me?",
            "What is the capital of France?",
            "Move the robot to its home position."
        ]
        for test_input in test_inputs:
            print(f"\nUser Input: {test_input}")
            llm_chat_history.clear() # Clear history for isolated test
            llm_chat_history.append({"role": "user", "content": test_input}) # Prime history for this test
            result = process_command_with_llm(test_input) # This will append again, effectively duplicating for test
                                                        # but it's okay for this direct test structure.
                                                        # Proper use in API server won't have this issue.
            if result.get("message"):
                print(f"LLM Response: {result['message']}")
            elif result.get("error"):
                print(f"LLM Error: {result['error']}")
        llm_chat_history.clear() # Clear history after tests
    else:
        print("LLM model not loaded, skipping LLM direct test.")

