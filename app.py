import os
import subprocess
import threading
import uuid
from flask import Flask, render_template, request, jsonify, send_file, Response

app = Flask(__name__)

WORK_DIR = os.path.expanduser("~/fpv-field-access/work")
THUMB_DIR = os.path.join(WORK_DIR, "thumbs")
os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)

jobs = {}
jobs_lock = threading.Lock()

convert_queue = []
convert_queue_lock = threading.Lock()
queue_worker_running = False

thumb_semaphore = threading.Semaphore(1)
thumb_paused = False
thumb_resume_timer = None

def detect_hw_encoder():
    test_cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'color=black:size=320x240:rate=10',
        '-t', '1',
        '-c:v', 'h264_v4l2m2m',
        '-b:v', '1M',
        '-pix_fmt', 'yuv420p',
        '-f', 'null', '-'
    ]
    try:
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print("Hardware encoder detected and working: h264_v4l2m2m")
            return 'h264_v4l2m2m'
    except:
        pass
    print("No hardware encoder found, using software (libx264)")
    return 'libx264'

HW_ENCODER = detect_hw_encoder()

def get_sd_path():
    media_path = "/media/naco"
    try:
        for mount in sorted(os.listdir(media_path)):
            full = os.path.join(media_path, mount)
            if os.path.ismount(full):
                return full
    except:
        pass
    return None

def cleanup_work_dir():
    for f in os.listdir(WORK_DIR):
        if f.startswith('trim_') or f.startswith('progress_') or f.endswith('_converted.mp4'):
            try:
                os.remove(os.path.join(WORK_DIR, f))
            except:
                pass
    print("Work directory cleaned up")

def get_free_space(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize

def get_videos(sd_path):
    videos = []
    for root, dirs, files in os.walk(sd_path):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            ext = os.path.splitext(f)[1].upper()
            if ext in ('.MP4', '.MOV', '.TS') and not f.startswith('._') and not f.startswith('.'):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, sd_path)
                size = os.path.getsize(full)
                is_ts = ext == '.TS'
                converted_exists = False
                conv_filename = None
                if is_ts:
                    base = os.path.splitext(f)[0]
                    conv_filename = f"{base}_converted.mp4"
                    converted_exists = os.path.exists(
                        os.path.join(WORK_DIR, conv_filename)
                    )
                videos.append({
                    "name": f,
                    "path": rel,
                    "size_mb": round(size / 1024 / 1024, 1),
                    "type": "ts" if is_ts else "video",
                    "converted": converted_exists,
                    "converted_file": conv_filename if converted_exists else None
                })
    return sorted(videos, key=lambda x: x["name"], reverse=True)

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

def run_convert_to_sd(job_id, src, out, cmd, duration, progress_file):
    global thumb_paused
    with jobs_lock:
        jobs[job_id]['status'] = 'running'

    if not os.path.exists(src):
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = 'SD card not accessible'
        thumb_paused = False
        return

    result = subprocess.run(cmd, capture_output=True, text=True)

    # treat as success if output exists and is >1MB (handles SD card read hiccups)
    output_ok = os.path.exists(out) and os.path.getsize(out) > 1024 * 1024

    with jobs_lock:
        if output_ok:
            jobs[job_id]['status'] = 'done'
            jobs[job_id]['progress'] = 100
            jobs[job_id]['output'] = os.path.basename(out)
        else:
            try:
                if os.path.exists(out):
                    os.remove(out)
            except:
                pass
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = 'SD card lost during conversion' if not os.path.exists(src) else result.stderr[-300:]
    if os.path.exists(progress_file):
        os.remove(progress_file)
    thumb_paused = False

def queue_worker():
    global queue_worker_running
    try:
        while True:
            with convert_queue_lock:
                if not convert_queue:
                    queue_worker_running = False
                    return
                job = convert_queue.pop(0)
            run_convert_to_sd(
                job['job_id'], job['src'], job['out'],
                job['cmd'], job['duration'], job['progress_file']
            )
    except Exception as e:
        print(f"Queue worker error: {e}")
        with convert_queue_lock:
            queue_worker_running = False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/videos')
def list_videos():
    sd = get_sd_path()
    if not sd:
        return jsonify([])
    return jsonify(get_videos(sd))

