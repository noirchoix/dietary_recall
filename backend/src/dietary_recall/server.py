"""Small dependency-free local research UI/API for the compatibility layer."""

from __future__ import annotations

import json
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .db import LegacyRepository
from .legacy import FoodPortion, calculate_foods
from .workspace import WorkingRepository


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dietary Recall Research Migration</title>
<style>
body{font-family:system-ui,sans-serif;max-width:980px;margin:40px auto;padding:0 18px;color:#17202a;background:#f7f9fb}
.card{background:white;border:1px solid #dce3e9;border-radius:12px;padding:20px;margin:16px 0} code{background:#eef2f5;padding:2px 5px;border-radius:4px}
input,button{font:inherit;padding:8px 10px;margin:4px} table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #e5e8eb;padding:7px;text-align:left}
.tag{display:inline-block;background:#fff3cd;color:#654f00;padding:5px 9px;border-radius:999px}
</style></head><body>
<h1>Dietary Recall Research Migration</h1>
<p class="tag">legacy_compatibility</p>
<div class="card"><h2>Archive status</h2><div id="summary">Loading…</div></div>
<div class="card"><h2>Food search</h2><input id="q" placeholder="Food name or ID"><button onclick="searchFoods()">Search</button><div id="foods"></div></div>
<div class="card"><h2>Layer boundary</h2><p>This application reproduces recoverable Java behavior. Scientific corrections and FAO/INFOODS enrichment are intentionally reserved for a separate <code>validated_research</code> layer.</p></div>
<script>
async function load(){let r=await fetch('/api/summary');let d=await r.json();document.getElementById('summary').textContent=`${d.foods} foods; ${d.participants} participant rows; ${d.tables} legacy tables; integrity: ${d.integrity}`}
async function searchFoods(){let q=encodeURIComponent(document.getElementById('q').value);let r=await fetch('/api/foods?q='+q);let d=await r.json();let h='<table><tr><th>ID</th><th>Food</th><th>Legacy weight</th></tr>';for(let x of d){h+=`<tr><td>${x.Food_ID??''}</td><td>${escapeHtml(x.Food_Name??'')}</td><td>${x.Food_Weight??''}</td></tr>`}document.getElementById('foods').innerHTML=h+'</table>'}
function escapeHtml(s){return String(s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]))}
load();
</script></body></html>"""


class ResearchHandler(BaseHTTPRequestHandler):
    repository: LegacyRepository
    working_repository: WorkingRepository | None = None

    def _send_json(self, value: Any, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                payload = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if parsed.path == "/api/summary":
                self._send_json(
                    {
                        "mode": "legacy_compatibility",
                        "validated_research": "planned_separate_layer",
                        "tables": len(self.repository.tables()),
                        "foods": self.repository.table_count("Food"),
                        "participants": self.repository.table_count("Person"),
                        "integrity": self.repository.integrity_check(),
                        "working_copy_enabled": self.working_repository is not None,
                    }
                )
                return
            if parsed.path == "/api/foods":
                self._send_json(self.repository.search_foods(query.get("q", [""])[0], 100))
                return
            if parsed.path == "/api/participants":
                # Direct identifiers are intentionally omitted from the HTTP API.
                rows = self.repository.search_people(query.get("q", [""])[0], 100)
                self._send_json(
                    [
                        {
                            "id": row.get("Usercode"),
                            "row_id": row.get("id"),
                            "Usercode": row.get("Usercode"),
                            "Age": row.get("Age"),
                            "Activity_Level": row.get("Activity_Level"),
                            "Gender": row.get("Gender"),
                            "Life_ID": row.get("Life_ID"),
                        }
                        for row in rows
                    ]
                )
                return
            self._error(404, "Not found")
        except Exception as exc:  # local research server: return concise diagnostic
            self._error(500, f"{type(exc).__name__}: {exc}")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 1_000_000:
                self._error(413, "Request too large")
                return
            body = json.loads(self.rfile.read(size).decode("utf-8") or "{}")
            if parsed.path == "/api/calculate":
                portions = [FoodPortion(int(item["food_id"]), float(item["grams"])) for item in body.get("foods", [])]
                result = calculate_foods(self.repository, portions)
                self._send_json(
                    {
                        "mode": "legacy_compatibility",
                        "portions": [asdict(item) for item in result.portions],
                        "totals": result.totals,
                    }
                )
                return
            if parsed.path == "/api/working-recall":
                if self.working_repository is None:
                    self._error(HTTPStatus.CONFLICT, "Server was not started with a Python working copy")
                    return
                portions = [FoodPortion(int(item["food_id"]), float(item["grams"])) for item in body.get("foods", [])]
                recall_id = self.working_repository.save_recall(int(body["person_id"]), str(body["day"]), portions)
                self._send_json({"saved": True, "recall_id": recall_id}, 201)
                return
            self._error(404, "Not found")
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._error(400, f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            self._error(500, f"{type(exc).__name__}: {exc}")

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(db_path: str | Path, host: str = "127.0.0.1", port: int = 8765, working_db: str | Path | None = None) -> None:
    repository = LegacyRepository(db_path)
    handler = type("ConfiguredResearchHandler", (ResearchHandler,), {})
    handler.repository = repository
    handler.working_repository = WorkingRepository(working_db) if working_db else None
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Dietary Recall Research Migration: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
