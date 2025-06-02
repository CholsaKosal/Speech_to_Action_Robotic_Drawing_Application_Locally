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

def process_command_with_llm_stream(text_input):
    """
    Processes the transcribed text with the LLM and yields response chunks (streaming).
    """
    global llm_instance, llm_chat_history 
    if llm_instance is None:
        logging.error("LLM model (llm_instance) is not loaded. Cannot process command.")
        if load_llm_model() is None: 
            yield {"error": "LLM not available (failed to load).", "done": True}
            return # Important to return after yielding the error

    MAX_HISTORY_TURNS = 4 
    if len(llm_chat_history) > MAX_HISTORY_TURNS * 2:
        llm_chat_history = llm_chat_history[-(MAX_HISTORY_TURNS * 2):]

    llm_chat_history.append({"role": "user", "content": text_input})
    
    system_prompt = (
        "You are 'Robotist', the intelligent AI controlling a robotic drawing arm. Your primary functions are to:\n" # Updated name
        "1.  Understand and execute drawing commands (e.g., 'Draw a house', 'Sketch a flower').\n"
        "2.  Understand and execute robot movement commands (e.g., 'Go home', 'Move to the center').\n"
        "3.  Engage in helpful and friendly conversation with the user about your capabilities, drawing tasks, or general topics.\n\n"
        "When you receive a command that involves a robot action (drawing or movement):\n"
        "- Clearly acknowledge the command.\n"
        "- If the command is clear, confirm the action you will take. For example, if the user says 'Draw a red square', respond with something like: 'Okay, I will draw a red square.'\n"
        "- If the command is ambiguous or lacks detail (e.g., 'Draw something'), ask for clarification. For example: 'Sure, what would you like me to draw?'\n\n"
        "For general conversation (e.g., 'Hello', 'How are you?', 'What can you draw?'):\n"
        "- Respond naturally, politely, and concisely.\n\n"
        "Keep your responses focused and to the point. Avoid overly long explanations unless specifically asked.\n"
        "Your goal is to be a helpful and efficient interface to the robot arm."
    )
    
    messages_for_llm = [
        {"role": "system", "content": system_prompt},
    ] + llm_chat_history

    logging.info(f"\nStreaming to LLM ({config.LLM_MODEL_FILENAME}) with {len(messages_for_llm)} total messages in context.")

    full_assistant_response = ""
    try:
        start_time = time.time()
        # Use stream=True to get a generator
        stream = llm_instance.create_chat_completion(
            messages=messages_for_llm,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE,
            stream=True  # Enable streaming
        )
        
        logging.info("LLM stream initiated.")
        for chunk_index, chunk in enumerate(stream):
            delta = chunk['choices'][0]['delta']
            content_piece = delta.get('content')
            
            if content_piece: # Check if there's new content in this chunk
                # logging.debug(f"LLM Stream chunk {chunk_index}: '{content_piece}'") # Can be very verbose
                yield {"chunk": content_piece, "done": False}
                full_assistant_response += content_piece
            
            # Check if the stream is finished (OpenAI-like API often sends a finish_reason)
            if chunk['choices'][0].get('finish_reason') is not None:
                logging.info(f"LLM stream finished. Reason: {chunk['choices'][0]['finish_reason']}")
                break 
        
        end_time = time.time()
        logging.info(f"LLM full response streamed in {end_time - start_time:.2f} seconds.")
        logging.info(f"LLM Final Assembled Output: {full_assistant_response}")

        llm_chat_history.append({"role": "assistant", "content": full_assistant_response})
        yield {"chunk": "", "done": True, "final_message": full_assistant_response} # Signal end of stream

    except Exception as e:
        logging.error(f"Error during LLM streaming: {e}", exc_info=True)
        if llm_chat_history and llm_chat_history[-1]["role"] == "user":
            llm_chat_history.pop() 
        yield {"error": f"LLM streaming failed.", "done": True}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logging.info("Voice Assistant Module - Direct Test")
    load_whisper_model() 
    loaded_llm = load_llm_model() 
    
    if loaded_llm: 
        logging.info("\n--- Testing LLM Streaming directly ---")
        test_inputs = [
            "Hello Robotist!",
            "Can you draw a very detailed and intricate dragon for me, make it red and breathing fire, on a mountain top?",
        ]
        for test_input in test_inputs:
            logging.info(f"\nUser Input: {test_input}")
            llm_chat_history.clear() 
            
            print("LLM Response (Streaming): ", end="", flush=True)
            full_response_for_test = ""
            # Note: process_command_with_llm_stream is a generator
            for response_part in process_command_with_llm_stream(test_input):
                if response_part.get("error"):
                    print(f"\nError: {response_part['error']}")
                    break
                if response_part.get("chunk"):
                    print(response_part["chunk"], end="", flush=True)
                    full_response_for_test += response_part["chunk"]
                if response_part.get("done"):
                    print("\n--- Stream Ended ---")
                    # logging.info(f"Test: Full assembled response: {full_response_for_test}") # Already logged inside
                    break
            print("\n") # Newline after each test input's full response
        llm_chat_history.clear() 
    else:
        logging.error("LLM model not loaded, skipping LLM direct test.")

