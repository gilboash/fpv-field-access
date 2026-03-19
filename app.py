import os
import subprocess
from flask import Flask, render_template, request, jsonify, send_file, Response

app = Flask(__name__)

SD_PATH = "/media/naco/3834-6662/DCIM"
WORK_DIR = os.path.expanduser("~/picam/work")
os.makedirs(WORK_DIR, exist_ok=True)

def get_videos():
    videos = []
    for root, dirs, files in os.walk(SD_PATH):
        for f in files:
            if f.upper().endswith(('.MP4', '.MOV')) and not f.startswith('._'):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, SD_PATH)
                size = os.path.getsize(full)
                videos.append({
                    "name": f,
                    "path": rel,
                    "size_mb": round(size / 1024 / 1024, 1)
                })
    return sorted(videos, key=lambda x: x["name"], reverse=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/videos')
def list_videos():
    return jsonify(get_videos())

@app.route('/api/stream/<path:filepath>')
def stream(filepath):
    full = os.path.join(SD_PATH, filepath)
    return send_file(full, mimetype='video/mp4')

@app.route('/api/download/<path:filepath>')
def download(filepath):
    full = os.path.join(SD_PATH, filepath)
    return send_file(full, as_attachment=True)

@app.route('/api/trim', methods=['POST'])
def trim():
    data = request.json
    src = os.path.join(SD_PATH, data['path'])
    out = os.path.join(WORK_DIR, "trim_" + os.path.basename(data['path']))
    start = data.get('start', 0)
    end = data.get('end')
    cmd = ['ffmpeg', '-y', '-i', src, '-ss', str(start)]
    if end:
        cmd += ['-to', str(end)]
    cmd += ['-c', 'copy', out]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return jsonify({"error": result.stderr}), 500
    return jsonify({"output": os.path.basename(out)})

@app.route('/api/convert', methods=['POST'])
def convert():
    data = request.json
    src = os.path.join(SD_PATH, data['path'])
    out = os.path.join(WORK_DIR, "conv_" + os.path.splitext(os.path.basename(data['path']))[0] + ".mp4")
    quality = data.get('quality', 28)
    cmd = ['ffmpeg', '-y', '-i', src,
           '-vcodec', 'libx264', '-crf', str(quality),
           '-preset', 'fast', '-acodec', 'aac', out]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return jsonify({"error": result.stderr}), 500
    return jsonify({"output": os.path.basename(out)})

@app.route('/api/output/<filename>')
def get_output(filename):
    path = os.path.join(WORK_DIR, filename)
    return send_file(path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)