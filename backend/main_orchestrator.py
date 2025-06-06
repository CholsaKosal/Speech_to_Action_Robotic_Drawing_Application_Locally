# backend/main_orchestrator.py
import threading
import logging
import eventlet
import os # Import os to check for files
eventlet.monkey_patch()

from api_server import app, socketio, command_queue, result_queue, result_processor_thread
from robot_worker import RobotWorker
import config

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(name)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'
    )

    # --- Start Worker Threads ---
    robot_worker = RobotWorker(command_queue, result_queue)
    worker_thread = threading.Thread(target=robot_worker.run, daemon=True)
    worker_thread.start()
    logging.info("Fn2 (RobotWorker) thread started.")

    result_thread = threading.Thread(target=result_processor_thread, daemon=True)
    result_thread.start()
    logging.info("Fn1 (Result Processor) thread started.")

    # --- SSL Configuration ---
    server_port = 5555
    app.config['SERVER_PORT'] = server_port
    
    # Define paths for SSL certificate and key
    cert_file = os.path.join(os.path.dirname(__file__), 'cert.pem')
    key_file = os.path.join(os.path.dirname(__file__), 'key.pem')
    
    ssl_context = None
    # Check if both certificate and key files exist
    if os.path.exists(cert_file) and os.path.exists(key_file):
        ssl_context = (cert_file, key_file)
        logging.info(f"SSL certificate found. Starting server on https://0.0.0.0:{server_port}")
    else:
        logging.warning(f"SSL certificate not found (cert.pem/key.pem). Mobile QR code upload will not work.")
        logging.warning("Please generate a self-signed certificate in the 'backend' directory. See README.md for instructions.")
        logging.info(f"Starting server without SSL on http://0.0.0.0:{server_port}")

    # --- Start Server ---
    # The socketio.run function will handle the web server
    socketio.run(app,
                 host='0.0.0.0',
                 port=server_port,
                 debug=False,
                 certfile=cert_file if ssl_context else None,
                 keyfile=key_file if ssl_context else None)
