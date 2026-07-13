"""
engine.py — Streamlit-free video engine for GitHub Actions.

Reuses the ffmpeg logic from video_merger_app.py, but with all Streamlit calls
(st.progress / st.info / st.warning) replaced by plain print(), so the exact
same functions run on a headless CI runner.

Public functions used by run_jobs.py:
  - download_file(url, dest)
  - image_audio_to_video(...)
  - merge_videos_and_audio(...)
  - concat_videos_keep_audio(...)
  - bulk_merge_one(...)
  - build_marketing_video(...)
"""

import os
import re
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests


# ── logging shim (replaces st.info / st.warning / st.progress) ────────────
def log(msg):
    print(f"[engine] {msg}", flush=True)


def warn(msg):
    print(f"[engine][WARN] {msg}", flush=True)


# ── shared subprocess runner ──────────────────────────────
def run_ff(cmd, timeout=3600):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if r.returncode != 0:
            return False, r.stderr.decode(errors="ignore")[-800:]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


# ── shared extension sets ─────────────────────────────────
URL_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
URL_AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}
URL_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff"}
IMAGE_EXTS_EDIT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff"}


def classify_ext(url_or_path):
    ext = Path(str(url_or_path).split("?")[0]).suffix.lower()
    if ext in URL_VIDEO_EXTS:
        return "video"
    if ext in URL_IMAGE_EXTS:
        return "image"
    if ext in URL_AUDIO_EXTS:
        return "audio"
    return "unknown"


# ── download helpers ──────────────────────────────────────
def _resolve_mediafire_url(url):
    try:
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "en-US,en;q=0.5",
        }
        r = requests.get(url, headers=hdrs, timeout=20, allow_redirects=True)
        html = r.text
        patterns = [
            r'href=["\']([^"\']+)["\'][^>]*aria-label=["\']Download file',
            r'aria-label=["\']Download file["\'][^>]*href=["\']([^"\']+)',
            r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)',
            r'href=["\']([^"\']+)["\'][^>]*id=["\']downloadButton',
            r'"downloadUrl"\s*:\s*"([^"]+)"',
            r"'downloadUrl'\s*:\s*'([^']+)'",
            r'https?://download\d+\.mediafire\.com/[A-Za-z0-9/_\-\.%]+',
        ]
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                found = m.group(1) if '(' in pat and 'http' not in pat.split('(')[0] else m.group(0)
                found = found.replace('\\/', '/').rstrip('.,)"\'')
                if found.startswith('http'):
                    return found
        return None
    except Exception:
        return None


def _normalize_download_url(url):
    if "dropbox.com" in url or "dropboxusercontent.com" in url:
        url = url.replace("www.dropbox.com", "dl.dropboxusercontent.com")
        url = url.replace("dl.dropbox.com", "dl.dropboxusercontent.com")
        if "dl=0" in url:
            url = url.replace("dl=0", "dl=1")
        elif "dl=1" not in url:
            url += "&dl=1" if "?" in url else "?dl=1"
        return url
    m = re.search(r"drive\.google\.com/file/d/([^/\?]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&confirm=t&id={m.group(1)}"
    return url


def _download_with_requests(url, dest):
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*", "Accept-Encoding": "identity", "Connection": "keep-alive",
        "Referer": "https://www.dropbox.com/",
    }
    r = requests.get(url, stream=True, timeout=600, headers=HEADERS, allow_redirects=True)
    r.raise_for_status()
    ct = r.headers.get("Content-Type", "")
    if "text/html" in ct and r.headers.get("Content-Length", "0") == "21":
        raise ValueError("Server returned HTML error page")
    size = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(512 * 1024):
            f.write(chunk)
            size += len(chunk)
    if size < 1024:
        raise ValueError(f"File too small ({size} bytes)")
    return size


