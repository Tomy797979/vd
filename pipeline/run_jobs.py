"""
run_jobs.py — read jobs.json, render each job with engine.py, upload to Dropbox.

Run locally:      python pipeline/run_jobs.py jobs.json
Run in Actions:   same, triggered by the workflow.

jobs.json shape (see jobs.example.json for full examples):
{
  "dropbox_folder": "/Videos",
  "jobs": [
    {
      "type": "merge",           # merge | concat | bulk | marketing
      "output": "final.mp4",
      "resolution": "youtube",   # original | youtube | tiktok
      "audio_mode": "replace",   # replace | mix   (merge/bulk)
      "image_duration": 5.0,     # seconds per image (merge with images)
      "image_motion": "kenburns",# static | kenburns
      "videos": ["https://...", "https://..."],   # video/image URLs in order
      "audios": ["https://..."],                  # audio URLs (merge)
      "pairs": [ {"media":"https://..a.jpg","audio":"https://..a.mp3"} ], # bulk
      "images": ["https://..1.jpg"],              # marketing
      "voice":  "https://..voice.mp3",            # marketing
      "music":  "https://..bg.mp3",               # marketing (optional)
      "music_volume": 0.18
    }
  ]
}
"""

import os
import sys
import json
import tempfile
from pathlib import Path

import engine
import dropbox_util as dbx


OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "outputs")


def _dl(url, dest_dir, i, kind_hint=""):
    ext = Path(str(url).split("?")[0]).suffix.lower() or (kind_hint or ".bin")
    dest = os.path.join(dest_dir, f"in_{i:03d}{ext}")
    ok, info = engine.download_file(url, dest)
    if not ok:
        print(f"  [download FAILED] {url[:70]} -> {info}", flush=True)
        return None
    print(f"  [downloaded] {os.path.basename(dest)} ({info})", flush=True)
    return dest


def _prep_video_or_image(url, work, i, resolution, img_dur, img_motion):
    """Return a local video path. Images are converted to a silent clip."""
    kind = engine.classify_ext(url)
    path = _dl(url, work, i, ".jpg" if kind == "image" else ".mp4")
    if not path:
        return None
    if kind == "image":
        clip = os.path.join(work, f"imgclip_{i:03d}.mp4")
        ok, msg = engine.image_audio_to_video(
            path, None, clip, resolution=resolution,
            duration=img_dur, motion=img_motion)
        if not ok:
            print(f"  [image->clip FAILED] {msg}", flush=True)
            return None
        return clip
    return path


def run_merge(job, work, out_path):
    res = job.get("resolution", "original")
    img_dur = float(job.get("image_duration", 5.0))
    img_motion = job.get("image_motion", "static")
    audio_mode = job.get("audio_mode", "replace")

    local_videos = []
    for i, u in enumerate(job.get("videos", [])):
        p = _prep_video_or_image(u, work, i, res, img_dur, img_motion)
        if p:
            local_videos.append(p)
    local_audios = []
    for i, u in enumerate(job.get("audios", [])):
        p = _dl(u, work, 900 + i, ".mp3")
        if p:
            local_audios.append(p)

    if not local_videos:
        return False, "No videos/images downloaded."
    return engine.merge_videos_and_audio(local_videos, local_audios, out_path,
                                         resolution=res, audio_mode=audio_mode)


def run_concat(job, work, out_path):
    res = job.get("resolution", "original")
    local = []
    for i, u in enumerate(job.get("videos", [])):
        p = _dl(u, work, i, ".mp4")
        if p:
            local.append(p)
    if not local:
        return False, "No videos downloaded."
    return engine.concat_videos_keep_audio(local, out_path, resolution=res)


def run_bulk(job, work, out_path):
    """Bulk produces MANY outputs; returns list of (path, name, msg)."""
    res = job.get("resolution", "original")
    img_dur = job.get("image_duration")  # None => match audio
    img_motion = job.get("image_motion", "static")
    audio_mode = job.get("audio_mode", "replace")
    prefix = job.get("prefix", "bulk_video")

    results = []
    for idx, pair in enumerate(job.get("pairs", [])):
        media_url = pair.get("media") or pair.get("video")
        audio_url = pair.get("audio")
        name = f"{prefix}_{idx + 1}.mp4"
        this_out = os.path.join(os.path.dirname(out_path), name)
        print(f" -- bulk pair {idx + 1}: {name}", flush=True)

        mpath = _dl(media_url, work, 1000 + idx, ".mp4")
        apath = _dl(audio_url, work, 2000 + idx, ".mp3") if audio_url else None
        if not mpath:
            results.append((None, name, "media download failed"))
            continue

        if engine.classify_ext(media_url) == "image":
            ok, msg = engine.image_audio_to_video(
                mpath, apath, this_out, resolution=res,
                duration=(float(img_dur) if img_dur else None), motion=img_motion)
        else:
            ok, msg = engine.bulk_merge_one(mpath, apath, this_out,
                                            resolution=res, audio_mode=audio_mode)
        results.append((this_out if ok else None, name, msg))
    return results


