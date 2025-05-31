# backend/main_orchestrator.py
from api_server import app, socketio

if __name__ == '__main__':
    print("Starting Python backend server (SocketIO with Flask)...")
    print("Frontend should connect to ws://localhost:5555 (or your machine's IP on the network)")
    # Use eventlet or gevent for production with Flask-SocketIO
    # For development, Werkzeug development server can be used but might need allow_unsafe_werkzeug=True
    # Or, more simply for development without extra dependencies if eventlet/gevent are tricky:
    socketio.run(app, host='0.0.0.0', port=5555, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)
    # Ensure eventlet is installed if you want to use it: pip install eventlet
    # socketio.run(app, host='0.0.0.0', port=5555, debug=True) # This will try to use eventlet or gevent if installed