def _download_with_ytdlp(url, dest):
    r = subprocess.run([
        "yt-dlp", "--no-check-certificates", "--no-warnings", "--no-playlist",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", dest, url
    ], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise ValueError(f"yt-dlp failed: {r.stderr[-300:]}")
    if not os.path.exists(dest) or os.path.getsize(dest) < 1024:
        raise ValueError("yt-dlp produced no output file")
    return os.path.getsize(dest)


def _download_with_ffmpeg(url, dest):
    r = subprocess.run([
        "ffmpeg", "-y",
        "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "-headers", "Referer: https://www.dropbox.com/\r\nAccept: */*\r\n",
        "-i", url, "-c", "copy", "-movflags", "+faststart", dest
    ], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise ValueError(f"ffmpeg failed: {r.stderr[-300:]}")
    if not os.path.exists(dest) or os.path.getsize(dest) < 1024:
        raise ValueError("ffmpeg produced no output")
    return os.path.getsize(dest)


def download_file(url, dest):
    url = url.strip()
    # Local file path (or file:// URI) — just copy it. Lets jobs.json reference
    # files committed to the repo, and makes CI/self-tests possible offline.
    local = url[7:] if url.startswith("file://") else url
    if not url.lower().startswith(("http://", "https://")) and os.path.exists(local):
        try:
            shutil.copy(local, dest)
            return True, f"{os.path.getsize(dest) // 1024} KB via local"
        except Exception as e:
            return False, f"local copy: {e}"
    if "mediafire.com" in url:
        direct = _resolve_mediafire_url(url)
        if direct:
            url = direct
        else:
            return False, "Cannot resolve MediaFire URL"
    normalized = _normalize_download_url(url)
    errors = []
    for label, fn, u in [
        ("requests", _download_with_requests, normalized),
        ("ffmpeg",   _download_with_ffmpeg,   normalized),
        ("yt-dlp",   _download_with_ytdlp,    url),
    ]:
        try:
            size = fn(u, dest)
            return True, f"{size // 1024} KB via {label}"
        except Exception as e:
            errors.append(f"{label}: {str(e)[:60]}")
            if os.path.exists(dest):
                try:
                    os.remove(dest)
                except Exception:
                    pass
    return False, " | ".join(errors)


# ── ffprobe helpers ───────────────────────────────────────
def get_duration(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", path],
                           capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return None


def get_duration_accurate(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30)
        v = r.stdout.strip()
        if v and v not in ("N/A", ""):
            return float(v)
    except Exception:
        pass
    return get_duration(path)


def _has_audio_stream(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    return "audio" in (r.stdout or "")


def _scale_filter(resolution):
    if resolution == "youtube":
        return "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2"
    if resolution == "tiktok":
        return "scale=576:1024:force_original_aspect_ratio=decrease,pad=576:1024:(ow-iw)/2:(oh-ih)/2"
    return ""


# ── media dims / duration for images ──────────────────────
def _media_dims(mpath, ext):
    w, h = 0, 0
    if ext in IMAGE_EXTS_EDIT:
        try:
            from PIL import Image as PILImg, ImageOps as PILOps
            with PILImg.open(mpath) as im:
                im = PILOps.exif_transpose(im)
                w, h = im.size
        except Exception:
            pass
    if w == 0 or h == 0:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", mpath],
            capture_output=True, text=True)
        try:
            w, h = map(int, probe.stdout.strip().split(","))
        except Exception:
            w, h = 1280, 720
    MAX_DIM = 1920
    if max(w, h) > MAX_DIM:
        sc = MAX_DIM / max(w, h)
        w, h = int(w * sc), int(h * sc)
    w = max(2, w if w % 2 == 0 else w - 1)
    h = max(2, h if h % 2 == 0 else h - 1)
    return w, h


def _zoom_factor(fi, half, n_frames, zoom_max):
    if fi < half:
        return 1.0 + (zoom_max - 1.0) * (fi / max(half, 1))
    return 1.0 + (zoom_max - 1.0) * (1.0 - (fi - half) / max(n_frames - half - 1, 1))


# ── image → video (static or ken burns) ───────────────────
def image_audio_to_video(image_path, audio_path, output_path,
                         resolution="original", duration=None,
                         motion="static", fps=30, zoom_max=1.12):
    tmp = tempfile.mkdtemp()
    audio_warn = None
    if audio_path:
        if not os.path.exists(audio_path) or not _has_audio_stream(audio_path):
            audio_warn = f"invalid audio ({os.path.basename(audio_path)}) — skipped"
            audio_path = None

    aud_dur = get_duration(audio_path) if audio_path else None
    target = float(duration) if duration is not None else (aud_dur if aud_dur else 5.0)
    target = max(0.5, target)

    w, h = _media_dims(image_path, Path(image_path).suffix.lower())
    base_video = os.path.join(tmp, "base.mp4")
    scale = _scale_filter(resolution)

    if motion == "kenburns":
        try:
            from PIL import Image as PILImg, ImageOps as PILOps
        except ImportError:
            motion = "static"
        else:
            frames_dir = os.path.join(tmp, "kb")
            os.makedirs(frames_dir, exist_ok=True)
            n_frames = max(1, int(fps * target))
            half = n_frames // 2
            converted = os.path.join(tmp, "src.jpg")
            try:
                with PILImg.open(image_path) as _raw:
                    PILOps.exif_transpose(_raw).convert("RGB").resize((w, h), PILImg.LANCZOS).save(
                        converted, "JPEG", quality=95)
            except Exception:
                ok, _ = run_ff(["ffmpeg", "-y", "-i", image_path, "-frames:v", "1", "-q:v", "2", converted])
                if not ok:
                    return False, "Cannot read image"
            with PILImg.open(converted) as im:
                im = im.convert("RGB").resize((w, h), PILImg.LANCZOS)
                for fi in range(n_frames):
                    zf = _zoom_factor(fi, half, n_frames, zoom_max)
                    cw, ch = max(1, int(w / zf)), max(1, int(h / zf))
                    x0, y0 = (w - cw) // 2, (h - ch) // 2
                    im.crop((x0, y0, x0 + cw, y0 + ch)).resize((w, h), PILImg.LANCZOS).save(
                        os.path.join(frames_dir, f"f{fi:06d}.jpg"), "JPEG", quality=90)
            enc_cmd = ["ffmpeg", "-y", "-framerate", str(fps),
                       "-i", os.path.join(frames_dir, "f%06d.jpg")] + \
                      (["-vf", scale] if scale else []) + \
                      ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                       "-pix_fmt", "yuv420p", "-an", base_video]
            ok, err = run_ff(enc_cmd)
            if not ok:
                return False, f"Ken Burns encode failed: {err}"

    if motion == "static":
        vf = (scale + "," if scale else "") + "format=yuv420p"
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", image_path,
               "-t", str(target), "-r", str(fps), "-vf", vf,
               "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an", base_video]
        ok, err = run_ff(cmd)
        if not ok:
            return False, f"Image→video failed: {err}"

    if audio_path:
        aac = os.path.join(tmp, "a.aac")
        ok, err = run_ff(["ffmpeg", "-y", "-i", audio_path, "-vn", "-c:a", "aac",
                          "-b:a", "192k", "-ar", "44100", "-ac", "2", aac])
        if not ok:
            return False, f"Audio encode failed: {err}"
        cmd = ["ffmpeg", "-y", "-i", base_video, "-i", aac,
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
               "-map", "0:v:0", "-map", "1:a:0", "-shortest", output_path]
        ok, err = run_ff(cmd)
        if not ok:
            return False, f"Audio mux failed: {err}"
    else:
        shutil.copy(base_video, output_path)

    return True, (audio_warn or "OK")


# ── merge many videos + many audios → one ─────────────────
def merge_videos_and_audio(video_paths, audio_paths, output_path,
                           resolution="original", audio_mode="replace"):
    tmp = tempfile.mkdtemp()
    merged_video = os.path.join(tmp, "merged_video.mp4")
    merged_audio = os.path.join(tmp, "merged_audio.aac")
    scale = _scale_filter(resolution)

    reencoded = []
    for i, vp in enumerate(video_paths):
        out = os.path.join(tmp, f"v{i}.mp4")
        cmd = ["ffmpeg", "-y", "-i", vp] + (["-vf", scale] if scale else []) + \
              ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
               "-c:a", "aac", "-b:a", "128k", "-ar", "44100", out]
        ok, err = run_ff(cmd)
        if not ok:
            return False, f"Re-encode failed [{os.path.basename(vp)}]:\n{err}"
        reencoded.append(out)
        log(f"re-encoded video {i + 1}/{len(video_paths)}")

    txt = os.path.join(tmp, "concat.txt")
    open(txt, "w").write("\n".join(f"file '{p}'" for p in reencoded))
    log("concatenating videos")
    ok, err = run_ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", txt, "-c", "copy", merged_video])
    if not ok:
        return False, f"Concat failed:\n{err}"
    video_dur = get_duration(merged_video)

    if audio_paths:
        ra_list = []
        for i, ap in enumerate(audio_paths):
            out = os.path.join(tmp, f"ra{i}.aac")
            ok, err = run_ff(["ffmpeg", "-y", "-i", ap, "-vn", "-c:a", "aac",
                              "-b:a", "192k", "-ar", "44100", "-ac", "2", out])
            if not ok:
                return False, f"Audio re-encode failed [{os.path.basename(ap)}]:\n{err}"
            ra_list.append(out)

        atxt = os.path.join(tmp, "audio_concat.txt")
        open(atxt, "w").write("\n".join(f"file '{p}'" for p in ra_list))
        log("merging audio tracks")
        ok, err = run_ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", atxt, "-c", "copy", merged_audio])
        if not ok:
            return False, f"Audio merge failed:\n{err}"
        audio_dur = get_duration_accurate(merged_audio)

        video_for_merge = merged_video
        if audio_dur and video_dur:
            adj = os.path.join(tmp, "adjusted.mp4")
            if audio_dur > video_dur * 1.05:
                log(f"video {video_dur:.1f}s < audio {audio_dur:.1f}s — looping video")
                ok, err = run_ff(["ffmpeg", "-y", "-stream_loop", "-1", "-i", merged_video,
                                  "-t", str(audio_dur),
                                  "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an", adj])
                if ok:
                    video_for_merge = adj
            elif video_dur > audio_dur * 1.05:
                log(f"video {video_dur:.1f}s > audio {audio_dur:.1f}s — trimming video")
                ok, err = run_ff(["ffmpeg", "-y", "-i", merged_video, "-t", str(audio_dur),
                                  "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an", adj])
                if ok:
                    video_for_merge = adj

        log("combining video + audio")
        if audio_mode == "replace":
            cmd = ["ffmpeg", "-y", "-i", video_for_merge, "-i", merged_audio,
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                   "-map", "0:v:0", "-map", "1:a:0", "-shortest", output_path]
        else:
            cmd = ["ffmpeg", "-y", "-i", video_for_merge, "-i", merged_audio,
                   "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=shortest:dropout_transition=2[a]",
                   "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                   "-shortest", output_path]
    else:
        cmd = ["ffmpeg", "-y", "-i", merged_video, "-c", "copy", output_path]

    ok, err = run_ff(cmd)
    if not ok:
        return False, f"Final merge failed:\n{err}"
    return True, "Video created successfully!"


# ── concat many videos, keep each one's own audio ─────────
def concat_videos_keep_audio(video_paths, output_path, resolution="original"):
    if not video_paths:
        return False, "No videos to concat."
    tmp = tempfile.mkdtemp()
    scale = _scale_filter(resolution)
    normalized = []
    for i, vp in enumerate(video_paths):
        out = os.path.join(tmp, f"n{i:03d}.mp4")
        has_aud = _has_audio_stream(vp)
        cmd = ["ffmpeg", "-y", "-i", vp]
        if not has_aud:
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-shortest"]
        cmd += (["-vf", scale] if scale else [])
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
        if not has_aud:
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]
        cmd += [out]
        ok, err = run_ff(cmd)
        if not ok:
            return False, f"Normalize failed [{os.path.basename(vp)}]:\n{err}"
        normalized.append(out)
        log(f"normalized {i + 1}/{len(video_paths)}")

    txt = os.path.join(tmp, "concat.txt")
    with open(txt, "w") as f:
        f.write("\n".join(f"file '{p}'" for p in normalized))
    log("concatenating")
    ok, err = run_ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                      "-i", txt, "-c", "copy", "-movflags", "+faststart", output_path])
    if not ok:
        ok, err = run_ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                          "-i", txt, "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                          "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output_path])
        if not ok:
            return False, f"Concat failed:\n{err}"
    return True, "Concat done!"


