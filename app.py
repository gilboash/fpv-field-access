import os
import subprocess
import threading
import uuid
from flask import Flask, render_template, request, jsonify, send_file, Response

app = Flask(__name__)

SD_PATH = "/media/naco/3834-6662"
WORK_DIR = os.path.expanduser("~/fpv-field-access/work")
THUMB_DIR = os.path.join(WORK_DIR, "thumbs")
os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)

# job_id -> {'progress': 0-100, 'status': 'running'|'done'|'error', 'output': filename}
jobs = {}
jobs_lock = threading.Lock()

def get_videos():
    videos = []
    for root, dirs, files in os.walk(SD_PATH):
        for f in files:
            ext = os.path.splitext(f)[1].upper()
            if ext in ('.MP4', '.MOV', '.TS') and not f.startswith('._'):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, SD_PATH)
                size = os.path.getsize(full)
                videos.append({
                    "name": f,
                    "path": rel,
                    "size_mb": round(size / 1024 / 1024, 1),
                    "type": "ts" if ext == ".TS" else "video"
                })
    return sorted(videos, key=lambda x: x["name"], reverse=True)


@app.route('/api/convert', methods=['POST'])
def convert():
    data = request.json
    base = os.path.splitext(os.path.basename(data['path']))[0]
    job_id = str(uuid.uuid4())[:8]
    out = os.path.join(WORK_DIR, f"{base}_converted_{job_id}.mp4")
    tmp = os.path.join(WORK_DIR, f"{base}_converted_{job_id}_tmp.mp4")
    progress_file = os.path.join(WORK_DIR, f"progress_{job_id}.txt")
    src = os.path.join(SD_PATH, data['path'])

    # estimate duration for progress tracking
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', src],
        capture_output=True, text=True
    )
    try:
        duration = float(probe.stdout.strip())
    except:
        duration = 300  # fallback 5 min

    cmd = ['ffmpeg', '-y', '-i', src,
           '-r', '30',
           '-vf', 'scale=640:-2',
           '-c:v', 'libx264', '-crf', '28', '-preset', 'ultrafast',
           '-threads', '1',
           '-c:a', 'aac', '-b:a', '96k',
           '-movflags', '+faststart',
           '-progress', progress_file,
           tmp]

    with jobs_lock:
        jobs[job_id] = {'status': 'queued', 'progress': 0, 'output': None}

    t = threading.Thread(
        target=run_trim_job,
        args=(job_id, src, out, tmp, cmd, duration, progress_file),
        daemon=True
    )
    t.start()

    return jsonify({'job_id': job_id, 'duration': duration})

def get_thumb_name(filename):
    base = os.path.splitext(filename)[0]
    return f"{base}_thumb.jpg"

def parse_ffmpeg_progress(progress_file, duration_secs):
    try:
        with open(progress_file, 'r') as f:
            content = f.read()
        for line in reversed(content.strip().split('\n')):
            if line.startswith('out_time_ms='):
                ms = int(line.split('=')[1])
                secs = ms / 1_000_000
                pct = min(int((secs / duration_secs) * 100), 99)
                return pct
    except:
        pass
    return 0

def run_trim_job(job_id, src, out, tmp, cmd, duration_secs, progress_file):
    with jobs_lock:
        jobs[job_id]['status'] = 'running'

    result = subprocess.run(cmd, capture_output=True, text=True)

    with jobs_lock:
        if result.returncode == 0:
            os.rename(tmp, out)
            jobs[job_id]['status'] = 'done'
            jobs[job_id]['progress'] = 100
            jobs[job_id]['output'] = os.path.basename(out)
        else:
            if os.path.exists(tmp):
                os.remove(tmp)
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = result.stderr[-300:]

    if os.path.exists(progress_file):
        os.remove(progress_file)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/videos')
def list_videos():
    return jsonify(get_videos())

@app.route('/api/thumbnail/<path:filepath>')
def thumbnail(filepath):
    filename = os.path.basename(filepath)
    thumb_name = get_thumb_name(filename)
    thumb_path = os.path.join(THUMB_DIR, thumb_name)

    if not os.path.exists(thumb_path):
        src = os.path.join(SD_PATH, filepath)
        tmp = thumb_path + ".tmp.jpg"
        cmd = ['ffmpeg', '-y', '-ss', '3', '-i', src,
               '-vframes', '1', '-q:v', '5',
               '-vf', 'scale=480:-1', tmp]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            os.rename(tmp, thumb_path)
        else:
            return '', 404

    return send_file(thumb_path, mimetype='image/jpeg')

@app.route('/api/stream/<path:filepath>')
def stream(filepath):
    full = os.path.join(SD_PATH, filepath)
    file_size = os.path.getsize(full)
    range_header = request.headers.get('Range')

    if range_header:
        byte_start = 0
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
    base = os.path.splitext(os.path.basename(data['path']))[0]
    quality = data.get('quality', 'original')  # original | medium | low
    start = float(data.get('start', 0))
    duration = float(data.get('duration', 30))
    job_id = str(uuid.uuid4())[:8]

    out = os.path.join(WORK_DIR, f"trim_{base}_{quality}_{job_id}.mp4")
    tmp = os.path.join(WORK_DIR, f"trim_{base}_{quality}_{job_id}_tmp.mp4")
    progress_file = os.path.join(WORK_DIR, f"progress_{job_id}.txt")
    src = os.path.join(SD_PATH, data['path'])

    with jobs_lock:
        jobs[job_id] = {'status': 'queued', 'progress': 0, 'output': None}

    if quality == 'original':
        cmd = ['ffmpeg', '-y',
               '-ss', str(start), '-i', src,
               '-t', str(duration),
               '-c', 'copy',
               '-progress', progress_file,
               tmp]
    elif quality == 'medium':
        cmd = ['ffmpeg', '-y',
               '-ss', str(start), '-i', src,
               '-t', str(duration),
               '-r', '30',
               '-c:v', 'libx264', '-crf', '28', '-preset', 'ultrafast',
               '-threads', '1',
               '-c:a', 'aac', '-b:a', '96k',
               '-movflags', '+faststart',
               '-progress', progress_file,
               tmp]
    else:  # low
        cmd = ['ffmpeg', '-y',
               '-ss', str(start), '-i', src,
               '-t', str(duration),
               '-r', '24',
               '-vf', 'scale=640:-2',
               '-c:v', 'libx264', '-crf', '35', '-preset', 'ultrafast',
               '-threads', '1',
               '-c:a', 'aac', '-b:a', '64k',
               '-movflags', '+faststart',
               '-progress', progress_file,
               tmp]

    t = threading.Thread(
        target=run_trim_job,
        args=(job_id, src, out, tmp, cmd, duration, progress_file),
        daemon=True
    )
    t.start()

    return jsonify({'job_id': job_id})

@app.route('/api/progress/<job_id>')
def progress(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'not found'}), 404

    progress_file = os.path.join(WORK_DIR, f"progress_{job_id}.txt")
    duration = request.args.get('duration', 30, type=float)

    if job['status'] == 'running':
        pct = parse_ffmpeg_progress(progress_file, duration)
        return jsonify({'status': 'running', 'progress': pct})
    elif job['status'] == 'done':
        return jsonify({'status': 'done', 'progress': 100, 'output': job['output']})
    elif job['status'] == 'error':
        return jsonify({'status': 'error', 'error': job.get('error', 'unknown')})
    else:
        return jsonify({'status': 'queued', 'progress': 0})

@app.route('/api/output/<filename>')
def get_output(filename):
    path = os.path.join(WORK_DIR, filename)
    return send_file(path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)