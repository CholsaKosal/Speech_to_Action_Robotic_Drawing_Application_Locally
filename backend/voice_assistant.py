# backend/voice_assistant.py
import whisper
import os
import time
from llama_cpp import Llama
import config # Import your project's config
import logging # For better logging
import json # For parsing structured commands

# --- Whisper STT Model ---
WHISPER_MODEL_SIZE = "base" 
whisper_model = None # Global variable for the Whisper model instance

def load_whisper_model():
    """Loads the Whisper model. Call this once when the server starts."""
    global whisper_model 
    if whisper_model is None:
        logging.info(f"Attempting to load Whisper model ({WHISPER_MODEL_SIZE})...")
        try:
            whisper_model = whisper.load_model(WHISPER_MODEL_SIZE)
            logging.info(f"Whisper model ({WHISPER_MODEL_SIZE}) loaded successfully.")
            return whisper_model 
        except Exception as e:
            logging.error(f"Error loading Whisper model: {e}", exc_info=True)
            whisper_model = None 
            return None
    logging.info("Whisper model already loaded.")
    return whisper_model

def transcribe_audio(audio_filepath):
    """Transcribes the given audio file using the loaded Whisper model."""
    global whisper_model 
    if whisper_model is None:
        logging.error("Whisper model is not loaded. Cannot transcribe.")
        if load_whisper_model() is None: # Attempt to load if not loaded
            logging.error("Failed to load Whisper model on demand.")
            return None
    
    if not os.path.exists(audio_filepath):
        logging.error(f"Audio file not found for transcription: {audio_filepath}")
        return None

    try:
        logging.info(f"Transcribing audio file: {audio_filepath}...")
        start_time = time.time()
        result = whisper_model.transcribe(audio_filepath, fp16=False) 
        transcription = result["text"]
        end_time = time.time()
        logging.info(f"Transcription complete in {end_time - start_time:.2f} seconds: '{transcription}'")
        return transcription
    except Exception as e:
        logging.error(f"Error during audio transcription: {e}", exc_info=True)
        return None

# --- Llama LLM ---
llm_instance = None # Global variable for the Llama model instance
llm_chat_history = [] 

def load_llm_model():
    """
    Loads the Llama GGUF model if specified in config.py.
    This is now optional. If LLM_MODEL_FILENAME is empty, it will skip loading.
    """
    global llm_instance 
    if llm_instance is not None:
        logging.info("LLM model already loaded.")
        return llm_instance

    model_filename = config.LLM_MODEL_FILENAME
    
    # *** ADDED CHECK: If no model filename is provided, disable LLM features ***
    if not model_filename:
        logging.warning("LLM_MODEL_FILENAME in config.py is empty. LLM-based voice commands will be disabled.")
        llm_instance = None
        return None

    base_dir = os.path.dirname(os.path.abspath(__file__)) 
    model_path = os.path.join(base_dir, "models", model_filename)
    
    logging.info(f"Attempting to load LLM model from path: {model_path}")
    if not os.path.exists(model_path):
        logging.error(f"LLM model file NOT FOUND at: {model_path}")
        logging.error(f"Please ensure the model is downloaded to 'backend/models/' and the filename is correct in config.py.")
        llm_instance = None
        return None
    
    try:
        llm_instance = Llama(
            model_path=model_path,
            n_ctx=config.LLM_N_CTX,         
            n_gpu_layers=config.LLM_N_GPU_LAYERS, 
            chat_format="chatml", 
            verbose=True
        )
        logging.info(f"LLM model ({model_filename}) loaded successfully.")
        return llm_instance 
    except Exception as e:
        logging.error(f"Fatal error loading LLM model from {model_path}: {e}", exc_info=True)
        llm_instance = None
        return None


