import os
import sys
import json
import subprocess
import threading
import multiprocessing
import tempfile
import uuid
import shutil
from collections import Counter
from faster_whisper import WhisperModel

# Platform-safe explicit DLL registration for Windows Python 3.8+
if sys.platform == "win32" and sys.version_info >= (3, 8):
    site_packages = os.path.join(sys.prefix, "Lib", "site-packages")
    for nvidia_lib in ["cublas", "cudnn"]:
        dll_path = os.path.join(site_packages, "nvidia", nvidia_lib, "bin")
        if os.path.exists(dll_path):
            os.add_dll_directory(dll_path)

STOPWORDS = {
    "about", "their", "there", "would", "could", "should", "these", "those", "which", "where",
    "because", "through", "before", "after", "between", "under", "over", "other", "some", "such",
    "only", "very", "just", "also", "that", "this", "with", "from", "have", "they", "will",
    "what", "when", "make", "like", "time", "know", "take", "people", "into", "year", "your",
    "good", "them", "then", "look", "come", "think", "than", "been", "much"
}

class SVTBackend:
    def __init__(self):
        self.segments = []
        self.data_lock = threading.Lock()

    def get_optimal_hardware(self):
        config = {"device": "cpu", "model": "base", "description": "CPU Mode"}
        try:
            import torch
            if torch.cuda.is_available():
                vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                gpu_name = torch.cuda.get_device_name(0)
                config["device"] = "cuda"
                config["description"] = f"{gpu_name} ({vram_gb:.1f} GB VRAM)"
                if vram_gb >= 6.0: config["model"] = "turbo"
                elif vram_gb >= 4.0: config["model"] = "small"
                elif vram_gb >= 2.0: config["model"] = "base"
                else: config["model"] = "tiny"
                return config
        except ImportError:
            pass 
        threads = multiprocessing.cpu_count()
        config["description"] = f"CPU ({threads} Threads)"
        config["model"] = "base" 
        return config

    def prepare_audio_for_playback(self, media_path):
        if not shutil.which("ffmpeg"):
            raise FileNotFoundError("FFmpeg is not installed or not found in system PATH.")
            
        temp_dir = tempfile.gettempdir()
        unique_filename = f"svt_temp_{uuid.uuid4().hex}.wav"
        temp_audio_path = os.path.join(temp_dir, unique_filename)
        
        command = [
            "ffmpeg", "-y", "-i", media_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "22050", "-ac", "1",
            temp_audio_path
        ]
        
        try:
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode('utf-8') if e.stderr else "Unknown FFmpeg error"
            raise RuntimeError(f"Audio extraction failed: {error_msg}")
            
        return temp_audio_path

    def transcribe(self, file_path, model_size, device, compute_type, callback_status, callback_segment, callback_done, callback_error):
        try:
            with self.data_lock:
                self.segments = []
            
            callback_status(f"Loading {model_size.title()} Model...", 0.1)
            model = WhisperModel(model_size, device=device, compute_type=compute_type)
            
            callback_status("Transcribing...", 0.3)
            segments, info = model.transcribe(
                file_path, beam_size=5, vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500)
            )
            
            total_duration = info.duration
            for index, segment in enumerate(segments, 1):
                seg_data = {
                    "id": index,
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip(),
                    "speaker": f"SPEAKER_{index:02d}"
                }
                with self.data_lock:
                    self.segments.append(seg_data)
                
                progress = min(1.0, segment.end / total_duration) if total_duration > 0 else 0.0
                callback_segment(seg_data, progress)
                
            callback_status("COMPLETE", 1.0)
            with self.data_lock:
                callback_done(list(self.segments), info.duration)
            
        except Exception as e:
            callback_error(str(e))

    def export_srt(self, save_path):
        with self.data_lock:
            with open(save_path, 'w', encoding='utf-8') as f:
                for seg in self.segments:
                    start = self.format_srt_time(seg['start'])
                    end = self.format_srt_time(seg['end'])
                    f.write(f"{seg['id']}\n{start} --> {end}\n{seg['text']}\n\n")

    def export_json(self, save_path):
        with self.data_lock:
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(self.segments, f, indent=4, ensure_ascii=False)

    def generate_summary(self):
        with self.data_lock:
            if not self.segments:
                return None
            full_text = " ".join([seg['text'] for seg in self.segments])
            
        words = [w.lower() for w in full_text.split() if len(w) > 3 and w.lower() not in STOPWORDS]
        counter = Counter(words)
        keywords = [word for word, count in counter.most_common(5)] if words else ["Insufficient data"]
        
        return {"word_count": len(full_text.split()), "keywords": keywords}

    @staticmethod
    def format_time(seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    @staticmethod
    def format_srt_time(seconds):
        ms = int((seconds - int(seconds)) * 1000)
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"