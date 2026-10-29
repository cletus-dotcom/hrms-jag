"""
HRMS Application Entry Point
Run with: python run.py
"""
import os

from app.deploy_env import load_deploy_env

_loaded_env = load_deploy_env()
if _loaded_env:
    print(f'Loaded environment from {_loaded_env}')

from waitress import serve
from app import create_app

app = create_app()

if __name__ == '__main__':
    # Get port from environment variable or default to 8015
    port = int(os.environ.get('PORT', 8030))
    host = os.environ.get('HOST', '0.0.0.0')
    
    print(f"Starting HRMS server on http://{host}:{port}")
    print("Press CTRL+C to stop the server")
    try:
        with app.app_context():
            from app.portal_site_service import site_upload_dir
            print(f"Site uploads folder: {site_upload_dir()}")
            from app.ermt_nas_service import schedule_ermt_nas_connect, nas_connection_status
            print(nas_connection_status())
            schedule_ermt_nas_connect(app)
    except Exception:
        pass
    try:
        from app.portal_import_service import IMPORT_RESOLVER_VERSION
        print(f"Import resolver: {IMPORT_RESOLVER_VERSION}")
    except Exception:
        pass
    
    # Use Waitress as production WSGI server (optional WAITRESS_MAX_REQUEST_BODY_BYTES)
    serve_kw = {'host': host, 'port': port, 'threads': 4}
    wb = os.environ.get('WAITRESS_MAX_REQUEST_BODY_BYTES', '').strip()
    if wb:
        serve_kw['max_request_body_size'] = int(wb)
    serve(app, **serve_kw)