def run_marketing(job, work, out_path):
    res = job.get("resolution", "tiktok")
    motion = job.get("image_motion", "kenburns")
    music_vol = float(job.get("music_volume", 0.18))

    images = []
    for i, u in enumerate(job.get("images", [])):
        p = _dl(u, work, i, ".jpg")
        if p:
            images.append(p)
    voice = _dl(job["voice"], work, 800, ".mp3") if job.get("voice") else None
    music = _dl(job["music"], work, 801, ".mp3") if job.get("music") else None
    if not images:
        return False, "No images downloaded."
    if not voice:
        return False, "No voiceover downloaded."
    return engine.build_marketing_video(images, voice, out_path,
                                        resolution=res, motion=motion,
                                        music_path=music, music_volume=music_vol)


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "jobs.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    default_folder = cfg.get("dropbox_folder", "/")

    token, tok_msg = dbx.get_access_token()
    if token:
        print("[dropbox] connected", flush=True)
    else:
        print(f"[dropbox] {tok_msg} — outputs will be artifacts only", flush=True)

    produced = []   # (final_path, name, dropbox_folder)
    failures = []

    for ji, job in enumerate(cfg.get("jobs", [])):
        jtype = job.get("type", "merge")
        out_name = job.get("output", f"job_{ji + 1}.mp4")
        folder = job.get("dropbox_folder", default_folder)
        work = tempfile.mkdtemp()
        out_path = os.path.join(OUTPUT_DIR, out_name)
        print(f"\n=== JOB {ji + 1} [{jtype}] -> {out_name} ===", flush=True)

        try:
            if jtype == "merge":
                ok, msg = run_merge(job, work, out_path)
                if ok:
                    produced.append((out_path, out_name, folder))
                else:
                    failures.append((out_name, msg))
            elif jtype == "concat":
                ok, msg = run_concat(job, work, out_path)
                if ok:
                    produced.append((out_path, out_name, folder))
                else:
                    failures.append((out_name, msg))
            elif jtype == "marketing":
                ok, msg = run_marketing(job, work, out_path)
                if ok:
                    produced.append((out_path, out_name, folder))
                else:
                    failures.append((out_name, msg))
            elif jtype == "bulk":
                for path, name, msg in run_bulk(job, work, out_path):
                    if path:
                        produced.append((path, name, folder))
                    else:
                        failures.append((name, msg))
            else:
                failures.append((out_name, f"unknown job type '{jtype}'"))
        except Exception as e:
            failures.append((out_name, f"exception: {e}"))
        print(f"    -> {'OK' if out_name not in [f[0] for f in failures] else 'see failures'}", flush=True)

    # Upload
    print("\n=== UPLOAD / RESULTS ===", flush=True)
    used = set()
    for path, name, folder in produced:
        if not (path and os.path.exists(path)):
            print(f"[missing] {name}", flush=True)
            continue
        size_mb = os.path.getsize(path) / 1024 / 1024
        if token:
            up_name = dbx._unique_name(Path(path).name, used)
            used.add(up_name)
            ok, fname, umsg, url = dbx.upload(token, path, folder, up_name)
            used.add(fname)
            if ok:
                print(f"[OK] {name} ({size_mb:.1f} MB) -> {url or '(no link)'}", flush=True)
            else:
                print(f"[upload FAILED] {name} ({size_mb:.1f} MB): {umsg}", flush=True)
        else:
            print(f"[OK-artifact] {name} ({size_mb:.1f} MB) in {OUTPUT_DIR}/", flush=True)

    if failures:
        print("\n=== FAILURES ===", flush=True)
        for name, msg in failures:
            print(f"[FAIL] {name}: {str(msg)[:200]}", flush=True)

    # Non-zero exit if everything failed, so the workflow shows red.
    if failures and not produced:
        sys.exit(1)


if __name__ == "__main__":
    main()
