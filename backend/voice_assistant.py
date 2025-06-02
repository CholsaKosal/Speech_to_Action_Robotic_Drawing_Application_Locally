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
                # chat_format="chatml", # Try if default formatting is off for DeepSeek
                verbose=True 
            )
            logging.info(f"LLM model ({model_filename}) loaded successfully into llm_instance.")
            return llm_instance 
        except Exception as e:
            logging.error(f"Error loading LLM model from {model_path}: {e}", exc_info=True)
            llm_instance = None
            return None
    logging.info("LLM model already loaded.")
    return llm_instance

def process_command_with_llm_stream(text_input):
    """
    Processes the transcribed text with the LLM and yields response chunks (streaming).
    Attempts to extract a structured command from the LLM's full response.
    """
    global llm_instance, llm_chat_history 
    if llm_instance is None:
        logging.error("LLM model (llm_instance) is not loaded. Cannot process command.")
        if load_llm_model() is None: 
            yield {"error": "LLM not available (failed to load).", "done": True}
            return 

    MAX_HISTORY_TURNS = 3 
    if len(llm_chat_history) > MAX_HISTORY_TURNS * 2:
        llm_chat_history = llm_chat_history[-(MAX_HISTORY_TURNS * 2):]

    llm_chat_history.append({"role": "user", "content": text_input})
    
    system_prompt = (
        "## IDENTITY AND ROLE: YOU ARE ROBOTIST! ##\n"
        "YOU ARE ROBOTIST, a specialized AI assistant that CONTROLS a physical robotic drawing arm. Your ONLY identity is Robotist. "
        "DO NOT mention being Phi or any other AI. DO NOT say you are developed by Microsoft or any other company. YOU ARE ROBOTIST.\n"
        "Your primary purpose is to assist users with drawing tasks and robot control through clear, friendly, and concise conversation, always as Robotist.\n\n"
        "## ROBOTIST'S CORE FUNCTIONS ##\n"
        "1.  **Drawing Commands:** Understand and CONFIRM requests like 'Robotist, draw a house with a red door' or 'Robotist, sketch a flower'. You will then initiate the drawing process through the robot arm.\n"
        "2.  **Robot Movement:** Understand and CONFIRM commands like 'Robotist, go to your home position' or 'Robotist, move to the center of the paper'. You will then initiate the robot movement.\n"
        "3.  **Conversation (as Robotist):** Chat about drawing, your capabilities as Robotist, or provide general assistance related to your functions. Always maintain the Robotist persona.\n\n"
        "## INTERACTION GUIDELINES & COMMAND STRUCTURE ##\n"
        "- **Always identify as Robotist.**\n"
        "- **Greetings:** Respond as Robotist. Example: User: 'Hello' -> Robotist: 'Hello! Robotist here. How can I help you draw today?'\n"
        "- **Capability Questions:** If asked 'What can you do?', respond: 'As Robotist, I can control this arm to draw what you describe, move the arm, and chat about our drawing projects. What would you like to create?'\n"
        "- **Action Commands (Drawing/Movement):**\n"
        "  1. Acknowledge the command naturally (e.g., 'Okay, you'd like me to draw a red circle.').\n"
        "  2. State your intention to perform the action (e.g., 'I will now draw a red circle.').\n"
        "  3. **CRITICAL FOR SYSTEM:** After your natural language response, if it's an actionable command, append the special token `ACTION_CMD:` followed by a valid JSON object describing the command. This JSON is for system processing and should not be part of your spoken response to the user.\n"
        "     - Example User: 'Robotist, draw a blue square.'\n"
        "     - Example Robotist Spoken Response: 'Alright, a blue square it is! I will draw a blue square.'\n"
        "     - Example Robotist Appended System Command: `ACTION_CMD: {\"type\": \"draw\", \"parameters\": {\"shape\": \"square\", \"color\": \"blue\"}}`\n\n"
        "     - Example User: 'Robotist, go home.'\n"
        "     - Example Robotist Spoken Response: 'Certainly! Moving to the home position.'\n"
        "     - Example Robotist Appended System Command: `ACTION_CMD: {\"type\": \"move\", \"parameters\": {\"target\": \"home\"}}`\n\n"
        "     - Example User: 'What can you draw?' (This is conversational)\n"
        "     - Example Robotist Spoken Response: 'I can draw various shapes like circles, squares, triangles, and simple objects. What did you have in mind?' (No ACTION_CMD needed here)\n"
        "- **Clarification:** If a command is vague, ask for more details. E.g., 'Draw something.' -> Robotist: 'I can try! What kind of object or shape would you like me to draw?' (No ACTION_CMD needed for clarification questions).\n"
        "- **YOU CAN DRAW:** When asked to draw, DO NOT say you are an AI that cannot physically draw. You are the interface to a robot that DOES draw. Confirm the drawing command positively.\n"
        "- **Be Concise:** Keep conversational responses brief and to the point.\n\n"
        "You are Robotist. Begin."
    )
    
    messages_for_llm = [
        {"role": "system", "content": system_prompt},
    ] + llm_chat_history

    logging.info(f"\nStreaming to LLM ({config.LLM_MODEL_FILENAME}) with {len(messages_for_llm)} total messages in context.")

    full_assistant_response = ""
    try:
        start_time = time.time()
        stream = llm_instance.create_chat_completion(
            messages=messages_for_llm,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE, 
            stream=True 
        )
        
        logging.info("LLM stream initiated.")
        for chunk_index, chunk in enumerate(stream):
            delta = chunk['choices'][0]['delta']
            content_piece = delta.get('content')
            
            if content_piece: 
                yield {"chunk": content_piece, "done": False}
                full_assistant_response += content_piece
            
            if chunk['choices'][0].get('finish_reason') is not None:
                logging.info(f"LLM stream finished. Reason: {chunk['choices'][0]['finish_reason']}")
                break 
        
        end_time = time.time()
        logging.info(f"LLM full response streamed in {end_time - start_time:.2f} seconds.")
        logging.info(f"LLM Final Assembled Output (raw): {full_assistant_response}")

        # --- Attempt to extract and parse ACTION_CMD ---
        parsed_action_command = None
        natural_language_response = full_assistant_response # Default to full response

        action_cmd_marker = "ACTION_CMD:"
        if action_cmd_marker in full_assistant_response:
            parts = full_assistant_response.split(action_cmd_marker, 1)
            natural_language_response = parts[0].strip() # Text before ACTION_CMD
            action_json_str = parts[1].strip()
            logging.info(f"Found ACTION_CMD marker. JSON string part: {action_json_str}")
            try:
                # Attempt to find the first '{' and last '}' to isolate the JSON object
                # This is a basic attempt and might need refinement for complex cases
                json_start = action_json_str.find('{')
                json_end = action_json_str.rfind('}')
                if json_start != -1 and json_end != -1 and json_end > json_start:
                    potential_json = action_json_str[json_start : json_end+1]
                    logging.info(f"Attempting to parse JSON: {potential_json}")
                    parsed_action_command = json.loads(potential_json)
                    logging.info(f"Successfully parsed ACTION_CMD: {parsed_action_command}")
                else:
                    logging.warning("Could not properly isolate JSON object within ACTION_CMD string.")
            except json.JSONDecodeError as e:
                logging.warning(f"JSONDecodeError parsing ACTION_CMD: {e}. String was: {action_json_str}")
            except Exception as e:
                logging.warning(f"Generic error parsing ACTION_CMD: {e}")
        
        llm_chat_history.append({"role": "assistant", "content": natural_language_response}) # Store only natural response in history
        
        final_response_payload = {
            "chunk": "", 
            "done": True, 
            "final_message": natural_language_response # Send the spoken part to frontend
        }
        if parsed_action_command:
            final_response_payload["parsed_action"] = parsed_action_command
        
        yield final_response_payload

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
            "Hello Robotist",
            "What can you do?",
            "Robotist draw a red square",
            "Move home"
        ]
        for test_input in test_inputs:
            logging.info(f"\nUser Input: {test_input}")
            llm_chat_history.clear() 
            
            print(f"Robotist Response (Streaming for '{test_input}'): ", end="", flush=True)
            final_parsed_action_for_test = None
            final_natural_response_for_test = ""

            for response_part in process_command_with_llm_stream(test_input):
                if response_part.get("error"):
                    print(f"\nError: {response_part['error']}")
                    break
                if response_part.get("chunk"): # This is the streaming text for display
                    print(response_part["chunk"], end="", flush=True)
                
                if response_part.get("done"):
                    print("\n--- Stream Ended ---")
                    final_natural_response_for_test = response_part.get("final_message", "")
                    if response_part.get("parsed_action"):
                        final_parsed_action_for_test = response_part.get("parsed_action")
                        logging.info(f"TEST: Extracted Parsed Action: {final_parsed_action_for_test}")
                    # logging.info(f"TEST: Final Natural Language Response: {final_natural_response_for_test}")
                    break
            print("\n") 
        llm_chat_history.clear() 
    else:
        logging.error("LLM model not loaded, skipping LLM direct test.")

