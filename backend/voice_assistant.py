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
        logging.info(f"Transcribing audio file: {audio_filepath} with Whisper model {WHISPER_MODEL_SIZE}...")
        start_time = time.time()
        # Ensure fp16 is False if CPU only, can be True if GPU supports it and it's configured
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
llm_instance = None # Global variable for the Llama model instance, set by load_llm_model
llm_chat_history = [] 

def load_llm_model():
    """Loads the Llama GGUF model. Call this once when the server starts."""
    global llm_instance 
    if llm_instance is None:
        model_filename = config.LLM_MODEL_FILENAME
        # Construct model path relative to this file's directory, then into 'models'
        base_dir = os.path.dirname(os.path.abspath(__file__)) 
        model_path = os.path.join(base_dir, "models", model_filename)
        
        logging.info(f"Attempting to load LLM model: {model_filename} from path: {model_path}")
        if not os.path.exists(model_path):
            logging.error(f"LLM model file NOT FOUND at: {model_path}")
            logging.error(f"Please ensure the model is downloaded to '{os.path.join('backend', 'models')}' and LLM_MODEL_FILENAME in config.py ('{model_filename}') is correct.")
            llm_instance = None
            return None
        
        logging.info(f"LLM model file found. Initializing Llama from: {model_path} ...")
        try:
            llm_instance = Llama(
                model_path=model_path,
                n_ctx=config.LLM_N_CTX,         
                n_gpu_layers=config.LLM_N_GPU_LAYERS, 
                chat_format="chatml", 
                verbose=True # llama.cpp verbose logging
            )
            logging.info(f"LLM model ({model_filename}) loaded successfully into voice_assistant.llm_instance with chat_format='chatml'.")
            return llm_instance 
        except Exception as e:
            logging.error(f"Error loading LLM model from {model_path} with chat_format='chatml': {e}", exc_info=True)
            try:
                logging.warning(f"Retrying LLM load for {model_filename} without explicit chat_format (auto-detection)...")
                llm_instance = Llama(
                    model_path=model_path,
                    n_ctx=config.LLM_N_CTX,         
                    n_gpu_layers=config.LLM_N_GPU_LAYERS, 
                    verbose=True 
                )
                logging.info(f"LLM model ({model_filename}) loaded successfully (fallback, auto-detected chat format).")
                return llm_instance
            except Exception as e2:
                logging.error(f"Error loading LLM model (fallback attempt) from {model_path}: {e2}", exc_info=True)
                llm_instance = None
                return None

    logging.info("LLM model already loaded (voice_assistant.llm_instance).")
    return llm_instance

