import os
import subprocess
from flask import Flask, render_template, request, jsonify, send_file, Response

app = Flask(__name__)

SD_PATH = "/media/naco/3834-6662"
WORK_DIR = os.path.expanduser("~/fpv-field-access/work")
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
                    "size_mb": round(size / 1024 / 1024, 1),
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
    file_size = os.path.getsize(full)
    range_header = request.headers.get('Range')

    if range_header:
        byte_start, byte_end = 0, None
        match = range_header.replace('bytes=', '').split('-')
        byte_start = int(match[0])
        byte_end = int(match[1]) if match[1] else file_size - 1
        length = byte_end - byte_start + 1

        def generate():
            with open(full, 'rb') as f:
                f.seek(byte_start)
                remaining = length
                while remaining:
                    chunk = f.read(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        rv = Response(generate(), status=206, mimetype='video/mp4')
        rv.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
        rv.headers['Accept-Ranges'] = 'bytes'
        rv.headers['Content-Length'] = length
        return rv

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
    tmp = out + ".tmp"
    start = data.get('start', 0)
    end = data.get('end')

    if end and float(end) <= float(start):
        return jsonify({"error": "End must be greater than start"}), 400

    cmd = ['ffmpeg', '-y', '-ss', str(start), '-i', src]
    if end:
        duration = float(end) - float(start)
        cmd += ['-t', str(duration)]
    cmd += ['-c', 'copy', tmp]  # no re-encode, instant cut

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        os.rename(tmp, out)
        return jsonify({"output": os.path.basename(out)})
    if os.path.exists(tmp):
        os.remove(tmp)
    return jsonify({"error": result.stderr[-500:]}), 500

@app.route('/api/output/<filename>')
def get_output(filename):
    path = os.path.join(WORK_DIR, filename)
    return send_file(path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)