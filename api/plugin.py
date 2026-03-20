"""Vercel serverless function — download the N+ plugin as a zip."""

from http.server import BaseHTTPRequestHandler
import io
import os
import zipfile


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        api_dir = os.path.dirname(os.path.abspath(__file__))
        files = {
            "audiophile_wishlist/__init__.py": os.path.join(api_dir, "_plugin_init.py"),
            "audiophile_wishlist/PLUGININFO": os.path.join(api_dir, "_plugin_info.txt"),
        }

        buf = io.BytesIO()

        try:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for arcname, filepath in files.items():
                    if os.path.exists(filepath):
                        zf.write(filepath, arcname)

            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", "attachment; filename=audiophile_wishlist.zip")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(buf.getvalue())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Error: {e}".encode())
