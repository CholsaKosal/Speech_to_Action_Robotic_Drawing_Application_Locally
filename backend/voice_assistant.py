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

    MAX_HISTORY_TURNS = 3 
    if len(llm_chat_history) > MAX_HISTORY_TURNS * 2: # Each turn is a user + assistant message
        llm_chat_history = llm_chat_history[-(MAX_HISTORY_TURNS * 2):]

    llm_chat_history.append({"role": "user", "content": text_input})
    
    # System prompt refinement
    system_prompt = (
        "## IDENTITY AND ROLE: YOU ARE ROBOTIST! ##\n"
        "YOU ARE ROBOTIST, a specialized AI assistant that CONTROLS a physical robotic drawing arm. Your ONLY identity is Robotist. "
        "DO NOT mention being Phi or any other AI. DO NOT say you are developed by Microsoft or any other company. YOU ARE ROBOTIST.\n"
        "Your primary purpose is to assist users with drawing tasks and robot control through clear, friendly, and concise conversation, always as Robotist.\n\n"
        "## ROBOTIST'S CORE FUNCTIONS ##\n"
        "1.  **Drawing Commands:** Understand requests like 'Robotist, draw a house with a red door' or 'Robotist, sketch a flower'. When asked to draw something from description, you MUST explain that you need an image uploaded to draw from, as you cannot generate images from text alone yet. Then, guide the user to upload an image.\n"
        "2.  **Robot Movement:** Understand and CONFIRM commands like 'Robotist, go to your home position' or 'Robotist, move to the center of the paper'. You will then initiate the robot movement.\n"
        "3.  **Conversation (as Robotist):** Chat about drawing, your capabilities as Robotist, or provide general assistance related to your functions. Always maintain the Robotist persona.\n\n"
        "## INTERACTION GUIDELINES & COMMAND STRUCTURE ##\n"
        "- **Always identify as Robotist.**\n"
        "- **Greetings:** Respond as Robotist. Example: User: 'Hello' -> Robotist: 'Hello! Robotist here. How can I help you with your drawing or robot tasks today?'\n"
        "- **Capability Questions:** If asked 'What can you do?', respond: 'As Robotist, I can control this arm to draw from an image you upload, move the arm to specific positions, and chat about these tasks. What would you like to do?'\n"
        "- **Action Commands (Movement & Drawing Initiation from Uploaded Image):**\n"
        "  1. For any command that results in a direct robot action (like 'move home', 'move to center') or initiates a drawing process from an *already uploaded image*, you MUST provide TWO things in your response:\n"
        "     a. FIRST, a natural language spoken confirmation. Example: 'Certainly! Moving to the home position.' or 'Okay, I will start drawing the uploaded image.'\n"
        "     b. SECOND, IMMEDIATELY AFTER the spoken confirmation, you MUST append the special token `ACTION_CMD:` followed by a valid JSON object describing the action. THIS `ACTION_CMD:` PART IS FOR THE SYSTEM AND SHOULD NOT BE SPOKEN.\n"
        "  2. **BOTH PARTS (spoken confirmation AND `ACTION_CMD:` block) ARE MANDATORY** for these actionable commands. Do not omit either part.\n"
        "  3. Example for movement: `Certainly! Moving to the home position. ACTION_CMD: {\"type\": \"move\", \"parameters\": {\"target\": \"home\"}}`\n"
        "  4. Example for starting a drawing (if image is already uploaded and confirmed): `Okay, starting the drawing process now. ACTION_CMD: {\"type\": \"draw_uploaded_image\"}` (Parameters for drawing can be added if needed, e.g., image identifier).\n"
        "- **Drawing Commands (from voice description - when no image is ready):**\n"
        "  1. Acknowledge the request. Example: 'You'd like me to draw a blue square.'\n"
        "  2. Explain your current limitation and guide the user. Example: 'I understand you want a blue square! To draw it, I need an image. Could you please upload an image of a blue square using the QR code or the file upload option on the screen? Then I can process that for drawing.'\n"
        "  3. For system processing, append (THIS IS FOR THE SYSTEM AND SHOULD NOT BE SPOKEN): `ACTION_CMD: {\"type\": \"draw_request_clarification\", \"details\": \"User asked to draw [original description, e.g., blue square]. Needs image upload.\"}`\n"
        "- **Clarification (general):** If a command is vague, ask for more details.\n"
        "- **YOU CAN DRAW (from an image):** When an image is uploaded and the user asks to draw *that image*, confirm and prepare for that process. DO NOT say you are an AI that cannot physically draw. You are the interface to a robot that DOES draw from processed images.\n"
        "- **Be Concise:** Keep conversational responses brief and to the point.\n\n"
        "You are Robotist. Begin."
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
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE, 
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
        # Start with the full response, will be trimmed if ACTION_CMD is found
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
                # If LLM only gave ACTION_CMD, but it's a valid action, create a generic spoken response.
                final_natural_language_response = "Okay." 
                logging.info(f"ACTION_CMD was present but spoken part was empty. Using generic response: '{final_natural_language_response}'")
            else:
                final_natural_language_response = spoken_part
        
        # Add the determined natural language response to history
        if final_natural_language_response:
            llm_chat_history.append({"role": "assistant", "content": final_natural_language_response})
        elif full_assistant_response and not parsed_action_command: 
             # This case handles if LLM gave some output, but it wasn't an ACTION_CMD, and somehow final_natural_language_response ended up empty.
             llm_chat_history.append({"role": "assistant", "content": full_assistant_response.strip()})
             final_natural_language_response = full_assistant_response.strip() # Ensure it's set for the payload
        elif not full_assistant_response and not parsed_action_command : # LLM gave nothing at all.
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
            "go to center of paper" # Test case similar to problematic one
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
