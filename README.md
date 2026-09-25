# ⚡ SVT Studio (Simple Video Transcriber)

A modern, hardware-accelerated desktop transcription application built with Python and CustomTkinter. This application allows users to effortlessly convert audio and video files into synchronized text offline. Featuring batch processing, real-time audio playback synchronization, in-app speaker editing, and automated exports to JSON and SRT, it is designed for video editors, content creators, and transcriptionists.

## ✨ Features

* ⚡ **Offline AI Transcription:** Utilizes local Whisper AI models (Tiny through Turbo) for completely private, fast transcription without relying on cloud APIs.
* 📑 **Batch Processing:** Load multiple media files (.mp4, .mkv, .wav, etc.) into a continuous queue for automated, hands-off processing.
* 🎯 **Real-Time Sync:** "Karaoke-style" word-level highlighting tracks exactly what is being said during audio playback for precise reviewing.
* ✏️ **Interactive Editing:** Click-to-edit speaker tags allow you to instantly rename speakers (e.g., SPEAKER_01 to John) across the entire transcript.
* 🔍 **Search & Filter:** A real-time search bar dynamically filters the transcript cards to instantly isolate specific keywords or speakers.
* 💾 **Automated Exports:** Automatically generates and saves `.srt` subtitle files and structured `.json` data dumps into a dedicated `Transcripts/` folder.
* 💡 **AI Analytics:** Automatically generates quick summaries and extracts primary keywords from your transcribed media.
* 📱 **Remote Notifications:** Optional Pushover API integration triggers mobile alerts when a lengthy batch queue finishes processing.

## 📥 Download & Installation

1. Go to the **Releases** section on the right side of this page.
2. Download the latest `SVT_Studio.exe` file.
3. Ensure **FFmpeg** is installed on your system and added to your PATH.
4. Double-click `SVT_Studio.exe` to launch—no Python installation required! *(To run from source, install the requirements and run `python gui.py`)*.

## 🖼️ Visual Preview

<img width="2559" height="1439" alt="image" src="https://github.com/user-attachments/assets/bba12d43-9186-4b0d-b7c0-66a945ad5e17" />

## 🚀 How To Use

* **Configuring:** Use the collapsible sidebar on the left to select your preferred AI Model (Tiny to Turbo) and Compute Hardware (CPU or CUDA).
* **Importing:** Click **Import Media** to load your video or audio files. The batch queue will automatically begin extracting and normalizing the audio.
* **Reviewing:** Use the footer playback controls or the timeline slider to scrub through the audio. The transcript will highlight dynamically as it plays.
* **Editing:** Click on any speaker label to rename them. Use the search bar at the top to filter the visible transcript cards. 
* **Exporting:** Check the auto-generated `Transcripts/` folder in your root directory for your completed files, or use the **Export SRT** / **Export JSON** buttons in the top right to save copies manually.

## 📁 Project Structure

```text
SVT-Studio/
├── Transcripts             # Auto-generated directory for output files
├── .gitignore                # Git exclusion rules
├── gui.py                    # Frontend CustomTkinter UI & consumer queue
├── LICENSE                   # Usage terms and restrictions
├── main.py                   # Backend Whisper AI & FFmpeg producer pipeline
├── README.md                 # Project documentation
├── requirements.txt          # Python dependencies
└── SVT Icon.ico              # Application branding icon
```

## 💻 Built With

* **CustomTkinter** - UI Framework
* **Faster-Whisper** - AI Transcription Engine
* **Pygame** - Audio Playback & Synchronization
* **FFmpeg** - Media Processing & Normalization

## 📄 License & Terms 

SVT Studio is distributed under custom terms allowing free personal use while restricting modification and redistribution. See the full [LICENSE](LICENSE.md) file for details.
