import os
import threading
import tkinter as tk
from bisect import bisect_right

import customtkinter as ctk
from tkinter import filedialog, messagebox
import pygame
from main import SVTBackend

# --- THEME PALETTE ---
BG_CANVAS = "#070B12"
BG_SIDEBAR = "#090E17"
CARD_BG = "#0F172A"
CARD_ACTIVE_BG = "#0C2233"
BORDER_INACTIVE = "#1E293B"
ACCENT_CYAN = "#22D3EE"
ACCENT_HOVER = "#06B6D4"
TEXT_MAIN = "#E2E8F0"
TEXT_MUTED = "#94A3B8"
TEXT_DIM = "#475569"

# status pill styles: (text colour, background)
STATUS_STYLES = {
    "ready": (TEXT_MUTED, "#1E293B"),
    "busy": ("#FBBF24", "#2A2110"),
    "done": ("#34D399", "#0F2A22"),
    "error": ("#F87171", "#2E1414"),
}

SPEAKER_COLORS = ["#22D3EE", "#A78BFA", "#F472B6", "#FBBF24", "#34D399", "#60A5FA"]

MODEL_HINTS = {
    "tiny": "Fastest · lowest accuracy",
    "base": "Fast · fine for clear audio",
    "small": "Balanced speed and accuracy",
    "medium": "More accurate · slower",
    "large-v3": "Most accurate · GPU recommended",
    "turbo": "Near-large accuracy · fast",
}

ctk.set_appearance_mode("dark")


def make_ghost_button(parent, text, command, width=96):
    return ctk.CTkButton(
        parent, text=text, width=width, height=32, fg_color="transparent",
        border_width=1, border_color=BORDER_INACTIVE, text_color="#FFFFFF",
        text_color_disabled=TEXT_DIM, hover_color=CARD_BG, command=command,
        state="disabled",
    )


class SVTApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SVT Studio")
        self.geometry("1180x760")
        self.minsize(960, 600)
        self.configure(fg_color=BG_CANVAS)

        self.audio_available = False
        try:
            pygame.mixer.init(frequency=16000, size=-16, channels=1)
            self.audio_available = True
        except pygame.error:
            pass

        self.backend = SVTBackend()

        self.is_transcribing = False
        self.is_playing = False
        self.scrubbing = False
        self.has_media = False
        self.media_duration = 0.0
        self.playback_offset = 0.0
        self.active_card_id = None
        self.card_elements = {}
        self.order_ids = []        # segment ids in display order
        self.order_starts = []     # matching start times (for bisect)
        self.speaker_colors = {}
        self.wrap_len = 680
        self._wrap_job = None
        self._filter_job = None

        self.current_file_path = None
        self.current_audio_path = None
        self.file_queue = []

        self.setup_layout()
        self.refresh_views()
        self.setup_shortcuts()
        self.poll_playback()

    # ------------------------------------------------------------ lifecycle
    def _cleanup_audio_file(self, path=None):
        target = path or self.current_audio_path
        if not target:
            return
        if self.audio_available:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except pygame.error:
                pass
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
        if target == self.current_audio_path:
            self.current_audio_path = None

    def destroy(self):
        """Ensure cache teardown on app exit."""
        self._cleanup_audio_file()
        super().destroy()

    # --------------------------------------------------------------- layout
    def setup_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.build_sidebar()
        self.build_workspace()
        self.build_player()

    def build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=250, fg_color=BG_SIDEBAR, corner_radius=0)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(sidebar, text="⚡ SVT Studio", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="#FFFFFF").pack(anchor="w", padx=24, pady=(28, 20))

        self.btn_import = ctk.CTkButton(
            sidebar, text="⊕  Import Media", font=ctk.CTkFont(weight="bold", size=14),
            fg_color=ACCENT_CYAN, text_color="#000000", hover_color=ACCENT_HOVER,
            height=44, corner_radius=8, command=self.upload_file)
        self.btn_import.pack(fill="x", padx=20, pady=(0, 6))
        ctk.CTkLabel(sidebar, text="Select several files to queue them", font=ctk.CTkFont(size=11),
                     text_color=TEXT_DIM).pack(anchor="w", padx=24, pady=(0, 22))

        ctk.CTkLabel(sidebar, text="CONFIGURATION", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=24, pady=(0, 6))

        hw = self.backend.get_optimal_hardware()
        ctk.CTkLabel(sidebar, text=f"Detected: {hw['description']}", font=ctk.CTkFont(size=11),
                     text_color=ACCENT_CYAN).pack(anchor="w", padx=24, pady=(0, 12))

        ctk.CTkLabel(sidebar, text="AI Model", font=ctk.CTkFont(size=13),
                     text_color=TEXT_MAIN).pack(anchor="w", padx=24, pady=(4, 2))
        self.model_var = ctk.StringVar(value=hw["model"])
        self.model_menu = ctk.CTkOptionMenu(
            sidebar, values=list(MODEL_HINTS), variable=self.model_var, fg_color=CARD_BG,
            button_color=BORDER_INACTIVE, command=self.on_model_change)
        self.model_menu.pack(fill="x", padx=20, pady=(0, 2))
        self.lbl_model_hint = ctk.CTkLabel(sidebar, text=MODEL_HINTS.get(hw["model"], ""),
                                           font=ctk.CTkFont(size=11), text_color=TEXT_DIM)
        self.lbl_model_hint.pack(anchor="w", padx=24, pady=(0, 12))

        ctk.CTkLabel(sidebar, text="Hardware", font=ctk.CTkFont(size=13),
                     text_color=TEXT_MAIN).pack(anchor="w", padx=24, pady=(4, 2))
        self.device_var = ctk.StringVar(value=hw["device"])
        self.device_menu = ctk.CTkOptionMenu(
            sidebar, values=["cpu", "cuda"], variable=self.device_var, fg_color=CARD_BG,
            button_color=BORDER_INACTIVE)
        self.device_menu.pack(fill="x", padx=20, pady=(0, 20))

        ctk.CTkButton(sidebar, text="⌨  Keyboard shortcuts", anchor="w", fg_color="transparent",
                      hover_color=CARD_BG, text_color=TEXT_MUTED, font=ctk.CTkFont(size=13),
                      height=36, command=self.show_shortcuts).pack(side="bottom", fill="x", padx=14, pady=16)

    def build_workspace(self):
        ws = ctk.CTkFrame(self, fg_color="transparent")
        ws.grid(row=0, column=1, sticky="nsew", padx=24, pady=(20, 10))
        ws.grid_rowconfigure(3, weight=1)
        ws.grid_columnconfigure(0, weight=1)

        # ---- header: title + actions
        header = ctk.CTkFrame(ws, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.grid_columnconfigure(0, weight=1)
        self.lbl_title = ctk.CTkLabel(header, text="No media loaded", anchor="w",
                                      font=ctk.CTkFont(size=20, weight="bold"), text_color="#FFFFFF")
        self.lbl_title.grid(row=0, column=0, sticky="ew")

        self.btn_json = make_ghost_button(header, "⇪  JSON", self.export_json, 84)
        self.btn_srt = make_ghost_button(header, "⇪  SRT", self.export_srt, 84)
        self.btn_copy = make_ghost_button(header, "⧉  Copy", self.copy_transcript, 84)
        self.btn_json.grid(row=0, column=3, padx=(8, 0))
        self.btn_srt.grid(row=0, column=2, padx=(8, 0))
        self.btn_copy.grid(row=0, column=1, padx=(8, 0))

        # ---- status bar
        status = ctk.CTkFrame(ws, fg_color="transparent")
        status.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.lbl_status = ctk.CTkLabel(status, text="", corner_radius=10, height=24,
                                       font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_status.pack(side="left")
        self.lbl_duration = ctk.CTkLabel(status, text="", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.lbl_duration.pack(side="left", padx=(12, 0))
        self.lbl_queue = ctk.CTkLabel(status, text="", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self.lbl_queue.pack(side="left", padx=(12, 0))
        self.lbl_percent = ctk.CTkLabel(status, text="", font=ctk.CTkFont(size=12, weight="bold"),
                                        text_color=ACCENT_CYAN, width=40)
        self.progress = ctk.CTkProgressBar(status, fg_color=BORDER_INACTIVE, progress_color=ACCENT_CYAN,
                                           height=6)
        self.progress.set(0)
        self.set_status("Ready", "ready")

        # ---- tabs (full-width segmented control)
        self.tab_var = ctk.StringVar(value="Transcript")
        self.tabs = ctk.CTkSegmentedButton(
            ws, values=["Transcript", "AI Insights"], variable=self.tab_var, command=lambda v: self.refresh_views(),
            height=36, font=ctk.CTkFont(size=13, weight="bold"), fg_color=BG_SIDEBAR,
            selected_color=BORDER_INACTIVE, selected_hover_color="#334155",
            unselected_color=BG_SIDEBAR, unselected_hover_color=CARD_BG, text_color="#FFFFFF")
        self.tabs.grid(row=2, column=0, sticky="w", pady=(0, 12))

        # ---- content area (views are swapped in/out)
        self.content = ctk.CTkFrame(ws, fg_color="transparent")
        self.content.grid(row=3, column=0, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.build_empty_view()
        self.build_transcript_view()
        self.build_insights_view()

    def build_empty_view(self):
        self.view_empty = ctk.CTkFrame(self.content, fg_color="transparent")
        card = ctk.CTkFrame(self.view_empty, fg_color=CARD_BG, border_width=1, border_color=BORDER_INACTIVE,
                            corner_radius=16)
        card.place(relx=0.5, rely=0.46, anchor="center")
        ctk.CTkLabel(card, text="🎙", font=ctk.CTkFont(size=44)).pack(padx=70, pady=(30, 6))
        ctk.CTkLabel(card, text="Transcribe your first recording", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#FFFFFF").pack(padx=40)
        ctk.CTkLabel(card, text="MP4 · MKV · MOV · MP3 · WAV", font=ctk.CTkFont(size=12),
                     text_color=TEXT_MUTED).pack(pady=(4, 18))
        ctk.CTkButton(card, text="⊕  Choose files", width=180, height=40, fg_color=ACCENT_CYAN,
                      text_color="#000000", hover_color=ACCENT_HOVER, font=ctk.CTkFont(weight="bold"),
                      command=self.upload_file).pack()
        ctk.CTkLabel(card, text="Click any line afterwards to jump to that moment in the audio.",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(pady=(16, 28), padx=30)

    def build_transcript_view(self):
        self.view_transcript = ctk.CTkFrame(self.content, fg_color="transparent")
        self.view_transcript.grid_rowconfigure(1, weight=1)
        self.view_transcript.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(self.view_transcript, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(bar, placeholder_text="🔍  Search text or speaker   (Ctrl+F)",
                                         font=ctk.CTkFont(size=14), height=38, fg_color=CARD_BG,
                                         border_color=BORDER_INACTIVE)
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self.search_entry.bind("<KeyRelease>", lambda e: self.schedule_filter())
        self.search_entry.bind("<Escape>", lambda e: self.clear_search())

        self.btn_clear = ctk.CTkButton(bar, text="✕", width=38, height=38, fg_color="transparent",
                                       border_width=1, border_color=BORDER_INACTIVE, hover_color=CARD_BG,
                                       text_color=TEXT_MUTED, command=self.clear_search)
        self.btn_clear.grid(row=0, column=1, padx=(8, 0))
        self.lbl_count = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED, width=110,
                                      anchor="e")
        self.lbl_count.grid(row=0, column=2, padx=(10, 0))

        self.cards_scroll = ctk.CTkScrollableFrame(self.view_transcript, fg_color=BG_CANVAS, corner_radius=0)
        self.cards_scroll.grid(row=1, column=0, sticky="nsew")
        self.cards_scroll._parent_canvas.bind("<Configure>", lambda e: self.schedule_rewrap())

        self.lbl_no_results = ctk.CTkLabel(self.cards_scroll, text="No matching lines.", text_color=TEXT_MUTED,
                                           font=ctk.CTkFont(size=14))

    def build_insights_view(self):
        self.view_insights = ctk.CTkFrame(self.content, fg_color="transparent")
        self.view_insights.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(self.view_insights, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Quick analytics", font=ctk.CTkFont(size=15, weight="bold"),
                     text_color="#FFFFFF").grid(row=0, column=0, sticky="w")
        self.btn_summary = ctk.CTkButton(top, text="Generate insights", width=160, height=34,
                                         fg_color=ACCENT_CYAN, text_color="#000000", hover_color=ACCENT_HOVER,
                                         text_color_disabled="#0B3A44", font=ctk.CTkFont(weight="bold"),
                                         command=self.generate_summary, state="disabled")
        self.btn_summary.grid(row=0, column=1)

        self.tiles_frame = ctk.CTkFrame(self.view_insights, fg_color="transparent")
        self.tiles_frame.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        self.tile_values = {}
        for i, name in enumerate(["Words", "Duration", "Speakers", "Pace (wpm)"]):
            self.tiles_frame.grid_columnconfigure(i, weight=1, uniform="tile")
            tile = ctk.CTkFrame(self.tiles_frame, fg_color=CARD_BG, border_width=1, border_color=BORDER_INACTIVE,
                                corner_radius=10)
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            val = ctk.CTkLabel(tile, text="—", font=ctk.CTkFont(size=26, weight="bold"), text_color="#FFFFFF")
            val.pack(anchor="w", padx=16, pady=(14, 0))
            ctk.CTkLabel(tile, text=name.upper(), font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=TEXT_MUTED).pack(anchor="w", padx=16, pady=(0, 14))
            self.tile_values[name] = val

        ctk.CTkLabel(self.view_insights, text="TOP KEYWORDS", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_MUTED).grid(row=2, column=0, sticky="w", pady=(24, 8))
        self.chips_frame = ctk.CTkFrame(self.view_insights, fg_color="transparent")
        self.chips_frame.grid(row=3, column=0, sticky="ew")
        self.lbl_insight_hint = ctk.CTkLabel(
            self.chips_frame, text="Transcribe a file, then click “Generate insights”.",
            font=ctk.CTkFont(size=13), text_color=TEXT_DIM)
        self.lbl_insight_hint.pack(anchor="w")

    def build_player(self):
        bar = ctk.CTkFrame(self, height=78, fg_color=BG_SIDEBAR, corner_radius=0, border_width=1,
                           border_color=BORDER_INACTIVE)
        bar.grid(row=1, column=1, sticky="ew")
        bar.pack_propagate(False)

        ctrl = ctk.CTkFrame(bar, fg_color="transparent")
        ctrl.pack(side="left", padx=(20, 4))
        ctk.CTkButton(ctrl, text="⏪", width=36, height=36, fg_color="transparent", text_color="#FFFFFF",
                      hover_color=CARD_BG, command=lambda: self.seek_relative(-5)).pack(side="left", padx=2)
        self.btn_play = ctk.CTkButton(ctrl, text="▶", width=44, height=44, fg_color=ACCENT_CYAN,
                                      text_color="#000000", hover_color=ACCENT_HOVER, corner_radius=22,
                                      font=ctk.CTkFont(size=16, weight="bold"), command=self.toggle_playback)
        self.btn_play.pack(side="left", padx=8)
        ctk.CTkButton(ctrl, text="⏩", width=36, height=36, fg_color="transparent", text_color="#FFFFFF",
                      hover_color=CARD_BG, command=lambda: self.seek_relative(5)).pack(side="left", padx=2)

        vol = ctk.CTkFrame(bar, fg_color="transparent")
        vol.pack(side="right", padx=(0, 20))
        ctk.CTkLabel(vol, text="🔊", text_color=TEXT_MUTED).pack(side="left", padx=(0, 6))
        self.volume_slider = ctk.CTkSlider(
            vol, from_=0, to=1, width=90, fg_color=BORDER_INACTIVE,
            progress_color=TEXT_MUTED, button_color="#FFFFFF",
            command=self.on_volume_change
        )
        self.volume_slider.set(1.0)
        self.volume_slider.pack(side="left")

        self.lbl_time = ctk.CTkLabel(bar, text="00:00 / 00:00", font=ctk.CTkFont(size=12, weight="bold"),
                                     text_color=TEXT_MUTED, width=100)
        self.lbl_time.pack(side="right", padx=(8, 16))

        prog = ctk.CTkFrame(bar, fg_color="transparent")
        prog.pack(side="left", fill="x", expand=True, padx=12)
        self.timeline_slider = ctk.CTkSlider(prog, from_=0, to=1, fg_color=BORDER_INACTIVE,
                                             progress_color=ACCENT_CYAN, button_color="#FFFFFF",
                                             command=self.on_slider_drag)
        self.timeline_slider.set(0)
        self.timeline_slider.pack(fill="x", expand=True)
        # seek only on release so dragging doesn't restart the audio on every pixel
        self.timeline_slider.bind("<ButtonRelease-1>", self.on_slider_release)

    def on_volume_change(self, val):
        if self.audio_available:
            try:
                pygame.mixer.music.set_volume(float(val))
            except pygame.error:
                pass

    # ------------------------------------------------------------ shortcuts
    def setup_shortcuts(self):
        self.bind("<space>", lambda e: self.shortcut(self.toggle_playback))
        self.bind("<Left>", lambda e: self.shortcut(lambda: self.seek_relative(-5)))
        self.bind("<Right>", lambda e: self.shortcut(lambda: self.seek_relative(5)))
        self.bind("<Up>", lambda e: self.shortcut(lambda: self.cards_scroll._parent_canvas.yview_scroll(-1, "units")))
        self.bind("<Down>", lambda e: self.shortcut(lambda: self.cards_scroll._parent_canvas.yview_scroll(1, "units")))
        self.bind("<Control-f>", lambda e: self.focus_search())
        self.bind("<Control-o>", lambda e: self.upload_file())

    def shortcut(self, fn):
        """Run a shortcut unless the user is typing in a text field."""
        try:
            focused = self.focus_get()
            if isinstance(focused, (tk.Entry, tk.Text)):
                return None
        except KeyError:
            pass
        fn()
        return "break"

    def show_shortcuts(self):
        messagebox.showinfo("Keyboard shortcuts",
                            "Space\tPlay / pause\n←  →\tSeek 5 seconds\n↑  ↓\tScroll transcript\n"
                            "Ctrl+F\tSearch transcript\nEsc\tClear search\nCtrl+O\tImport media")

    # ---------------------------------------------------------------- views
    def refresh_views(self):
        for v in (self.view_empty, self.view_transcript, self.view_insights):
            v.grid_forget()
        if self.tab_var.get() == "AI Insights":
            self.view_insights.grid(row=0, column=0, sticky="nsew")
        elif self.has_media:
            self.view_transcript.grid(row=0, column=0, sticky="nsew")
        else:
            self.view_empty.grid(row=0, column=0, sticky="nsew")

    def set_status(self, text, kind="ready", progress_val=None):
        fg, bg = STATUS_STYLES[kind]
        self.lbl_status.configure(text=f"  ●  {text}  ", text_color=fg, fg_color=bg)
        if progress_val is not None:
            self.progress.set(progress_val)
            self.lbl_percent.configure(text=f"{int(progress_val * 100)}%")

    def set_controls_locked(self, locked):
        state = "disabled" if locked else "normal"
        self.model_menu.configure(state=state)
        self.device_menu.configure(state=state)

    def set_export_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for b in (self.btn_copy, self.btn_srt, self.btn_json, self.btn_summary):
            b.configure(state=state)

    def on_model_change(self, value):
        self.lbl_model_hint.configure(text=MODEL_HINTS.get(value, ""))

    # ------------------------------------------------ workflow & transcription
    def upload_file(self):
        file_paths = filedialog.askopenfilenames(filetypes=[("Media Files", "*.mp4 *.mkv *.mov *.mp3 *.wav")])
        if not file_paths:
            return
        self.file_queue.extend(file_paths)
        self.update_queue_label()
        if not self.is_transcribing:
            self.process_next_in_queue()

    def update_queue_label(self):
        n = len(self.file_queue)
        self.lbl_queue.configure(text=f"+{n} queued" if n else "")

    def reset_playback(self):
        if self.audio_available:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass
        self.is_playing = False
        self.scrubbing = False
        self.playback_offset = 0.0
        self.media_duration = 0.0
        self.btn_play.configure(text="▶")
        self.timeline_slider.configure(to=1)
        self.timeline_slider.set(0)
        self.lbl_time.configure(text="00:00 / 00:00")

    def process_next_in_queue(self):
        if not self.file_queue:
            self.set_status("All files complete", "done")
            self.update_queue_label()
            return

        self.reset_playback()
        self._cleanup_audio_file()

        self.current_file_path = self.file_queue.pop(0)
        self.lbl_title.configure(text=os.path.basename(self.current_file_path))
        self.has_media = True
        self.tab_var.set("Transcript")
        self.refresh_views()

        self.clear_search(refilter=False)
        for child in self.cards_scroll.winfo_children():
            if child is not self.lbl_no_results:
                child.destroy()
        self.lbl_no_results.pack_forget()
        self.card_elements.clear()
        self.order_ids.clear()
        self.order_starts.clear()
        self.speaker_colors.clear()
        self.active_card_id = None
        self.lbl_count.configure(text="")
        self.lbl_duration.configure(text="")
        self.reset_insights()

        self.is_transcribing = True
        self.set_controls_locked(True)
        self.set_export_enabled(False)
        self.update_queue_label()
        self.set_status("Extracting audio…", "busy", 0.0)

        self.progress.pack(side="right", fill="x", expand=True, padx=(16, 0))
        self.lbl_percent.pack(side="right", padx=(16, 0))

        threading.Thread(target=self.run_pipeline, args=(self.current_file_path,), daemon=True).start()

    def run_pipeline(self, file_path):
        audio_file = None
        try:
            audio_file = self.backend.prepare_audio_for_playback(file_path)
            device = self.device_var.get()
            compute = "float16" if device == "cuda" else "int8"

            self.backend.transcribe(
                file_path,
                model_size=self.model_var.get(),
                device=device,
                compute_type=compute,
                callback_status=lambda txt, prog: self.after(
                    0, lambda t=txt, p=prog: self.set_status(str(t).capitalize(), "busy", p)
                ),
                callback_segment=lambda seg, prog: self.after(
                    0, lambda s=seg, p=prog: self.handle_new_segment(s, p)
                ),
                callback_done=lambda segs, dur: self.after(
                    0, lambda af=audio_file, d=dur: self.on_transcription_finished(af, d)
                ),
                callback_error=lambda err: self.after(
                    0, lambda e=err, af=audio_file: self.on_error(e, af)
                ),
                prepared_wav_path=audio_file,
            )
        except Exception as e:
            msg = str(e)
            self.after(0, lambda m=msg, af=audio_file: self.on_error(m, af))

    def handle_new_segment(self, seg, prog):
        self.render_card(seg)
        self.progress.set(prog)
        self.lbl_percent.configure(text=f"{int(prog * 100)}%")
        self.update_count()
        self.scroll_to_card(seg["id"])

    # ---------------------------------------------------------------- cards
    def speaker_color(self, name):
        if name not in self.speaker_colors:
            self.speaker_colors[name] = SPEAKER_COLORS[len(self.speaker_colors) % len(SPEAKER_COLORS)]
        return self.speaker_colors[name]

    def render_card(self, seg):
        card_border = ctk.CTkFrame(self.cards_scroll, fg_color=BORDER_INACTIVE, corner_radius=8)
        card_inner = ctk.CTkFrame(card_border, fg_color=CARD_BG, corner_radius=7)
        card_inner.pack(fill="both", expand=True, padx=1, pady=1)

        left_col = ctk.CTkFrame(card_inner, fg_color=CARD_BG, width=130)
        left_col.pack(side="left", fill="y", padx=(14, 10), pady=12)
        left_col.pack_propagate(False)

        ctk.CTkLabel(left_col, text=f"[{self.backend.format_time(seg['start'])}]",
                     font=ctk.CTkFont(size=12, weight="bold"), text_color=ACCENT_CYAN).pack(anchor="w")

        color = self.speaker_color(seg["speaker"])
        lbl_speaker = ctk.CTkLabel(left_col, text=f"{seg['speaker']} ✎", font=ctk.CTkFont(size=10, weight="bold"),
                                   text_color=color, fg_color="#162033", corner_radius=6, cursor="hand2")
        lbl_speaker.pack(anchor="w", pady=(4, 0))

        lbl_text = ctk.CTkLabel(card_inner, text=seg["text"], font=ctk.CTkFont(size=14), text_color=TEXT_MAIN,
                                justify="left", anchor="w", wraplength=self.wrap_len, cursor="hand2")
        lbl_text.pack(side="left", fill="both", expand=True, padx=(5, 14), pady=12)

        for elem in (card_border, card_inner, left_col, lbl_text):
            elem.bind("<Button-1>", lambda e, s=seg["start"]: self.seek_to(s))
        lbl_speaker.bind("<Button-1>", lambda e, s_id=seg["id"]: self.prompt_rename_speaker(s_id))

        self.card_elements[seg["id"]] = {
            "border": card_border, "inner": card_inner, "speaker_lbl": lbl_speaker, "text_lbl": lbl_text,
            "start": seg["start"], "end": seg["end"], "text": seg["text"].lower(), "speaker": seg["speaker"],
        }
        self.order_ids.append(seg["id"])
        self.order_starts.append(seg["start"])

        if self.matches(self.card_elements[seg["id"]], self.current_query()):
            card_border.pack(fill="x", pady=6, padx=4)

    def prompt_rename_speaker(self, seg_id):
        if seg_id not in self.card_elements:
            return
        old_name = self.card_elements[seg_id]["speaker"]
        dialog = ctk.CTkInputDialog(text=f"Rename '{old_name}' everywhere to:", title="Edit speaker")
        new_name = dialog.get_input()

        if new_name and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            with self.backend.data_lock:
                for s in self.backend.segments:
                    if s["speaker"] == old_name:
                        s["speaker"] = new_name
            color = self.speaker_colors.pop(old_name, None) or self.speaker_color(new_name)
            self.speaker_colors[new_name] = color
            for data in self.card_elements.values():
                if data["speaker"] == old_name:
                    data["speaker"] = new_name
                    data["speaker_lbl"].configure(text=f"{new_name} ✎", text_color=color)
            speakers = len({d["speaker"] for d in self.card_elements.values()})
            if self.tile_values["Speakers"].cget("text") != "—":
                self.tile_values["Speakers"].configure(text=str(speakers))
            self.auto_save_current_files()

    def set_card_active(self, seg_id, active):
        data = self.card_elements.get(seg_id)
        if not data:
            return
        data["border"].configure(fg_color=ACCENT_CYAN if active else BORDER_INACTIVE)
        data["inner"].configure(fg_color=CARD_ACTIVE_BG if active else CARD_BG)
        for w in (data["speaker_lbl"].master,):
            w.configure(fg_color=CARD_ACTIVE_BG if active else CARD_BG)

    # --------------------------------------------------------------- search
    def current_query(self):
        return self.search_entry.get().strip().lower()

    @staticmethod
    def matches(data, query):
        return not query or query in data["text"] or query in data["speaker"].lower()

    def schedule_filter(self):
        if self._filter_job:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(200, self.filter_transcripts)

    def filter_transcripts(self):
        self._filter_job = None
        query = self.current_query()
        for seg_id in self.order_ids:
            self.card_elements[seg_id]["border"].pack_forget()
        self.lbl_no_results.pack_forget()
        shown = 0
        for seg_id in self.order_ids:   # re-pack in order so ordering is preserved
            data = self.card_elements[seg_id]
            if self.matches(data, query):
                data["border"].pack(fill="x", pady=6, padx=4)
                shown += 1
        if query and shown == 0:
            self.lbl_no_results.pack(pady=60)
        self.update_count()

    def update_count(self):
        total = len(self.order_ids)
        if not total:
            self.lbl_count.configure(text="")
            return
        query = self.current_query()
        if query:
            shown = sum(1 for d in self.card_elements.values() if self.matches(d, query))
            self.lbl_count.configure(text=f"{shown} of {total} lines")
        else:
            self.lbl_count.configure(text=f"{total} lines")

    def clear_search(self, refilter=True):
        self.search_entry.delete(0, "end")
        if refilter:
            self.filter_transcripts()
            self.focus_set()

    def focus_search(self):
        if self.has_media:
            self.tab_var.set("Transcript")
            self.refresh_views()
            self.search_entry.focus_set()

    # ------------------------------------------------------------ responsive
    def schedule_rewrap(self):
        if self._wrap_job:
            self.after_cancel(self._wrap_job)
        self._wrap_job = self.after(150, self.apply_rewrap)

    def apply_rewrap(self):
        self._wrap_job = None
        new_wrap = max(280, self.cards_scroll._parent_canvas.winfo_width() - 230)
        if abs(new_wrap - self.wrap_len) < 12:
            return
        self.wrap_len = new_wrap
        for data in self.card_elements.values():
            data["text_lbl"].configure(wraplength=new_wrap)

    def scroll_to_card(self, target_id):
        if target_id not in self.card_elements:
            return
        widget = self.card_elements[target_id]["border"]
        if not widget.winfo_ismapped():
            return
        canvas = self.cards_scroll._parent_canvas
        self.update_idletasks()
        widget_y = widget.winfo_y()
        widget_h = widget.winfo_height()
        canvas_h = canvas.winfo_height()
        bbox = canvas.bbox("all")
        if not bbox:
            return
        inner_h = bbox[3] - bbox[1]
        if inner_h > canvas_h:
            target_y = widget_y - (canvas_h / 2) + (widget_h / 2)
            target_y = max(0, min(target_y, inner_h - canvas_h))
            canvas.yview_moveto(target_y / inner_h)

    # ------------------------------------------------------------ completion
    def auto_save_current_files(self):
        if self.current_file_path and self.backend.segments:
            transcript_dir = os.path.join(os.getcwd(), "Transcripts")
            os.makedirs(transcript_dir, exist_ok=True)
            name = os.path.splitext(os.path.basename(self.current_file_path))[0]
            self.backend.export_srt(os.path.join(transcript_dir, f"{name}.srt"))
            self.backend.export_json(os.path.join(transcript_dir, f"{name}.json"))

    def finish_progress(self):
        self.progress.pack_forget()
        self.lbl_percent.pack_forget()

    def on_transcription_finished(self, audio_file, duration):
        self.is_transcribing = False
        self.media_duration = max(duration, 0.0)
        self.current_audio_path = audio_file
        self.finish_progress()
        self.set_controls_locked(False)
        self.set_export_enabled(bool(self.backend.segments))
        self.set_status("Complete", "done")
        self.lbl_duration.configure(text=f"Duration {self.backend.format_time(self.media_duration)}")
        self.timeline_slider.configure(to=max(self.media_duration, 1.0))

        if self.audio_available and audio_file and os.path.exists(audio_file):
            try:
                pygame.mixer.music.load(audio_file)
                pygame.mixer.music.set_volume(float(self.volume_slider.get()))
            except pygame.error:
                pass

        self.playback_offset = 0.0
        self.update_time_display(0)
        self.update_count()

        self.auto_save_current_files()
        if self.file_queue:
            self.process_next_in_queue()

    def on_error(self, error_msg, temp_audio_file=None):
        self.is_transcribing = False
        self.finish_progress()
        self.set_controls_locked(False)
        self.set_status("Error", "error")
        if temp_audio_file:
            self._cleanup_audio_file(temp_audio_file)
        messagebox.showerror("Processing error", f"An error occurred:\n{error_msg}")
        if self.file_queue:
            self.process_next_in_queue()

    # ------------------------------------------------- playback & live sync
    def toggle_playback(self):
        if not self.media_duration or not self.audio_available or not self.current_audio_path:
            return
        try:
            if self.is_playing:
                self.playback_offset = self.get_current_time()
                pygame.mixer.music.pause()
                self.btn_play.configure(text="▶")
                self.is_playing = False
            else:
                pygame.mixer.music.play(start=self.playback_offset)
                self.btn_play.configure(text="❚❚")
                self.is_playing = True
        except pygame.error:
            pass

    def seek_to(self, seconds):
        if not self.media_duration or not self.audio_available or not self.current_audio_path:
            return
        seconds = max(0.0, min(self.media_duration, float(seconds)))
        self.playback_offset = seconds
        try:
            pygame.mixer.music.play(start=seconds)
            if not self.is_playing:
                pygame.mixer.music.pause()
        except pygame.error:
            pass
        self.timeline_slider.set(seconds)
        self.update_time_display(seconds)
        self.sync_active_card(seconds)

    def seek_relative(self, delta):
        self.seek_to(self.get_current_time() + delta)

    def on_slider_drag(self, value):
        if not self.media_duration:
            return
        self.scrubbing = True
        self.update_time_display(float(value))

    def on_slider_release(self, _event):
        if self.scrubbing:
            self.scrubbing = False
            self.seek_to(self.timeline_slider.get())

    def get_current_time(self):
        if not self.is_playing or not self.audio_available:
            return self.playback_offset
        try:
            pos = pygame.mixer.music.get_pos()
        except pygame.error:
            return self.playback_offset
        if pos == -1:
            return self.playback_offset
        return min(self.media_duration, self.playback_offset + pos / 1000.0)

    def update_time_display(self, cur_sec):
        self.lbl_time.configure(
            text=f"{self.backend.format_time(cur_sec)} / {self.backend.format_time(self.media_duration)}")

    def poll_playback(self):
        if self.is_playing and not self.scrubbing:
            cur = self.get_current_time()
            self.timeline_slider.set(cur)
            self.update_time_display(cur)
            self.sync_active_card(cur)
            busy = False
            if self.audio_available:
                try:
                    busy = pygame.mixer.music.get_busy()
                except pygame.error:
                    busy = False
            if cur >= self.media_duration - 0.05 or not busy:
                if self.audio_available:
                    try:
                        pygame.mixer.music.stop()
                    except pygame.error:
                        pass
                self.is_playing = False
                self.btn_play.configure(text="▶")
                self.playback_offset = 0.0
                self.timeline_slider.set(0)
                self.update_time_display(0)
        self.after(100, self.poll_playback)

    def sync_active_card(self, cur_time):
        idx = bisect_right(self.order_starts, cur_time) - 1
        target_id = self.order_ids[idx] if idx >= 0 else None
        if target_id != self.active_card_id:
            self.set_card_active(self.active_card_id, False)
            self.set_card_active(target_id, True)
            if target_id is not None:
                self.scroll_to_card(target_id)
            self.active_card_id = target_id

    # --------------------------------------------------------------- output
    def default_export_name(self):
        if self.current_file_path:
            return os.path.splitext(os.path.basename(self.current_file_path))[0]
        return "transcript"

    def export_srt(self):
        if not self.backend.segments:
            messagebox.showinfo("Export", "No transcription available to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".srt", initialfile=self.default_export_name(),
                                            filetypes=[("SRT Subtitles", "*.srt")])
        if path:
            self.backend.export_srt(path)
            self.flash_button(self.btn_srt, "✓  Saved", "⇪  SRT")

    def export_json(self):
        if not self.backend.segments:
            messagebox.showinfo("Export", "No transcription available to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile=self.default_export_name(),
                                            filetypes=[("JSON Data", "*.json")])
        if path:
            self.backend.export_json(path)
            self.flash_button(self.btn_json, "✓  Saved", "⇪  JSON")

    def copy_transcript(self):
        with self.backend.data_lock:
            lines = [f"[{self.backend.format_time(s['start'])}] {s['speaker']}: {s['text']}"
                     for s in self.backend.segments]
        if not lines:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.flash_button(self.btn_copy, "✓  Copied", "⧉  Copy")

    def flash_button(self, button, temp_text, original_text):
        button.configure(text=temp_text)
        self.after(1500, lambda: button.configure(text=original_text))

    # -------------------------------------------------------------- insights
    def reset_insights(self):
        for v in self.tile_values.values():
            v.configure(text="—")
        for child in self.chips_frame.winfo_children():
            child.destroy()
        self.lbl_insight_hint = ctk.CTkLabel(
            self.chips_frame, text="Transcribe a file, then click “Generate insights”.",
            font=ctk.CTkFont(size=13), text_color=TEXT_DIM)
        self.lbl_insight_hint.pack(anchor="w")

    def generate_summary(self):
        self.btn_summary.configure(state="disabled", text="Analyzing…")

        def work():
            try:
                data = self.backend.generate_summary()
            except Exception:
                data = None
            self.after(0, lambda d=data: self.show_summary(d))

        threading.Thread(target=work, daemon=True).start()

    def show_summary(self, data):
        self.btn_summary.configure(state="normal", text="Regenerate")
        for child in self.chips_frame.winfo_children():
            child.destroy()
        if not data:
            ctk.CTkLabel(self.chips_frame, text="No transcription available to summarize.",
                         font=ctk.CTkFont(size=13), text_color=TEXT_MUTED).pack(anchor="w")
            return

        words = data["word_count"]
        speakers = len({d["speaker"] for d in self.card_elements.values()})
        wpm = int(round(words / (self.media_duration / 60.0))) if self.media_duration > 0 else 0
        self.tile_values["Words"].configure(text=f"{words:,}")
        self.tile_values["Duration"].configure(text=self.backend.format_time(self.media_duration))
        self.tile_values["Speakers"].configure(text=str(speakers))
        self.tile_values["Pace (wpm)"].configure(text=str(wpm) if wpm else "—")

        for i, kw in enumerate(data["keywords"]):
            chip = ctk.CTkLabel(self.chips_frame, text=f"  {kw.title()}  ", height=30, corner_radius=15,
                                fg_color="#0B2A38", text_color=ACCENT_CYAN, font=ctk.CTkFont(size=13, weight="bold"))
            chip.grid(row=i // 5, column=i % 5, padx=(0, 8), pady=(0, 8), sticky="w")


if __name__ == "__main__":
    app = SVTApp()
    app.mainloop()