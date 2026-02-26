import os
import asyncio
from collections import deque
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent

load_dotenv()

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
LANGUAGE_CODE = os.getenv("LANGUAGE_CODE", "en-US")
AWS_SAMPLE_RATE = int(os.getenv("AWS_SAMPLE_RATE", "16000"))

# 20ms silence keepalive (prevents AWS 15s timeout)
SILENCE = b"\x00" * int(AWS_SAMPLE_RATE * 0.02) * 2

app = FastAPI()

# room_id -> set of websockets
rooms: dict[str, set[WebSocket]] = {}


def _norm_room(room_id: str) -> str:
    room_id = (room_id or "").strip()
    return room_id if room_id else "demo"


async def broadcast(room_id: str, message: dict):
    connections = rooms.get(room_id, set())
    dead = []
    for ws in connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.discard(ws)


class TranscriptHandler(TranscriptResultStreamHandler):
    """
    Dedupe strategy:
    - Track recent result_ids (AWS sometimes revises finals)
    - Track last final text as a backup
    """
    def __init__(self, output_stream, room_id: str, name: str, role: str):
        super().__init__(output_stream)
        self.room_id = room_id
        self.name = name
        self.role = role
        self._seen_result_ids = deque(maxlen=200)
        self._last_text = ""

    async def handle_transcript_event(self, event: TranscriptEvent):
        for result in event.transcript.results:
            if result.is_partial:
                continue

            # Dedupe by result_id when present
            rid = getattr(result, "result_id", None)
            if rid and rid in self._seen_result_ids:
                continue
            if rid:
                self._seen_result_ids.append(rid)

            if not result.alternatives:
                continue

            text = (result.alternatives[0].transcript or "").strip()
            if not text:
                continue

            # Backup dedupe by exact text (helps with some revisions)
            if text == self._last_text:
                continue
            self._last_text = text

            print(f"[{self.room_id}] {self.name} ({self.role}): {text}")

            await broadcast(self.room_id, {
                "type": "transcript",
                "name": self.name,
                "role": self.role,
                "text": text
            })


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    room_id = _norm_room(room_id)
    await websocket.accept()

    # First message must be user info
    user_info = await websocket.receive_json()
    name = (user_info.get("name") or "Unknown").strip()[:40]
    role = (user_info.get("role") or "participant").strip().lower()[:20]

    # Keep roles simple for demo
    if role not in {"interviewer", "candidate"}:
        role = "participant"

    rooms.setdefault(room_id, set()).add(websocket)

    audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=300)

    client = TranscribeStreamingClient(region=REGION)

    stream = await client.start_stream_transcription(
        language_code=LANGUAGE_CODE,
        media_sample_rate_hz=AWS_SAMPLE_RATE,
        media_encoding="pcm",
    )

    handler = TranscriptHandler(stream.output_stream, room_id, name, role)

    async def send_audio():
        while True:
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                await stream.input_stream.send_audio_event(audio_chunk=chunk)
            except asyncio.TimeoutError:
                # keepalive silence (prevents AWS timeout)
                await stream.input_stream.send_audio_event(audio_chunk=SILENCE)

    send_task = asyncio.create_task(send_audio())
    receive_task = asyncio.create_task(handler.handle_events())

    try:
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                break

            if message.get("bytes") is not None:
                audio_bytes = message["bytes"]

                # Drop oldest if queue is full (prevents lag)
                if audio_queue.full():
                    try:
                        audio_queue.get_nowait()
                    except Exception:
                        pass

                await audio_queue.put(audio_bytes)

    finally:
        # remove socket
        conns = rooms.get(room_id, set())
        conns.discard(websocket)
        if not conns:
            rooms.pop(room_id, None)

        # close AWS stream cleanly
        try:
            await stream.input_stream.end_stream()
        except Exception:
            pass

        send_task.cancel()
        receive_task.cancel()
        try:
            await send_task
        except Exception:
            pass
        try:
            await receive_task
        except Exception:
            pass


@app.get("/")
async def get():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Live Interview Transcription</title>
<style>
body {
    font-family: 'Segoe UI', sans-serif;
    margin: 0;
    background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
    color: white;
}
.container {
    max-width: 980px;
    margin: 50px auto;
    background: rgba(255,255,255,0.06);
    backdrop-filter: blur(10px);
    border-radius: 16px;
    padding: 26px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.35);
}
h1 { text-align: center; margin: 0 0 18px 0; }
.sub { text-align:center; opacity:0.85; margin-bottom: 18px; font-size: 14px; }

.controls {
    display: grid;
    grid-template-columns: 1fr 1fr 160px;
    gap: 10px;
    align-items: center;
    margin-bottom: 10px;
}
@media (max-width: 700px) {
  .controls { grid-template-columns: 1fr; }
}

input, select {
    padding: 11px 12px;
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.15);
    background: rgba(0,0,0,0.18);
    color: white;
    outline: none;
    font-size: 14px;
}

