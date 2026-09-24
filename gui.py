import os
import threading
import customtkinter as ctk
from tkinter import filedialog, messagebox
import pygame
from main import SVTBackend

# --- THEME PALETTE ---
BG_CANVAS = "#070B12"
CARD_BG = "#0F172A"
BORDER_INACTIVE = "#1E293B"
ACCENT_CYAN = "#22D3EE"
TEXT_MUTED = "#94A3B8"

ctk.set_appearance_mode("dark")

class SVTApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SVT Studio")
        self.geometry("1180x750")
        self.configure(fg_color=BG_CANVAS)
        
        pygame.mixer.init()
        self.backend = SVTBackend()
        
        self.is_transcribing = False
        self.is_playing = False
        self.media_duration = 0.0
        self.playback_offset = 0.0
        self.active_card_id = None
        self.card_elements = {}
        
        self.current_file_path = None
        self.current_audio_path = None 
        self.file_queue = []

        self.setup_layout()
        self.poll_playback()
        
        self.bind("<Up>", lambda e: self.cards_scroll._parent_canvas.yview_scroll(-1, "units"))
        self.bind("<Down>", lambda e: self.cards_scroll._parent_canvas.yview_scroll(1, "units"))

    def destroy(self):
        """Override to ensure cache teardown on app exit."""
        if self.current_audio_path and os.path.exists(self.current_audio_path):
            pygame.mixer.music.unload()
            try:
                os.remove(self.current_audio_path)
            except OSError:
                pass
        super().destroy()

    def setup_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ================= 1. SIDEBAR =================
        sidebar = ctk.CTkFrame(self, width=240, fg_color="#090E17", corner_radius=0)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(sidebar, text="⚡ SVT Studio", font=ctk.CTkFont(size=22, weight="bold"), text_color="#FFFFFF").pack(anchor="w", padx=24, pady=(28, 20))

        ctk.CTkButton(
            sidebar, text="⊕  Import Media", font=ctk.CTkFont(weight="bold", size=14),
            fg_color=ACCENT_CYAN, text_color="#000000", hover_color="#06B6D4",
            height=42, corner_radius=8, command=self.upload_file
        ).pack(fill="x", padx=20, pady=(0, 24))

        ctk.CTkLabel(sidebar, text="CONFIGURATION", font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT_MUTED).pack(anchor="w", padx=24, pady=(10, 4))
        
        hw_config = self.backend.get_optimal_hardware()
        ctk.CTkLabel(sidebar, text=f"System: {hw_config['description']}", font=ctk.CTkFont(size=10), text_color=ACCENT_CYAN).pack(anchor="w", padx=24, pady=(0, 10))

        ctk.CTkLabel(sidebar, text="AI Model", font=ctk.CTkFont(size=13), text_color="#E2E8F0").pack(anchor="w", padx=24, pady=(8, 2))
        self.model_var = ctk.StringVar(value=hw_config['model'])
        ctk.CTkOptionMenu(sidebar, values=["tiny", "base", "small", "medium", "large-v3", "turbo"], variable=self.model_var, fg_color=CARD_BG, button_color="#1E293B").pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(sidebar, text="Hardware", font=ctk.CTkFont(size=13), text_color="#E2E8F0").pack(anchor="w", padx=24, pady=(8, 2))
        self.device_var = ctk.StringVar(value=hw_config['device'])
        ctk.CTkOptionMenu(sidebar, values=["cpu", "cuda"], variable=self.device_var, fg_color=CARD_BG, button_color="#1E293B").pack(fill="x", padx=20, pady=(0, 20))

        ctk.CTkLabel(sidebar, text="⚙  Settings", text_color=TEXT_MUTED, font=ctk.CTkFont(size=13)).pack(side="bottom", anchor="w", padx=24, pady=24)

        # ================= 2. MAIN WORKSPACE =================
        workspace = ctk.CTkFrame(self, fg_color="transparent")
        workspace.grid(row=0, column=1, sticky="nsew", padx=24, pady=(20, 10))
        workspace.grid_rowconfigure(2, weight=1)
        workspace.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(workspace, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        
        self.lbl_title = ctk.CTkLabel(header, text="No Media Loaded", font=ctk.CTkFont(size=18, weight="bold"), text_color="#FFFFFF")
        self.lbl_title.pack(side="left", anchor="w")
        
        ctk.CTkButton(header, text="⇪  Export JSON", width=110, height=32, fg_color="transparent", border_width=1, border_color=BORDER_INACTIVE, text_color="#FFFFFF", hover_color=CARD_BG, command=self.export_json).pack(side="right")
        ctk.CTkButton(header, text="⇪  Export SRT", width=110, height=32, fg_color="transparent", border_width=1, border_color=BORDER_INACTIVE, text_color="#FFFFFF", hover_color=CARD_BG, command=self.export_srt).pack(side="right", padx=(0, 10))

        meta_bar = ctk.CTkFrame(workspace, fg_color="transparent")
        meta_bar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        
        self.lbl_meta = ctk.CTkLabel(meta_bar, text="DURATION: --:--   •   STATUS: READY", font=ctk.CTkFont(size=11, weight="bold"), text_color=ACCENT_CYAN)
        self.lbl_meta.pack(side="left")

        self.transcription_progress = ctk.CTkProgressBar(meta_bar, fg_color=BORDER_INACTIVE, progress_color=ACCENT_CYAN, height=6, width=150)
        self.transcription_progress.set(0)

        self.tabview = ctk.CTkTabview(
            workspace, fg_color="transparent",
            segmented_button_fg_color="#090E17", segmented_button_selected_color="#1E293B",
            segmented_button_selected_hover_color="#334155", segmented_button_unselected_hover_color="#0F172A",
            text_color=TEXT_MUTED
        )
        self.tabview.grid(row=2, column=0, sticky="nsew")
        self.tabview._segmented_button.configure(font=ctk.CTkFont(size=13, weight="bold"))

        self.tab_transcript = self.tabview.add("📑 Transcript")
        self.tab_insights = self.tabview.add("💡 AI Insights")
        
        self.tab_transcript.grid_rowconfigure(0, weight=0)
        self.tab_transcript.grid_rowconfigure(1, weight=1)
        self.tab_transcript.grid_columnconfigure(0, weight=1)
        
        self.tab_insights.grid_rowconfigure(1, weight=1)
        self.tab_insights.grid_columnconfigure(0, weight=1)

        # --- Search Bar Component ---
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", self.filter_transcripts)
        
        self.search_entry = ctk.CTkEntry(
            self.tab_transcript, placeholder_text="🔍 Search transcript...",
            textvariable=self.search_var, font=ctk.CTkFont(size=14),
            height=38, fg_color=CARD_BG, border_color=BORDER_INACTIVE
        )
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))

        self.cards_scroll = ctk.CTkScrollableFrame(self.tab_transcript, fg_color=BG_CANVAS, corner_radius=0)
        self.cards_scroll.grid(row=1, column=0, sticky="nsew")
        
        self.lbl_placeholder = ctk.CTkLabel(self.cards_scroll, text="Click 'Import Media' to begin transcription.", text_color=TEXT_MUTED, font=ctk.CTkFont(size=14))
        self.lbl_placeholder.pack(pady=100)

        self.btn_summary = ctk.CTkButton(
            self.tab_insights, text="Generate AI Analytics", font=ctk.CTkFont(weight="bold"), 
            fg_color="#1E293B", hover_color="#334155", text_color="#FFFFFF", height=40, command=self.generate_summary
        )
        self.btn_summary.grid(row=0, column=0, pady=(20, 10), padx=20, sticky="ew")

        self.ai_textbox = ctk.CTkTextbox(self.tab_insights, font=ctk.CTkFont(size=15), fg_color=CARD_BG, text_color="#E2E8F0", wrap="word", corner_radius=8)
        self.ai_textbox.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.ai_textbox.insert("1.0", "AI Analytics will appear here...")

        # ================= 3. FOOTER PLAYER =================
        player_bar = ctk.CTkFrame(self, height=75, fg_color="#090E17", corner_radius=0, border_width=1, border_color=BORDER_INACTIVE)
        player_bar.grid(row=1, column=1, sticky="ew")
        player_bar.pack_propagate(False)

        ctrl_frame = ctk.CTkFrame(player_bar, fg_color="transparent")
        ctrl_frame.pack(side="left", padx=20)

        ctk.CTkButton(ctrl_frame, text="⏪", width=36, height=36, fg_color="transparent", text_color="#FFFFFF", hover_color=CARD_BG, command=lambda: self.seek_relative(-5)).pack(side="left", padx=2)
        self.btn_play = ctk.CTkButton(ctrl_frame, text="▶", width=42, height=42, fg_color=ACCENT_CYAN, text_color="#000000", hover_color="#06B6D4", corner_radius=21, font=ctk.CTkFont(size=16, weight="bold"), command=self.toggle_playback)
        self.btn_play.pack(side="left", padx=8)
        ctk.CTkButton(ctrl_frame, text="⏩", width=36, height=36, fg_color="transparent", text_color="#FFFFFF", hover_color=CARD_BG, command=lambda: self.seek_relative(5)).pack(side="left", padx=2)

        progress_frame = ctk.CTkFrame(player_bar, fg_color="transparent")
        progress_frame.pack(side="left", fill="x", expand=True, padx=15)

        self.timeline_slider = ctk.CTkSlider(progress_frame, from_=0, to=100, fg_color="#1E293B", progress_color=ACCENT_CYAN, button_color="#FFFFFF", command=self.on_slider_seek)
        self.timeline_slider.set(0)
        self.timeline_slider.pack(fill="x", expand=True)

        self.lbl_time = ctk.CTkLabel(player_bar, text="00:00 / 00:00", font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_MUTED)
        self.lbl_time.pack(side="right", padx=24)

    # ================= WORKFLOW & TRANSCRIPTION =================
    def upload_file(self):
        file_paths = filedialog.askopenfilenames(filetypes=[("Media Files", "*.mp4 *.mkv *.mov *.mp3 *.wav")])
        if not file_paths: return
        self.file_queue.extend(file_paths)
        if not self.is_transcribing: self.process_next_in_queue()

    def process_next_in_queue(self):
        if self.current_audio_path and os.path.exists(self.current_audio_path):
            pygame.mixer.music.unload() 
            try: os.remove(self.current_audio_path)
            except OSError: pass
                
        if not self.file_queue:
            self.lbl_meta.configure(text="STATUS: ALL QUEUED FILES COMPLETE")
            return
            
        self.current_file_path = self.file_queue.pop(0)
        self.lbl_title.configure(text=os.path.basename(self.current_file_path))
        self.lbl_placeholder.pack_forget()
        
        self.search_var.set("")
        for child in self.cards_scroll.winfo_children(): child.destroy()
        self.card_elements.clear()

        self.is_transcribing = True
        queue_count = len(self.file_queue)
        queue_text = f" ({queue_count} remaining)" if queue_count > 0 else ""
        self.lbl_meta.configure(text=f"STATUS: EXTRACTING AUDIO...{queue_text}")
        
        self.transcription_progress.pack(side="left", padx=20)
        self.transcription_progress.set(0)
        
        threading.Thread(target=self.run_pipeline, args=(self.current_file_path,), daemon=True).start()

    def run_pipeline(self, file_path):
        try:
            audio_file = self.backend.prepare_audio_for_playback(file_path)
            device = self.device_var.get()
            compute = "float16" if device == "cuda" else "int8"
            
            self.backend.transcribe(
                file_path, model_size=self.model_var.get(), device=device, compute_type=compute,
                callback_status=lambda txt, prog: self.after(0, lambda: self.lbl_meta.configure(text=f"STATUS: {txt.upper()}")),
                callback_segment=lambda seg, prog: self.after(0, lambda: self.handle_new_segment(seg, prog)),
                callback_done=lambda segs, dur: self.after(0, lambda: self.on_transcription_finished(audio_file, dur)),
                callback_error=lambda err: self.after(0, lambda: self.on_error(err))
            )
        except Exception as e:
            self.after(0, lambda: self.on_error(str(e)))

    def handle_new_segment(self, seg, prog):
        self.render_card(seg)
        self.transcription_progress.set(prog)
        self.scroll_to_card(seg['id'])

    def render_card(self, seg):
        card_border = ctk.CTkFrame(self.cards_scroll, fg_color=BORDER_INACTIVE, corner_radius=8)
        card_border.pack(fill="x", pady=6, padx=4)

        card_inner = ctk.CTkFrame(card_border, fg_color=CARD_BG, corner_radius=7)
        card_inner.pack(fill="both", expand=True, padx=1, pady=1)

        left_col = ctk.CTkFrame(card_inner, fg_color=CARD_BG, width=110)
        left_col.pack(side="left", fill="y", padx=(14, 10), pady=12)
        left_col.pack_propagate(False)

        time_str = f"[{self.backend.format_time(seg['start'])}]"
        ctk.CTkLabel(left_col, text=time_str, font=ctk.CTkFont(size=12, weight="bold"), text_color=ACCENT_CYAN).pack(anchor="w")
        
        # Click-to-edit Speaker Label
        lbl_speaker = ctk.CTkLabel(left_col, text=seg['speaker'], font=ctk.CTkFont(size=10, weight="bold"), text_color=TEXT_MUTED, cursor="hand2")
        lbl_speaker.pack(anchor="w", pady=(2, 0))

        lbl_text = ctk.CTkLabel(card_inner, text=seg['text'], font=ctk.CTkFont(size=13), text_color="#E2E8F0", justify="left", wraplength=680)
        lbl_text.pack(side="left", fill="both", expand=True, padx=(5, 14), pady=12)

        for elem in (card_border, card_inner, left_col, lbl_text):
            elem.bind("<Button-1>", lambda e, s=seg['start']: self.seek_to(s))
            
        lbl_speaker.bind("<Button-1>", lambda e, s_id=seg['id']: self.prompt_rename_speaker(s_id))

        self.card_elements[seg['id']] = {
            "border": card_border, "inner": card_inner, "speaker_lbl": lbl_speaker,
            "start": seg['start'], "end": seg['end'],
            "text": seg['text'].lower(), "speaker": seg['speaker']
        }
        
    def prompt_rename_speaker(self, seg_id):
        if seg_id not in self.card_elements: return
        old_name = self.card_elements[seg_id]["speaker"]
        dialog = ctk.CTkInputDialog(text=f"Rename '{old_name}' to:", title="Edit Speaker")
        new_name = dialog.get_input()
        
        if new_name and new_name.strip() and new_name != old_name:
            new_name = new_name.strip()
            with self.backend.data_lock:
                for s in self.backend.segments:
                    if s['speaker'] == old_name: s['speaker'] = new_name
            for s_id, data in self.card_elements.items():
                if data["speaker"] == old_name:
                    data["speaker"] = new_name
                    data["speaker_lbl"].configure(text=new_name)
            self.auto_save_current_files()

    def filter_transcripts(self, *args):
        query = self.search_var.get().lower()
        for seg_id in sorted(self.card_elements.keys()):
            data = self.card_elements[seg_id]
            card = data["border"]
            if not query or query in data["text"] or query in data["speaker"].lower():
                card.pack(fill="x", pady=6, padx=4)
            else:
                card.pack_forget()

    def scroll_to_card(self, target_id):
        if target_id not in self.card_elements: return
        widget = self.card_elements[target_id]["border"]
        canvas = self.cards_scroll._parent_canvas
        self.update_idletasks() 
        widget_y = widget.winfo_y()
        widget_h = widget.winfo_height()
        canvas_h = canvas.winfo_height()
        bbox = canvas.bbox("all")
        if not bbox: return
        inner_h = bbox[3] - bbox[1]
        if inner_h > canvas_h:
            target_y = widget_y - (canvas_h / 2) + (widget_h / 2)
            target_y = max(0, min(target_y, inner_h - canvas_h))
            fraction = target_y / inner_h
            canvas.yview_moveto(fraction)
            
    def auto_save_current_files(self):
        if self.current_file_path:
            project_root = os.getcwd() 
            transcript_dir = os.path.join(project_root, "Transcripts")
            os.makedirs(transcript_dir, exist_ok=True)
            
            file_name = os.path.splitext(os.path.basename(self.current_file_path))[0]
            auto_save_srt = os.path.join(transcript_dir, f"{file_name}.srt")
            auto_save_json = os.path.join(transcript_dir, f"{file_name}.json")
            
            self.backend.export_srt(auto_save_srt)
            self.backend.export_json(auto_save_json)

    def on_transcription_finished(self, audio_file, duration):
        self.is_transcribing = False
        self.media_duration = duration
        self.lbl_meta.configure(text=f"DURATION: {self.backend.format_time(duration)}   •   STATUS: COMPLETE")
        self.timeline_slider.configure(to=duration)
        self.current_audio_path = audio_file  
        self.transcription_progress.pack_forget()
        
        pygame.mixer.music.load(audio_file)
        self.playback_offset = 0.0
        self.update_time_display(0)
        
        self.auto_save_current_files()
        if self.file_queue: self.process_next_in_queue()

    def on_error(self, error_msg):
        self.is_transcribing = False
        self.lbl_meta.configure(text=f"STATUS: ERROR")
        messagebox.showerror("Processing Error", f"An error occurred:\n{error_msg}")
        if self.file_queue: self.process_next_in_queue()

    # ================= PLAYBACK & REAL-TIME SYNC =================
    def toggle_playback(self):
        if not self.media_duration: return
        if self.is_playing:
            self.playback_offset = self.get_current_time()
            pygame.mixer.music.pause()
            self.btn_play.configure(text="▶")
            self.is_playing = False
        else:
            pygame.mixer.music.play(start=self.playback_offset)
            self.btn_play.configure(text="❚❚")
            self.is_playing = True

    def seek_to(self, seconds):
        if not self.media_duration: return
        self.playback_offset = seconds
        pygame.mixer.music.play(start=seconds)
        if not self.is_playing: pygame.mixer.music.pause()
        self.timeline_slider.set(seconds)
        self.update_time_display(seconds)
        self.sync_active_card(seconds)

    def seek_relative(self, delta):
        cur = self.get_current_time()
        target = max(0.0, min(self.media_duration, cur + delta))
        self.seek_to(target)

    def on_slider_seek(self, value):
        self.seek_to(float(value))

    def get_current_time(self):
        if not self.is_playing: return self.playback_offset
        pos = pygame.mixer.music.get_pos()
        if pos == -1: return self.playback_offset
        return self.playback_offset + (pos / 1000.0)

    def update_time_display(self, cur_sec):
        cur_str = self.backend.format_time(cur_sec)
        total_str = self.backend.format_time(self.media_duration)
        self.lbl_time.configure(text=f"{cur_str} / {total_str}")

    def poll_playback(self):
        if self.is_playing:
            cur_time = self.get_current_time()
            self.timeline_slider.set(cur_time)
            self.update_time_display(cur_time)
            self.sync_active_card(cur_time)
            if cur_time >= self.media_duration:
                self.is_playing = False
                self.btn_play.configure(text="▶")
                self.playback_offset = 0.0
        self.after(100, self.poll_playback)

    def sync_active_card(self, cur_time):
        target_id = None
        for seg_id, data in self.card_elements.items():
            if cur_time >= data["start"]: target_id = seg_id
            else: break 
        if target_id != self.active_card_id:
            if self.active_card_id and self.active_card_id in self.card_elements:
                self.card_elements[self.active_card_id]["border"].configure(fg_color=BORDER_INACTIVE)
            if target_id and target_id in self.card_elements:
                self.card_elements[target_id]["border"].configure(fg_color=ACCENT_CYAN)
                self.scroll_to_card(target_id) 
            self.active_card_id = target_id

    # ================= OUTPUT =================
    def export_srt(self):
        if not self.backend.segments:
            messagebox.showinfo("Export", "No transcription available to export.")
            return
        save_path = filedialog.asksaveasfilename(defaultextension=".srt", filetypes=[("SRT Subtitles", "*.srt")])
        if save_path:
            self.backend.export_srt(save_path)
            messagebox.showinfo("Export Complete", f"Saved to {os.path.basename(save_path)}")

    def export_json(self):
        if not self.backend.segments:
            messagebox.showinfo("Export", "No transcription available to export.")
            return
        save_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Data", "*.json")])
        if save_path:
            self.backend.export_json(save_path)
            messagebox.showinfo("Export Complete", f"Saved to {os.path.basename(save_path)}")

    def generate_summary(self):
        self.ai_textbox.delete("1.0", "end")
        self.ai_textbox.insert("end", "Generating summary...\n")
        summary_data = self.backend.generate_summary()
        if not summary_data:
            self.ai_textbox.delete("1.0", "end")
            self.ai_textbox.insert("end", "No transcription available to summarize.")
            return
        summary_text = f"\n--- Transcription Analytics ---\n\n"
        summary_text += f"Total Words: {summary_data['word_count']}\n\n"
        summary_text += f"Primary Keywords: {', '.join(summary_data['keywords']).title()}\n"
        self.ai_textbox.delete("1.0", "end")
        self.ai_textbox.insert("end", summary_text)

if __name__ == "__main__":
    app = SVTApp()
    app.mainloop()