def process_command_with_llm_stream(text_input):
    """
    Processes the transcribed text with the LLM. If LLM is not loaded, it returns an error.
    """
    global llm_instance, llm_chat_history 

    if llm_instance is None:
        logging.error("LLM model is not available. Cannot process text command.")
        yield {"error": "LLM model not configured or failed to load. Voice commands are disabled.", "done": True}
        return

    # Reset history for each new command for simplicity
    llm_chat_history = []
    llm_chat_history.append({"role": "user", "content": text_input})
    
    system_prompt = (
        "## YOU ARE ROBOTIST - ROBOT CONTROLLER ##\n"
        "Your primary function is to understand user commands and translate them into structured JSON for a robot arm. "
        "You also provide brief spoken feedback.\n\n"
        "### CORE ROBOT ACTIONS & REQUIRED OUTPUT FORMAT ###\n"
        "For the following specific user intents, your output MUST be in this exact two-part format:\n"
        "1.  **Spoken Confirmation:** A very short, direct confirmation.\n"
        "2.  **System Directive (`ACTION_CMD:`):** IMMEDIATELY after the spoken confirmation, append `ACTION_CMD:` followed by the precise JSON shown below. This JSON part is for the system and IS NOT SPOKEN.\n\n"
        "**MANDATORY EXAMPLES - FOLLOW THESE EXACTLY:**\n\n"
        "  - User input contains: \"home\", \"go home\", \"move to home position\"\n"
        "    Your Output: `Okay, moving home. ACTION_CMD: {\"type\": \"move\", \"parameters\": {\"target\": \"home\"}}`\n\n"
        "  - User input contains: \"center\", \"go to center\", \"move to center position\", \"middle of paper\"\n"
        "    Your Output: `Alright, moving to the center. ACTION_CMD: {\"type\": \"move\", \"parameters\": {\"target\": \"center\"}}`\n\n"
        "  - User input (after image upload is confirmed by system): \"draw it\", \"start drawing\", \"go ahead and draw\"\n"
        "    Your Output: `Starting the drawing. ACTION_CMD: {\"type\": \"draw_uploaded_image\"}`\n\n"
        "**IMPORTANT:**\n"
        "- If the user's command clearly matches one of the above intents, you MUST output both the spoken confirmation AND the corresponding `ACTION_CMD:` block. NO EXCEPTIONS.\n"
        "- If the user asks to draw something from a verbal description (e.g., \"draw a cat\"), respond: `I need an image to draw from. Please upload one. ACTION_CMD: {\"type\": \"draw_request_clarification\", \"details\": \"User asked to draw from description. Needs image.\"}`\n"
        "- For any other input (greetings, questions, unclear commands), provide a very brief, helpful response as Robotist. DO NOT output `ACTION_CMD:` for these. Example: User: \"Hello\" -> Your Output: `Hello! Robotist here.` User: \"What can you do?\" -> Your Output: `I can control the robot to move and draw from images.`\n\n"
        "Be direct and prioritize the `ACTION_CMD:` for recognized actions. You are Robotist."
    )
    
    messages_for_llm = [
        {"role": "system", "content": system_prompt},
    ] + llm_chat_history

    full_assistant_response = ""
    try:
        stream = llm_instance.create_chat_completion(
            messages=messages_for_llm,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE,
            stream=True 
        )
        
        for chunk_data in stream:
            delta = chunk_data['choices'][0]['delta']
            content_piece = delta.get('content')
            if content_piece: 
                yield {"chunk": content_piece, "done": False}
                full_assistant_response += content_piece
            if chunk_data['choices'][0].get('finish_reason') is not None:
                break 
        
        logging.info(f"LLM Final Assembled Output: {full_assistant_response}")

        parsed_action_command = None
        final_natural_language_response = full_assistant_response.strip() 

        action_cmd_marker = "ACTION_CMD:"
        if action_cmd_marker in final_natural_language_response:
            parts = final_natural_language_response.split(action_cmd_marker, 1)
            spoken_part = parts[0].strip()
            action_json_str = parts[1].strip()
            
            try:
                potential_json = action_json_str[action_json_str.find('{') : action_json_str.rfind('}')+1]
                parsed_action_command = json.loads(potential_json)
                logging.info(f"Successfully parsed ACTION_CMD: {parsed_action_command}")
            except Exception as e: 
                logging.warning(f"Could not parse ACTION_CMD JSON: {e}. String was: '{action_json_str}'")

            final_natural_language_response = spoken_part if spoken_part else "Processing command."

        final_response_payload = { "chunk": "", "done": True, "final_message": final_natural_language_response }
        if parsed_action_command:
            final_response_payload["parsed_action"] = parsed_action_command
        
        yield final_response_payload

    except Exception as e:
        logging.error(f"Error during LLM streaming: {e}", exc_info=True)
        yield {"error": f"LLM streaming failed: {str(e)}", "done": True}