def process_command_with_llm_stream(text_input):
    """
    Processes the transcribed text with the LLM and yields response chunks (streaming).
    Attempts to extract a structured command from the LLM's full response.
    """
    global llm_instance, llm_chat_history 
    
    logging.info(f"--- voice_assistant.process_command_with_llm_stream called with input: '{text_input}' ---")

    if llm_instance is None:
        logging.error("LLM model (voice_assistant.llm_instance) is not loaded. Attempting to load now.")
        if load_llm_model() is None: # Try to load it if it's not already
            logging.error("Failed to load LLM model on demand in process_command_with_llm_stream.")
            yield {"error": "LLM not available (failed to load).", "done": True}
            return 

    MAX_HISTORY_TURNS = 1 # Keep history very short for simpler models
    if len(llm_chat_history) > MAX_HISTORY_TURNS * 2: 
        llm_chat_history = llm_chat_history[-(MAX_HISTORY_TURNS * 2):]

    llm_chat_history.append({"role": "user", "content": text_input})
    
    # Simplified System Prompt - Focus on Command Extraction
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

    logging.info(f"Streaming to LLM ({config.LLM_MODEL_FILENAME}) with {len(messages_for_llm)} total messages in context. Last user message: '{text_input}'")

    full_assistant_response = ""
    try:
        start_time = time.time()
        
        stream = llm_instance.create_chat_completion(
            messages=messages_for_llm,
            max_tokens=config.LLM_MAX_TOKENS, # Max tokens for the LLM's response
            temperature=config.LLM_TEMPERATURE, # Controls randomness: 0.0 for deterministic, ~0.7 for creative
            stream=True 
        )
        
        logging.info("LLM stream initiated with llama.cpp.")
        chunk_count = 0
        for chunk_index, chunk_data in enumerate(stream):
            chunk_count += 1
            delta = chunk_data['choices'][0]['delta']
            content_piece = delta.get('content')
            
            if content_piece: 
                logging.debug(f"LLM Content Piece from chunk {chunk_index}: '{content_piece}'")
                yield {"chunk": content_piece, "done": False}
                full_assistant_response += content_piece
            else:
                logging.debug(f"LLM Chunk {chunk_index} had no 'content' in delta. Delta: {delta}")
            
            if chunk_data['choices'][0].get('finish_reason') is not None:
                logging.info(f"LLM stream finished by model. Reason: {chunk_data['choices'][0]['finish_reason']}. Total chunks processed: {chunk_count}")
                break 
        
        end_time = time.time()
        logging.info(f"LLM full response assembled/streamed in {end_time - start_time:.2f} seconds. Total chunks: {chunk_count}")
        logging.info(f"LLM Final Assembled Output (raw from stream): {full_assistant_response}")

        parsed_action_command = None
        final_natural_language_response = full_assistant_response.strip() 

        action_cmd_marker = "ACTION_CMD:"
        if action_cmd_marker in final_natural_language_response:
            parts = final_natural_language_response.split(action_cmd_marker, 1)
            spoken_part = parts[0].strip()
            action_json_str = parts[1].strip()
            
            logging.info(f"Found ACTION_CMD marker. Potential spoken part: '{spoken_part}'. JSON string part: '{action_json_str}'")
            try:
                json_start = action_json_str.find('{')
                json_end = action_json_str.rfind('}')
                if json_start != -1 and json_end != -1 and json_end > json_start:
                    potential_json = action_json_str[json_start : json_end+1]
                    logging.info(f"Attempting to parse JSON from ACTION_CMD: {potential_json}")
                    parsed_action_command = json.loads(potential_json)
                    logging.info(f"Successfully parsed ACTION_CMD: {parsed_action_command}")
                else:
                    logging.warning(f"Could not properly isolate JSON object within ACTION_CMD string: '{action_json_str}'")
            except json.JSONDecodeError as e:
                logging.warning(f"JSONDecodeError parsing ACTION_CMD: {e}. String was: '{action_json_str}'")
            except Exception as e: 
                logging.warning(f"Generic error parsing ACTION_CMD JSON: {e}. String was: '{action_json_str}'")

            if not spoken_part and parsed_action_command:
                final_natural_language_response = "Okay, processing that." 
                logging.info(f"ACTION_CMD was present but spoken part was empty. Using generic response: '{final_natural_language_response}'")
            else:
                final_natural_language_response = spoken_part
        
        if final_natural_language_response:
            llm_chat_history.append({"role": "assistant", "content": final_natural_language_response})
        elif full_assistant_response and not parsed_action_command: 
             llm_chat_history.append({"role": "assistant", "content": full_assistant_response.strip()})
             final_natural_language_response = full_assistant_response.strip() 
        elif not full_assistant_response and not parsed_action_command : 
            logging.warning("LLM produced an empty response and no action command.")
            final_natural_language_response = "I'm sorry, I didn't quite understand. Could you please rephrase?" 
            llm_chat_history.append({"role": "assistant", "content": final_natural_language_response})


        final_response_payload = {
            "chunk": "", 
            "done": True, 
            "final_message": final_natural_language_response 
        }
        if parsed_action_command:
            final_response_payload["parsed_action"] = parsed_action_command
        
        logging.info(f"Yielding final 'done' payload: {final_response_payload}")
        yield final_response_payload

    except Exception as e:
        logging.error(f"Error during LLM streaming in process_command_with_llm_stream: {e}", exc_info=True)
        if llm_chat_history and llm_chat_history[-1]["role"] == "user":
            llm_chat_history.pop() 
        yield {"error": f"LLM streaming failed due to server error: {str(e)}", "done": True}


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG) 
    
    logging.info("--- Voice Assistant Module Direct Test ---")
    
    logging.info("\n--- Testing Whisper STT ---")
    if load_whisper_model():
        logging.info("Whisper model loaded. Transcription test would require an audio file.")
    else:
        logging.error("Whisper model failed to load. Cannot test transcription.")

    logging.info("\n--- Testing LLM Streaming ---")
    if load_llm_model(): 
        test_inputs = [
            "Hello Robotist",
            "What can you do?",
            "Robotist draw a red square",
            "Move home",
            "go to center of paper", 
            "move to home position" 
        ]
        for test_input_idx, test_input_text in enumerate(test_inputs):
            logging.info(f"\n--- Test Input {test_input_idx + 1}: '{test_input_text}' ---")
            llm_chat_history.clear() 
            
            print(f"Robotist Response (Streaming for '{test_input_text}'): ", end="", flush=True)
            
            for response_part in process_command_with_llm_stream(test_input_text):
                if response_part.get("error"):
                    print(f"\nLLM Stream Error: {response_part['error']}")
                    break
                if response_part.get("chunk"): 
                    print(response_part["chunk"], end="", flush=True) 
                
                if response_part.get("done"):
                    print("\n--- LLM Stream Ended for this test input ---")
                    if response_part.get("final_message"):
                         logging.debug(f"Test: Final natural message from payload: '{response_part.get('final_message')}'")
                    if response_part.get("parsed_action"):
                        logging.info(f"Test: Extracted Parsed Action: {response_part.get('parsed_action')}")
                    else:
                        logging.info(f"Test: No parsed action extracted for this input.")
                    break 
            print("\n") 
        llm_chat_history.clear() 
    else:
        logging.error("LLM model not loaded, skipping LLM direct test.")

    logging.info("--- Voice Assistant Module Direct Test Complete ---")