# ── one video/image + one audio → one video (bulk unit) ───
def bulk_merge_one(video_path, audio_path, output_path,
                   resolution="original", audio_mode="replace"):
    tmp = tempfile.mkdtemp()
    merged_audio = os.path.join(tmp, "audio.aac")
    scale = _scale_filter(resolution)

    reenc = os.path.join(tmp, "v.mp4")
    cmd = ["ffmpeg", "-y", "-i", video_path] + (["-vf", scale] if scale else []) + \
          ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100", reenc]
    ok, err = run_ff(cmd)
    if not ok:
        return False, f"Re-encode failed: {err}"

    if audio_path:
        ok, err = run_ff(["ffmpeg", "-y", "-i", audio_path, "-vn", "-c:a", "aac",
                          "-b:a", "192k", "-ar", "44100", "-ac", "2", merged_audio])
        if not ok:
            return False, f"Audio encode failed: {err}"
        audio_dur = get_duration(merged_audio)
        video_dur = get_duration(reenc)
        adj = os.path.join(tmp, "adj.mp4")
        use_video = reenc
        if audio_dur and video_dur:
            if audio_dur > video_dur:
                run_ff(["ffmpeg", "-y", "-stream_loop", "-1", "-i", reenc, "-t", str(audio_dur),
                        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an", adj])
            else:
                run_ff(["ffmpeg", "-y", "-i", reenc, "-t", str(audio_dur),
                        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an", adj])
            use_video = adj if os.path.exists(adj) else reenc

        if audio_mode == "replace":
            cmd = ["ffmpeg", "-y", "-i", use_video, "-i", merged_audio,
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                   "-map", "0:v:0", "-map", "1:a:0", "-shortest", output_path]
        else:
            cmd = ["ffmpeg", "-y", "-i", use_video, "-i", merged_audio,
                   "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=shortest[a]",
                   "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                   "-shortest", output_path]
        ok, err = run_ff(cmd)
        if not ok:
            return False, f"Merge failed: {err}"
    else:
        shutil.copy(reenc, output_path)
    return True, "OK"


# ── marketing video (images + voiceover [+ music]) ────────
def build_marketing_video(image_paths, voice_audio, output_path,
                          resolution="tiktok", motion="kenburns", music_path=None,
                          music_volume=0.18):
    if not image_paths:
        return False, "No images."
    if not voice_audio or not os.path.exists(voice_audio) or not _has_audio_stream(voice_audio):
        return False, "No valid voiceover."
    voice_dur = get_duration_accurate(voice_audio) or get_duration(voice_audio)
    if not voice_dur or voice_dur <= 0:
        return False, "Cannot read voiceover duration."

    tmp = tempfile.mkdtemp()
    base_silent = os.path.join(tmp, "base_silent.mp4")
    if len(image_paths) == 1:
        ok, msg = image_audio_to_video(image_paths[0], None, base_silent,
                                       resolution=resolution, duration=voice_dur, motion=motion)
        if not ok:
            return False, f"Image clip failed: {msg}"
    else:
        per = max(0.8, voice_dur / len(image_paths))
        clips = []
        for i, img in enumerate(image_paths):
            clip = os.path.join(tmp, f"mkt_clip_{i:03d}.mp4")
            ok, msg = image_audio_to_video(img, None, clip,
                                           resolution=resolution, duration=per, motion=motion)
            if not ok:
                return False, f"Image clip {os.path.basename(img)} failed: {msg}"
            clips.append(clip)
            log(f"marketing clip {i + 1}/{len(image_paths)}")
        ok, msg = concat_videos_keep_audio(clips, base_silent, resolution=resolution)
        if not ok:
            return False, f"Concat clips failed: {msg}"

    voice_aac = os.path.join(tmp, "voice.aac")
    ok, err = run_ff(["ffmpeg", "-y", "-i", voice_audio, "-vn", "-c:a", "aac",
                      "-b:a", "192k", "-ar", "44100", "-ac", "2", voice_aac])
    if not ok:
        return False, f"Voiceover encode failed: {err}"

    final_audio = voice_aac
    if music_path and os.path.exists(music_path) and _has_audio_stream(music_path):
        log("mixing background music under voice")
        mixed = os.path.join(tmp, "mixed.aac")
        ok, err = run_ff([
            "ffmpeg", "-y", "-i", voice_aac, "-stream_loop", "-1", "-i", music_path,
            "-filter_complex",
            f"[1:a]volume={music_volume}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[a]",
            "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-t", str(voice_dur), mixed])
        if ok:
            final_audio = mixed
        else:
            warn(f"music mix failed, using voice only: {err[:150]}")

    ok, err = run_ff([
        "ffmpeg", "-y", "-i", base_silent, "-i", final_audio,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0", "-shortest",
        "-movflags", "+faststart", output_path])
    if not ok:
        return False, f"Mux voiceover failed: {err}"
    return True, "Marketing video done!"
