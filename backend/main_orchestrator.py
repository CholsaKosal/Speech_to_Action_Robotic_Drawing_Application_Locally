# backend/main_orchestrator.py
from api_server import app, socketio # app is the Flask app instance

if __name__ == '__main__':
    server_port = 5555 # Define the port
    # It's good practice to ensure app config is set before running.
    # This is already done in api_server.py when it's imported, 
    # but explicitly setting it here or ensuring it's set in app object is fine.
    if 'SERVER_PORT' not in app.config:
        app.config['SERVER_PORT'] = server_port

    print(f"Starting Python backend server (SocketIO with Flask) on port {server_port}...")
    print(f"Frontend should connect to ws://localhost:{server_port} (or your machine's IP on the network)")
    
    # Construct the QR code upload page URL for the print message
    # This logic is similar to what's in api_server.py for determining host_ip
    # For simplicity in this print, we'll just remind the user it's their local IP.
    print(f"QR code upload page will be accessible via http://<YOUR_LOCAL_IP>:{server_port}/qr_upload_page/<session_id>")

    # The async_mode='eventlet' should be set during SocketIO instantiation in api_server.py
    # The use_reloader=False is important when debug=True with eventlet.
    socketio.run(app, 
                 host='0.0.0.0', 
                 port=server_port, 
                 debug=True, 
                 use_reloader=False) # Removed async_mode='eventlet' from here
