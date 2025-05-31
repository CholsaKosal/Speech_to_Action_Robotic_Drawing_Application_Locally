# backend/main_orchestrator.py
from api_server import app, socketio # app is the Flask app instance

if __name__ == '__main__':
    server_port = 5555 # Define the port
    app.config['SERVER_PORT'] = server_port # Make it available for URL generation in api_server

    print(f"Starting Python backend server (SocketIO with Flask) on port {server_port}...")
    print(f"Frontend should connect to ws://localhost:{server_port} (or your machine's IP on the network)")
    print(f"QR code upload page will be accessible via http://<YOUR_LOCAL_IP>:{server_port}/qr_upload_page/<session_id>")

    socketio.run(app, host='0.0.0.0', port=server_port, debug=True, async_mode='eventlet')
