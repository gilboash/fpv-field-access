import os
import subprocess
import threading
import json
from flask import Flask, render_template, request, jsonify, send_file
from datetime import datetime

app = Flask(__name__)

SD_PATH = "/media/naco/3834-6662"
WORK_DIR = os.path.expanduser("~/fpv-field-access/work")
PROXY_DIR = os.path.join(WORK_DIR, "proxies")
os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(PROXY_DIR, exist_ok=True)

# Track proxy generation status
proxy_status = {}  # filename -> 'queued' | 'processing' | 'done' | 'error'
proxy_lock = threading.Lock()

def get_videos():
    videos = []
    for root, dirs, files in os.walk(SD_PATH):
        for f in files:
            if f.upper().endswith(('.MP4', '.MOV')) and not f.startswith('._'):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, SD_PATH)
                size = os.path.getsize(full)
                proxy = get_proxy_name(f)
                with proxy_lock:
                    status = proxy_status.get(f, 'queued')
                videos.append({
                    "name": f,
                    "path": rel,
                    "size_mb": round(size / 1024 / 1024, 1),
                    "proxy_status": status,
                    "has_proxy": os.path.exists(os.path.join(PROXY_DIR, proxy))
                })
    return sorted(videos, key=lambda x: x["name"], reverse=True)

def get_proxy_name(filename):
    base = os.path.splitext(filename)[0]
    return f"{base}_proxy.mp4"

def generate_proxy(filepath, filename):
    proxy_name = get_proxy_name(filename)
    proxy_path = os.path.join(PROXY_DIR, proxy_name)

    if os.path.exists(proxy_path):
        with proxy_lock:
            proxy_status[filename] = 'done'
        return

    with proxy_lock:
        proxy_status[filename] = 'processing'

    cmd = [
        'ffmpeg', '-y',
        '-i', filepath,
        '-vf', 'scale=854:480:force_original_aspect_ratio=decrease',
        '-c:v', 'libx264',
        '-crf', '28',
        '-preset', 'ultrafast',
        '-threads', '1',
        '-c:a', 'aac',
        '-b:a', '96k',
        '-movflags', '+faststart',
        proxy_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    with proxy_lock:
        if result.returncode == 0:
            proxy_status[filename] = 'done'
        else:
            proxy_status[filename] = 'error'

def start_proxy_worker():
    videos = get_videos()
    for v in videos:
        filename = v['name']
        proxy_name = get_proxy_name(filename)
        proxy_path = os.path.join(PROXY_DIR, proxy_name)
        if os.path.exists(proxy_path):
            with proxy_lock:
                proxy_status[filename] = 'done'
            continue
        with proxy_lock:
            proxy_status[filename] = 'queued'

    for v in videos:
        filename = v['name']
        proxy_name = get_proxy_name(filename)
        proxy_path = os.path.join(PROXY_DIR, proxy_name)
        if not os.path.exists(proxy_path):
            full = os.path.join(SD_PATH, v['path'])
            t = threading.Thread(target=generate_proxy, args=(full, filename), daemon=True)
            t.start()
            t.join()  # process one at a time, Pi Zero can't handle parallel ffmpeg

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/videos')
def list_videos():
    return jsonify(get_videos())

@app.route('/api/stream/<path:filepath>')
def stream(filepath):
    filename = os.path.basename(filepath)
    proxy_name = get_proxy_name(filename)
    proxy_path = os.path.join(PROXY_DIR, proxy_name)
    if os.path.exists(proxy_path):
        return send_file(proxy_path, mimetype='video/mp4')
    full = os.path.join(SD_PATH, filepath)
    return send_file(full, mimetype='video/mp4')

@app.route('/api/download/original/<path:filepath>')
def download_original(filepath):
    full = os.path.join(SD_PATH, filepath)
    return send_file(full, as_attachment=True)

@app.route('/api/download/compressed/<path:filepath>')
def download_compressed(filepath):
    filename = os.path.basename(filepath)
    proxy_name = get_proxy_name(filename)
    proxy_path = os.path.join(PROXY_DIR, proxy_name)
    if os.path.exists(proxy_path):
        return send_file(proxy_path, as_attachment=True)
    return jsonify({"error": "Proxy not ready yet"}), 404

@app.route('/api/trim', methods=['POST'])
def trim():
    data = request.json
    src = os.path.join(SD_PATH, data['path'])
    out = os.path.join(WORK_DIR, "trim_" + os.path.basename(data['path']))
    start = data.get('start', 0)
    end = data.get('end')
    cmd = ['ffmpeg', '-y', '-ss', str(start), '-i', src]
    if end and float(end) > float(start):
        duration = float(end) - float(start)
        cmd += ['-t', str(duration)]
    cmd += [
        '-vf', 'scale=854:480:force_original_aspect_ratio=decrease',
        '-c:v', 'libx264',
        '-crf', '28',
        '-preset', 'ultrafast',
        '-threads', '1',
        '-c:a', 'aac',
        '-b:a', '96k',
        '-movflags', '+faststart',
        out
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return jsonify({"error": result.stderr}), 500
    return jsonify({"output": os.path.basename(out)})

@app.route('/api/output/<filename>')
def get_output(filename):
    path = os.path.join(WORK_DIR, filename)
    return send_file(path, as_attachment=True)

@app.route('/api/status')
def status():
    with proxy_lock:
        return jsonify(dict(proxy_status))

if __name__ == '__main__':
    print("Starting proxy worker in background...")
    worker = threading.Thread(target=start_proxy_worker, daemon=True)
    worker.start()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)