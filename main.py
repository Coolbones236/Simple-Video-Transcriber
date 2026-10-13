import copy
import inspect
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import wave
from collections import Counter

import numpy as np

# 1. Platform-safe explicit DLL registration for Windows Python 3.8+
_DLL_HANDLES = []

if sys.platform == "win32" and sys.version_info >= (3, 8):
    site_packages = os.path.join(sys.prefix, "Lib", "site-packages")
    for nvidia_lib in ["cublas", "cudnn"]:
        dll_path = os.path.join(site_packages, "nvidia", nvidia_lib, "bin")
        if os.path.exists(dll_path):
            _DLL_HANDLES.append(os.add_dll_directory(dll_path))

try:
    import av
except ImportError:
    av = None

from faster_whisper import WhisperModel


# 2. Unconditional Defensive PyAV Compatibility Shim for `metadata_errors`
def _patch_pyav_open():
    """
    Wraps av.open() unconditionally so that if an older PyAV build rejects
    the 'metadata_errors' keyword argument, it is transparently stripped
    and retried.
    """
    if av is None:
        return
    orig_open = getattr(av, "open", None)
    if orig_open is None or getattr(orig_open, "_is_patched", False):
        return

    def _safe_av_open(*args, **kwargs):
        try:
            return orig_open(*args, **kwargs)
        except TypeError as exc:
            if "metadata_errors" in str(exc) and "metadata_errors" in kwargs:
                cleaned_kwargs = dict(kwargs)
                cleaned_kwargs.pop("metadata_errors", None)
                return orig_open(*args, **cleaned_kwargs)
            raise

    _safe_av_open._is_patched = True
    av.open = _safe_av_open