button {
    padding: 11px 14px;
    border-radius: 10px;
    border: none;
    background: linear-gradient(to right, #0072ff, #00c6ff);
    color: white;
    font-weight: 700;
    cursor: pointer;
    transition: 0.2s;
}
button:hover { transform: translateY(-1px); }
button:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }

.meta {
    display:flex;
    gap: 10px;
    justify-content: space-between;
    align-items:center;
    flex-wrap: wrap;
    margin: 10px 0 16px;
    opacity: 0.9;
    font-size: 13px;
}
.pill {
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,0.12);
}

.transcripts {
    max-height: 420px;
    overflow-y: auto;
    padding-right: 8px;
}
.message {
    padding: 12px;
    margin-bottom: 10px;
    border-radius: 12px;
    border: 1px solid rgba(255,255,255,0.10);
}
.interviewer { background: rgba(0, 114, 255, 0.22); }
.candidate   { background: rgba(0, 255, 170, 0.18); }
.participant { background: rgba(255, 255, 255, 0.10); }

small { opacity: 0.8; }
a { color: #8fe9ff; }
</style>
</head>
<body>

<div class="container">
    <h1>🎙 Live Interview Transcription</h1>
    

    <div class="controls">
        <input id="name" placeholder="Your Name (required)" />
        <select id="role">
            <option value="interviewer">Interviewer</option>
            <option value="candidate">Candidate</option>
        </select>
        <button id="startBtn" onclick="start()">Start</button>
    </div>

    <div class="meta">
        <div class="pill">Room: <b id="roomLabel"></b></div>
        <div class="pill">Status: <b id="status">Not connected</b></div>
        <div class="pill">
          Share link:
          <a id="shareLink" href="#" target="_blank" rel="noopener">copy</a>
        </div>
    </div>

    <div class="transcripts" id="transcripts"></div>
</div>

<script>
let socket;
let audioContext;
let processor;
let input;
let started = false;

function getRoom() {
  const params = new URLSearchParams(window.location.search);
  return params.get("room") || "demo";
}

function setShareLink(room) {
  const url = window.location.origin + "/?room=" + encodeURIComponent(room);
  const a = document.getElementById("shareLink");
  a.href = url;
  a.textContent = url + " (click to open / copy)";
  a.onclick = async (e) => {
    e.preventDefault();
    try {
      await navigator.clipboard.writeText(url);
      a.textContent = url + " (copied)";
      setTimeout(() => a.textContent = url + " (click to open / copy)", 1200);
    } catch {
      window.open(url, "_blank");
    }
  };
}

function start() {
    if (started) return;
    const name = document.getElementById("name").value.trim();
    const role = document.getElementById("role").value;

    if (!name) {
      alert("Please enter your name.");
      return;
    }

    started = true;
    document.getElementById("startBtn").disabled = true;

    const room = getRoom();
    document.getElementById("roomLabel").innerText = room;
    setShareLink(room);

    const protocol = window.location.protocol === "https:" ? "wss://" : "ws://";
    socket = new WebSocket(protocol + window.location.host + "/ws/" + encodeURIComponent(room));

    socket.onopen = () => {
        document.getElementById("status").innerText = "Connected";
        socket.send(JSON.stringify({name: name, role: role}));
        startMic();
    };

    socket.onclose = () => {
        document.getElementById("status").innerText = "Disconnected";
    };

    socket.onerror = () => {
        document.getElementById("status").innerText = "WebSocket error";
    };

    socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "transcript") {
            const div = document.getElementById("transcripts");
            const msg = document.createElement("div");
            msg.className = "message " + (data.role || "participant");
            msg.innerHTML = "<b>" + escapeHtml(data.name) + "</b> <small>(" + escapeHtml(data.role) + ")</small><br>" + escapeHtml(data.text);
            div.appendChild(msg);
            div.scrollTop = div.scrollHeight;
        }
    };
}

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

async function startMic() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      // NOTE: browser may ignore sampleRate; still works for demo in practice.
      audioContext = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 16000});
      input = audioContext.createMediaStreamSource(stream);
      processor = audioContext.createScriptProcessor(4096, 1, 1);

      processor.onaudioprocess = (e) => {
          if (!socket || socket.readyState !== 1) return;
          const inputData = e.inputBuffer.getChannelData(0);
          const pcm = new Int16Array(inputData.length);
          for (let i = 0; i < inputData.length; i++) {
              let v = inputData[i];
              if (v > 1) v = 1;
              if (v < -1) v = -1;
              pcm[i] = v * 0x7fff;
          }
          socket.send(pcm.buffer);
      };

      input.connect(processor);
      processor.connect(audioContext.destination);

      document.getElementById("status").innerText = "Mic streaming";
    } catch (err) {
      document.getElementById("status").innerText = "Mic permission blocked";
      alert("Microphone access failed. Please allow mic permission and reload.");
      console.error(err);
    }
}

// Init UI labels early
(function init(){
  const room = getRoom();
  document.getElementById("roomLabel").innerText = room;
  setShareLink(room);
})();
</script>

</body>
</html>
""")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)