@app.route('/api/thumbnail/<path:filepath>')
def thumbnail(filepath):
    global thumb_paused
    if thumb_paused:
        return '', 503

    sd = get_sd_path()
    if not sd:
        return '', 404

    filename = os.path.basename(filepath)
    thumb_name = get_thumb_name(filename)
    thumb_path = os.path.join(THUMB_DIR, thumb_name)

    if os.path.exists(thumb_path):
        return send_file(thumb_path, mimetype='image/jpeg')

    with thumb_semaphore:
        if thumb_paused:
            return '', 503
        if os.path.exists(thumb_path):
            return send_file(thumb_path, mimetype='image/jpeg')

        src = os.path.join(sd, filepath)
        tmp = thumb_path + ".tmp.jpg"
        cmd = [
            'ffmpeg', '-y',
            '-ss', '0',
            '-i', src,
            '-vframes', '1',
            '-q:v', '10',
            '-vf', 'scale=160:-1',
            tmp
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            os.rename(tmp, thumb_path)
            return send_file(thumb_path, mimetype='image/jpeg')

    return '', 404

def make_stream_response(full_path, range_header):
    file_size = os.path.getsize(full_path)
    if range_header:
        match = range_header.replace('bytes=', '').split('-')
        byte_start = int(match[0])
        byte_end = int(match[1]) if match[1] else min(byte_start + 10*1024*1024, file_size - 1)
        length = byte_end - byte_start + 1

        def generate():
            with open(full_path, 'rb') as f:
                f.seek(byte_start)
                remaining = length
                while remaining:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        rv = Response(generate(), status=206, mimetype='video/mp4', direct_passthrough=True)
        rv.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
        rv.headers['Accept-Ranges'] = 'bytes'
        rv.headers['Content-Length'] = length
        return rv

    return send_file(full_path, mimetype='video/mp4')

def pause_thumbs_temporarily():
    global thumb_paused, thumb_resume_timer
    thumb_paused = True
    if thumb_resume_timer:
        thumb_resume_timer.cancel()
    def resume_thumbs():
        global thumb_paused
        thumb_paused = False
    thumb_resume_timer = threading.Timer(30, resume_thumbs)
    thumb_resume_timer.start()

@app.route('/api/stream/<path:filepath>')
def stream(filepath):
    pause_thumbs_temporarily()
    sd = get_sd_path()
    if not sd:
        return '', 404
    full = os.path.join(sd, filepath)
    return make_stream_response(full, request.headers.get('Range'))

@app.route('/api/stream_converted/<filename>')
def stream_converted(filename):
    pause_thumbs_temporarily()
    full = os.path.join(WORK_DIR, filename)
    if not os.path.exists(full):
        return '', 404
    return make_stream_response(full, request.headers.get('Range'))

@app.route('/api/download/<path:filepath>')
def download(filepath):
    sd = get_sd_path()
    if not sd:
        return '', 404
    full = os.path.join(sd, filepath)
    return send_file(full, as_attachment=True)

@app.route('/api/download_converted/<filename>')
def download_converted(filename):
    full = os.path.join(WORK_DIR, filename)
    if not os.path.exists(full):
        return '', 404
    return send_file(full, as_attachment=True)

@app.route('/api/trim', methods=['POST'])
def trim():
    sd = get_sd_path()
    if not sd:
        return jsonify({'error': 'No SD card found'}), 404

    data = request.json
    base = os.path.splitext(os.path.basename(data['path']))[0]
    quality = data.get('quality', 'original')
    start = float(data.get('start', 0))
    duration = float(data.get('duration', 30))
    job_id = str(uuid.uuid4())[:8]
    out = os.path.join(WORK_DIR, f"trim_{base}_{quality}_{job_id}.mp4")
    tmp = os.path.join(WORK_DIR, f"trim_{base}_{quality}_{job_id}_tmp.mp4")
    progress_file = os.path.join(WORK_DIR, f"progress_{job_id}.txt")
    src = os.path.join(sd, data['path'])

    if quality == 'original':
        cmd = ['ffmpeg', '-y', 
               '-readrate', '4.0',
               '-ss', str(start), '-i', src,
               '-t', str(duration), '-c', 'copy',
               '-progress', progress_file, tmp]
    elif quality == 'medium':
        if HW_ENCODER == 'h264_v4l2m2m':
            cmd = ['ffmpeg', '-y',
                '-readrate', '4.0',
                '-ss', str(start), '-i', src,
                '-t', str(duration), '-r', '30',
                '-vf', 'scale=1280:-2',
                '-pix_fmt', 'yuv420p',
                '-c:v', 'h264_v4l2m2m', '-b:v', '2M',
                '-c:a', 'aac', '-b:a', '96k',
                '-max_muxing_queue_size', '128',
                '-movflags', '+faststart',
                '-progress', progress_file, tmp]
        else:
            cmd = ['ffmpeg', '-y',
                '-readrate', '2.0',
                '-ss', str(start), '-i', src,
                '-t', str(duration), '-r', '24',
                '-threads', '1',           # single thread only
                '-vf', 'scale=854:-2',     # smaller scale
                '-c:v', 'libx264', '-crf', '32', '-preset', 'ultrafast',
                '-c:a', 'aac', '-b:a', '96k',
                '-max_muxing_queue_size', '128',
                '-movflags', '+faststart',
                '-progress', progress_file, tmp]
    else:  # low
        if HW_ENCODER == 'h264_v4l2m2m':
            cmd = ['ffmpeg', '-y',
                '-readrate', '4.0',
                '-ss', str(start), '-i', src,
                '-t', str(duration), '-r', '24',
                '-vf', 'scale=640:-2',
                '-pix_fmt', 'yuv420p',
                '-c:v', 'h264_v4l2m2m', '-b:v', '1M',
                '-c:a', 'aac', '-b:a', '64k',
                '-max_muxing_queue_size', '128',
                '-movflags', '+faststart',
                '-progress', progress_file, tmp]
        else:
            cmd = ['ffmpeg', '-y',
                '-readrate', '2.0',
                '-ss', str(start), '-i', src,
                '-t', str(duration), '-r', '24',
                '-threads', '1',
                '-vf', 'scale=640:-2',
                '-c:v', 'libx264', '-crf', '35', '-preset', 'ultrafast',
                '-c:a', 'aac', '-b:a', '64k',
                '-max_muxing_queue_size', '128',
                '-movflags', '+faststart',
                '-progress', progress_file, tmp]

    with jobs_lock:
        jobs[job_id] = {'status': 'queued', 'progress': 0, 'output': None}

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

@app.route('/api/convert_queue', methods=['POST'])
def add_to_convert_queue():
    global queue_worker_running, thumb_paused
    thumb_paused = True

    sd = get_sd_path()
    if not sd:
        return jsonify({'error': 'No SD card found'}), 404

    data = request.json
    paths = data.get('paths', [])
    job_ids = []

    for path in paths:
        src = os.path.join(sd, path)
        base = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(WORK_DIR, f"{base}_converted.mp4")
        job_id = str(uuid.uuid4())[:8]
        progress_file = os.path.join(WORK_DIR, f"progress_{job_id}.txt")

        # check Pi local storage has enough space
        src_size = os.path.getsize(src)
        free_space = get_free_space(WORK_DIR)
        if free_space < src_size * 1.1:
            free_mb = round(free_space / 1024 / 1024)
            needed_mb = round(src_size * 1.1 / 1024 / 1024)
            job_ids.append({
                'job_id': job_id,
                'path': path,
                'error': f"Not enough local space — need {needed_mb} MB, {free_mb} MB free"
            })
            continue

        cmd = ['ffmpeg', '-y', 
               '-readrate', '4.0',    # read at 4x speed, don't saturate USB
               '-i', src,
               '-c', 'copy',
               '-tag:v', 'hvc1',
               '-movflags', '+faststart',
               '-progress', progress_file,
               out]

        with jobs_lock:
            jobs[job_id] = {'status': 'queued', 'progress': 0, 'output': None, 'path': path}

        with convert_queue_lock:
            convert_queue.append({
                'job_id': job_id,
                'src': src,
                'out': out,
                'cmd': cmd,
                'duration': 300,
                'progress_file': progress_file
            })

        job_ids.append({'job_id': job_id, 'path': path})

    with convert_queue_lock:
        if not queue_worker_running and convert_queue:
            queue_worker_running = True
            t = threading.Thread(target=queue_worker, daemon=True)
            t.start()

    return jsonify({'jobs': job_ids})

@app.route('/api/queue_status')
def queue_status():
    with jobs_lock:
        return jsonify(dict(jobs))

@app.route('/api/conversion_busy')
def conversion_busy():
    with convert_queue_lock:
        busy = queue_worker_running or len(convert_queue) > 0
    return jsonify({'busy': busy})

if __name__ == '__main__':
    cleanup_work_dir()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)