_patch_pyav_open()

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
        self.data_lock = threading.RLock()
        self._model_cache = {}

    def get_optimal_hardware(self):
        config = {"device": "cpu", "model": "base", "description": "CPU Mode"}
        try:
            import torch
            if torch.cuda.is_available():
                vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                gpu_name = torch.cuda.get_device_name(0)
                config["device"] = "cuda"
                config["description"] = f"{gpu_name} ({vram_gb:.1f} GB VRAM)"
                if vram_gb >= 6.0:
                    config["model"] = "turbo"
                elif vram_gb >= 4.0:
                    config["model"] = "small"
                elif vram_gb >= 2.0:
                    config["model"] = "base"
                else:
                    config["model"] = "tiny"
                return config
        except ImportError:
            pass

        threads = multiprocessing.cpu_count()
        config["description"] = f"CPU ({threads} Threads)"
        config["model"] = "base"
        return config

    def prepare_audio_for_playback(self, media_path):
        if not media_path or not os.path.isfile(media_path):
            raise FileNotFoundError(f"Media file not found: {media_path}")

        if not shutil.which("ffmpeg"):
            raise FileNotFoundError("FFmpeg is not installed or not found in system PATH.")

        temp_dir = tempfile.gettempdir()
        unique_filename = f"svt_temp_{uuid.uuid4().hex}.wav"
        temp_audio_path = os.path.join(temp_dir, unique_filename)

        # 16000 Hz mono PCM s16le is natively compatible with both pygame playback
        # and faster-whisper (eliminating the need for PyAV resampling/decoding).
        command = [
            "ffmpeg", "-y", "-i", media_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            temp_audio_path
        ]

        try:
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True
            )
        except subprocess.CalledProcessError as e:
            if os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                except OSError:
                    pass
            error_msg = (
                e.stderr.decode("utf-8", errors="replace").strip()
                if e.stderr else "Unknown FFmpeg error"
            )
            raise RuntimeError(f"Audio extraction failed: {error_msg}") from e

        return temp_audio_path

    @staticmethod
    def _load_wav_as_float32(wav_path):
        """
        Loads a 16kHz mono 16-bit PCM WAV file directly into a float32 NumPy array
        normalized to [-1.0, 1.0]. Bypasses PyAV (av.open) completely.
        """
        with wave.open(wav_path, "rb") as wf:
            if wf.getnchannels() == 1 and wf.getsampwidth() == 2 and wf.getframerate() == 16000:
                frames = wf.readframes(wf.getnframes())
                audio_int16 = np.frombuffer(frames, dtype=np.int16)
                return audio_int16.astype(np.float32) / 32768.0
        return None

    def _get_or_create_model(self, model_size, device, compute_type):
        key = (model_size, device, compute_type)
        if key not in self._model_cache:
            self._model_cache.clear()
            self._model_cache[key] = WhisperModel(
                model_size, device=device, compute_type=compute_type
            )
        return self._model_cache[key]

    def transcribe(
        self, file_path, model_size, device, compute_type,
        callback_status, callback_segment, callback_done, callback_error,
        prepared_wav_path=None
    ):
        temp_wav_created = None
        try:
            with self.data_lock:
                self.segments = []

            callback_status(f"Loading {model_size.title()} Model...", 0.1)
            model = self._get_or_create_model(model_size, device, compute_type)

            callback_status("Transcribing...", 0.3)

            # Prefer pre-extracted 16kHz mono PCM float32 array to avoid PyAV bugs
            audio_input = file_path
            if isinstance(file_path, str):
                wav_source = prepared_wav_path
                if not wav_source and file_path.lower().endswith(".wav"):
                    wav_source = file_path

                if wav_source and os.path.isfile(wav_source):
                    pcm_array = self._load_wav_as_float32(wav_source)
                    if pcm_array is not None:
                        audio_input = pcm_array

            try:
                segments, info = model.transcribe(
                    audio_input, beam_size=5, vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500)
                )
            except TypeError as exc:
                # Fallback if direct file_path triggered PyAV metadata_errors
                if "metadata_errors" in str(exc) and isinstance(file_path, str):
                    temp_wav_created = self.prepare_audio_for_playback(file_path)
                    pcm_array = self._load_wav_as_float32(temp_wav_created)
                    segments, info = model.transcribe(
                        pcm_array if pcm_array is not None else temp_wav_created,
                        beam_size=5, vad_filter=True,
                        vad_parameters=dict(min_silence_duration_ms=500)
                    )
                else:
                    raise

            total_duration = float(info.duration or 0.0)
            for index, segment in enumerate(segments, 1):
                seg_data = {
                    "id": index,
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": segment.text.strip(),
                    "speaker": "SPEAKER_01"
                }
                with self.data_lock:
                    self.segments.append(seg_data)

                progress = (
                    min(1.0, max(0.0, segment.end / total_duration))
                    if total_duration > 0 else 0.0
                )
                callback_segment(copy.deepcopy(seg_data), progress)

            callback_status("COMPLETE", 1.0)
            with self.data_lock:
                segments_snapshot = copy.deepcopy(self.segments)

            callback_done(segments_snapshot, total_duration)

        except Exception as e:
            callback_error(str(e))
        finally:
            if temp_wav_created and os.path.exists(temp_wav_created):
                try:
                    os.remove(temp_wav_created)
                except OSError:
                    pass

    def export_srt(self, save_path):
        with self.data_lock:
            segments_snapshot = copy.deepcopy(self.segments)

        speakers = {seg.get("speaker", "SPEAKER_01") for seg in segments_snapshot}
        include_speaker = len(speakers) > 1 or (
            speakers and next(iter(speakers)) != "SPEAKER_01"
        )

        with open(save_path, "w", encoding="utf-8") as f:
            for seg in segments_snapshot:
                start = self.format_srt_time(seg["start"])
                end = self.format_srt_time(seg["end"])
                text = seg["text"]
                if include_speaker and seg.get("speaker"):
                    text = f"[{seg['speaker']}] {text}"
                f.write(f"{seg['id']}\n{start} --> {end}\n{text}\n\n")

    def export_json(self, save_path):
        with self.data_lock:
            segments_snapshot = copy.deepcopy(self.segments)

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(segments_snapshot, f, indent=4, ensure_ascii=False)

    def generate_summary(self):
        with self.data_lock:
            if not self.segments:
                return None
            full_text = " ".join(seg["text"] for seg in self.segments if seg.get("text"))

        all_words = re.findall(r"\b\w+\b", full_text.lower())
        filtered_words = [
            w for w in all_words
            if len(w) > 3 and not w.isdigit() and w not in STOPWORDS
        ]
        counter = Counter(filtered_words)
        keywords = (
            [word for word, _ in counter.most_common(5)]
            if filtered_words else ["Insufficient data"]
        )

        return {"word_count": len(all_words), "keywords": keywords}

    @staticmethod
    def format_time(seconds):
        total_seconds = max(0, int(round(seconds)))
        m, s = divmod(total_seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    @staticmethod
    def format_srt_time(seconds):
        total_ms = max(0, int(round(seconds * 1000)))
        total_seconds, ms = divmod(total_ms, 1000)
        m, s = divmod(total_seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"