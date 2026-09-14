"""Loopback-only static site + actual pilot service for browser integration tests.

Private sessions stay outside the served directory. No AWS credentials, public
generation quotas, or deployed research records are used by this local server.
"""
import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets

from serverless.study.service import StudyService, StudyError, PROFILES
from serverless.study.store import SQLiteStudyStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site",type=Path,required=True)
    parser.add_argument("--protocol",type=Path,required=True)
    parser.add_argument("--state",type=Path,required=True)
    parser.add_argument("--port",type=int,default=8765)
    args = parser.parse_args()
    args.state.mkdir(parents=True,exist_ok=True)
    secret_path = args.state/"session-secret"
    if not secret_path.exists():
        secret_path.write_bytes(secrets.token_bytes(32))
    document = json.loads(args.protocol.read_text())
    service = StudyService(document,SQLiteStudyStore(args.state/"pilot.sqlite3"),secret_path.read_bytes(),enabled=True)
    invitations = args.state/"invitations.json"
    if not invitations.exists():
        invitations.write_text(json.dumps([{ "reviewerId":f"reviewer-{i+1:02d}", "profile":profile,
            "invitation":service.invite(f"reviewer-{i+1:02d}",profile,"Codex session default (provider model ID not exposed)")}
            for i,profile in enumerate(PROFILES)]),encoding="utf-8")
    # Hand each independent reviewer only their own invitation, never the roster.
    for invitation in json.loads(invitations.read_text()):
        destination = args.state/(invitation["reviewerId"]+".json")
        destination.write_text(json.dumps(invitation),encoding="utf-8")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*values,**kwargs):
            super().__init__(*values,directory=str(args.site),**kwargs)

        def log_message(self,format,*values):
            pass # Invitations and bearer tokens must not enter access logs.

        def guess_type(self,path):
            if Path(path).is_file() and not Path(path).suffix:
                return "text/html; charset=utf-8"
            return super().guess_type(path)

        def do_GET(self):
            if self.path.split("?")[0].startswith("/api-config"):
                script = b'window.SOILIE_API_BASE = ""; window.SOILIE_STUDY_API_BASE = "";'
                self.send_response(200)
                self.send_header("Content-Type","text/javascript")
                self.send_header("Content-Length",str(len(script)))
                self.end_headers()
                self.wfile.write(script)
            else:
                super().do_GET()

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length",0))
                if not 0 < size <= 8192:
                    raise ValueError()
                body = json.loads(self.rfile.read(size))
                if not isinstance(body,dict):
                    raise ValueError()
                if self.path == "/study/sessions":
                    result = service.start(body)
                else:
                    match = re.fullmatch(r"/study/sessions/([a-f0-9-]+)(/responses)?",self.path)
                    if not match:
                        raise StudyError(404,"NOT_FOUND","Route not found")
                    result = (service.respond if match[2] else service.resume)(match[1],body)
                status = 200
            except StudyError as error:
                status,result = error.status,{"error":{"code":error.code,"message":str(error)}}
            except (ValueError,TypeError):
                status,result = 400,{"error":{"code":"INVALID_JSON","message":"A small JSON object is required."}}
            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1",args.port),Handler)
    print(f"Local study server: http://127.0.0.1:{args.port}",flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
