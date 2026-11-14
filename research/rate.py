"""Serve a rating round in the browser. usage: rate.py <round> [port]
Reads data/rounds/<round>.json (from pick_round.py) and writes the verdicts to data/rounds/<round>_labels.json.
The page never receives the group a listing came from."""
import json, pathlib, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from common import DATA

name = sys.argv[1]
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
items = json.loads((DATA / "rounds" / f"{name}.json").read_text())
labels_path = DATA / "rounds" / f"{name}_labels.json"
ids = {it["item_id"] for it in items}
shown = [{k: it[k] for k in ("item_id", "title", "brand", "size", "price")} for it in items]
page = (pathlib.Path(__file__).parent / "rate.html").read_bytes()


class Handler(BaseHTTPRequestHandler):
    def send(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send(page, "text/html; charset=utf-8")
        elif self.path == "/round":
            labels = json.loads(labels_path.read_text()) if labels_path.exists() else {"likes": [], "finished": False}
            self.send(json.dumps({"items": shown, **labels}).encode(), "application/json")
        elif self.path.startswith("/img/") and self.path[5:] in ids:
            self.send((DATA / "img310" / f"{self.path[5:]}.webp").read_bytes(), "image/webp")
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        likes = [i for i in body["likes"] if i in ids]
        labels_path.write_text(json.dumps({"likes": likes, "finished": bool(body["finished"])}))
        self.send(b"{}", "application/json")

    def log_message(self, *args):
        pass


print(f"round {name}: {len(items)} listings on http://127.0.0.1:{port}", flush=True)
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
