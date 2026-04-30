"""
HRMS Application Entry Point
Run with: python run.py
"""
from waitress import serve
from app import create_app
import os

app = create_app()

if __name__ == '__main__':
    # Get port from environment variable or default to 8015
    port = int(os.environ.get('PORT', 8030))
    host = os.environ.get('HOST', '0.0.0.0')
    
    print(f"Starting HRMS server on http://{host}:{port}")
    print("Press CTRL+C to stop the server")
    
    # Use Waitress as production WSGI server
    serve(app, host=host, port=port, threads=4)
