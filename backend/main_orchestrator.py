# backend/main_orchestrator.py
import threading
import logging
import eventlet # Import eventlet
eventlet.monkey_patch() # Patch standard libraries for async compatibility

# Import the app and socketio instances, and the queues/result processor
from api_server import app, socketio, command_queue, result_queue, result_processor_thread
from robot_worker import RobotWorker
import config

if __name__ == '__main__':
    # Configure basic logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(name)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s'
    )

    # 1. Start the RobotWorker thread (Fn2)
    robot_worker = RobotWorker(command_queue, result_queue)
    worker_thread = threading.Thread(target=robot_worker.run, daemon=True)
    worker_thread.start()
    logging.info("Fn2 (RobotWorker) thread started.")

    # 2. Start the Result Processor thread (part of Fn1)
    result_thread = threading.Thread(target=result_processor_thread, daemon=True)
    result_thread.start()
    logging.info("Fn1 (Result Processor) thread started.")

    # 3. Start the Flask-SocketIO server using eventlet
    server_port = 5555
    if 'SERVER_PORT' not in app.config:
        app.config['SERVER_PORT'] = server_port

    logging.info(f"Starting Fn1 (Flask-SocketIO server with eventlet) on port {server_port}...")
    
    # Use socketio.run which will now use eventlet due to the monkey_patch and async_mode setting in api_server.py
    socketio.run(app,
                 host='0.0.0.0',
                 port=server_port,
                 debug=False) # Debug mode is not recommended with eventlet in this setup

