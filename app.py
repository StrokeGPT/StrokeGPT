import os
import sys
import io
import re
import atexit
import threading
import time
import random
from collections import deque
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, send_file, send_from_directory

from settings_manager import SettingsManager
from handy_controller import HandyController
from llm_service import LLMService
from audio_service import AudioService
from background_modes import AutoModeThread, auto_mode_logic, milking_mode_logic, edging_mode_logic
from script_library import ScriptLibrary

# --- INITIALIZATION ---
app = Flask(__name__)
LLM_URL = "http://127.0.0.1:11434/api/chat"

settings = SettingsManager(settings_file_path="my_settings.json")
settings.load()

# Load pattern library
script_paths = [
    Path(__file__).with_name("static").joinpath("complete_script_library_with_meta.json"),
    Path("/mnt/data/complete_script_library_with_meta.json"),
]
scripts = ScriptLibrary(script_paths)
llm = LLMService(url=LLM_URL)

handy = HandyController(settings.handy_key, llm_service=llm) # <-- Pass LLM service here
handy.update_settings(getattr(settings, "min_speed", 0),
                      getattr(settings, "max_speed", 100),
                      getattr(settings, "min_depth", 0),
                      getattr(settings, "max_depth", 100))

audio = AudioService()
if settings.elevenlabs_api_key:
    if audio.set_api_key(settings.elevenlabs_api_key):
        audio.fetch_available_voices()
        audio.configure_voice(settings.elevenlabs_voice_id, True)

# --- IN-MEMORY STATE ---
chat_history = deque(maxlen=50)
messages_for_ui = deque()
auto_mode_active_task = None
current_reply_length = settings.reply_length
use_long_term_memory = True
calibration_pos_mm = 0.0
user_signal_event = threading.Event()
mode_message_queue = deque(maxlen=5)
edging_start_time = None
special_persona_mode = None
special_persona_interactions_left = 0
last_pattern_name = None

# Zone lock and stroke policy
_zone_lock = {"zone": None, "expires_at": 0.0, "no_connectors": False}
_allow_full_until = 0.0  # timestamp when "full strokes" permission expires

# Telemetry
move_telemetry = deque(maxlen=50)

# --- CONSTANTS ---
SNAKE_ASCII = "<pre>...</pre>"
DOOM_SLAYER_ASCII = r"""<pre>
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⢾⠍⡉⠉⠙⣿⣆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣠⣴⠾⠿⠽⢷⣶⣤⡀⠀⠀⠀⠀⠀⠀⠀⢀⣟⡟⣠⣿⣶⡀⣷⡻⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠴⡟⡋⡀⠀⣀⣀⠀⠀⠉⠛⣦⡀⠀⠀⠀⠀⠀⠀⢿⣅⣽⣿⣿⣷⣿⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡴⠃⢀⢾⣿⣿⣿⣯⣬⣽⣿⣀⡀⠈⠙⣆⠀⠀⠀⠀⢀⣸⣯⣿⣾⡷⢻⣿⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣜⢁⠁⣾⡿⣙⠿⣯⣭⣍⣹⠼⠋⠁⣴⠀⢘⣧⠀⠀⡴⢛⣭⢟⠽⠋⢠⣼⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⢻⠘⢸⡿⢷⣬⣧⡀⠀⠀⠀⢀⣤⠾⢿⡇⠘⣿⡆⣸⠛⣿⡿⣟⡀⠀⡾⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣾⣦⣿⣿⡄⠈⢿⢿⣷⣶⡾⠋⠁⠀⣸⠇⡰⠛⢷⣷⣻⡿⠺⣿⣿⠽⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⣀⣀⣀⠀⣴⠏⠀⣿⠙⢻⣿⣄⠈⠀⠸⠀⠉⠀⣠⣾⠟⢀⣧⡇⠀⢽⣿⣿⣬⣼⣿⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣰⠚⣿⣿⣿⣿⣿⡿⠟⢛⣰⣿⣧⣷⣝⡿⣷⣞⢷⣄⣲⣾⣿⡃⢰⡿⡟⢀⣴⣿⣿⣿⣯⡿⠿⣿⣶⣤⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⠞⢁⣼⣿⣿⣿⡟⠋⠁⣉⣽⣿⣿⣿⣿⣿⣽⣯⣿⡄⠉⠁⢷⣬⣹⣿⣿⣤⡾⠁⣸⣿⣿⡟⠁⠀⠀⢹⣿⣿⣷⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣾⡷⠞⣫⣾⣿⣿⣿⣧⡀⣤⠀⠈⣻⣿⣿⣿⣿⣿⣿⣿⣷⣖⠀⠘⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠃⠋⠻⢤⣅⡺⢦⡀⠳⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣰⣿⣯⣴⠞⠁⣀⣿⣿⣿⣿⣷⣄⣤⠤⢊⣿⣿⣿⣿⣿⣿⣿⣿⣯⣴⣴⣶⣿⣿⠟⣸⣿⣿⣿⣿⡏⡆⠀⢠⣤⣠⣥⠀⡟⣶⣿⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣽⡿⣿⣏⡀⠀⠹⣟⣿⡿⣿⣿⣋⣶⣺⡽⣿⣏⣅⠛⠂⠴⠶⠿⠿⠃⠈⠉⠻⣷⣶⣿⣿⣿⣿⡿⠀⣿⡄⠈⣷⣮⠙⢀⡿⠘⢻⣇⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢸⣧⢻⡶⠀⠀⠘⢿⣿⣿⣿⣿⣿⠋⠉⠀⠀⠉⠻⠿⠶⠶⠶⠦⠴⠞⠛⠷⠗⠈⠛⢿⣿⣿⡿⢁⣼⠯⠄⠀⠀⠀⣠⡞⠁⣠⣾⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢸⣻⣾⣷⡀⢐⠀⣿⣿⣿⣿⣿⠁⠀⠠⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡁⠰⣾⣿⠀⠀⠈⢻⣿⣅⢿⣇⠀⠀⠀⠀⢀⣿⡟⠀⡷⢿⢿⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⣿⡎⣤⣌⡰⣿⣿⣿⣿⣟⠀⠠⠀⢀⡀⠀⠂⠀⠀⠉⠉⠉⠈⠉⠙⢾⣭⡤⠂⠀⠀⠹⣿⣎⣿⣶⣒⣿⣷⣿⣯⣮⡵⣿⣾⣿⡀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢻⢿⠛⣿⠛⠛⢿⣿⣿⣃⢀⣀⣀⠀⣀⣤⣾⠓⠶⠖⠷⣤⣄⡀⠀⠀⠀⠀⠀⠀⢠⣿⣿⣾⣿⣿⣿⠍⣩⣉⣿⡆⠰⣿⣭⡇⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡸⠾⢴⡇⠀⠀⢸⣿⣿⣯⣭⣿⣿⣿⡿⠛⠛⠛⠛⠛⠛⠛⠟⠻⣷⣶⣴⣶⣮⡴⠫⢾⣿⣿⣟⠉⣹⣿⣿⣿⣿⣷⣄⠸⢿⡇⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⣴⠋⡽⠁⡾⠀⠀⠀⣼⣿⣧⣁⣴⣶⠾⢿⣿⡶⠀⠒⠒⠂⠀⠀⠀⣰⣾⣧⣌⣉⠙⠂⢠⢿⣿⣿⣫⡿⠿⠋⠉⠈⠙⢻⣽⢧⠀⣽⣄⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⢀⣴⡻⣻⣼⣿⣰⠇⠀⠀⠀⠉⣁⣿⣟⢉⣼⣶⣶⡿⠿⣿⡟⠛⠛⠛⣷⣾⢿⣯⣤⣤⡉⠳⡶⢋⡞⣿⣿⣇⠀⠀⠙⠀⠀⠀⢀⣿⣫⠇⣈⣁⣣⡀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⣠⠎⠉⣰⣿⣿⣿⠉⢲⣤⠀⠀⠾⣿⣿⣳⣜⢿⡟⠫⢠⣶⣾⣷⣤⣤⣼⣯⡤⣤⣀⣻⠻⣿⣦⣠⠞⣼⣿⡿⢿⣤⡸⣷⣦⣤⣴⣿⣿⣯⠼⢥⣈⣿⡗⠶⢤⡀⠀⠀
⠀⠀⠀⠀⢰⡃⠀⣼⣿⣿⣿⣿⣷⣤⣁⣀⣤⣾⣿⣿⣿⣿⣿⣿⠷⣾⣟⣀⣫⣄⣀⣀⣠⣄⠘⢿⡤⠴⣷⡿⠃⠘⡽⣿⣃⠘⣿⣿⣿⣿⣿⣿⣿⠿⡿⠟⠀⠘⣝⢿⡆⠀⠻⣦⡀
⠀⠀⠀⢀⡏⢀⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⢳⣿⣯⣿⣿⣿⡿⣾⣿⡏⠙⣿⡉⠙⡍⠉⢿⣟⣴⠶⠾⢿⣟⠷⣿⡿⠿⣷⣾⡇⢻⣿⣿⡏⡴⠞⠻⣞⡍⠙⢫⣿⣧⠀⠀⠘⠃
⠀⠀⢀⣾⢿⡾⢷⣿⣿⠋⣿⣿⣿⣿⣿⣿⣿⡗⣼⢿⣿⣿⠘⣿⣿⣷⣼⣣⣶⠾⠿⠛⠶⣦⠚⣠⣴⣿⣿⠋⢰⣼⣯⠁⠐⢺⣿⣿⣮⣿⣿⣿⡟⠂⢀⣽⡓⡀⠒⢹⣿⠇⠀⣤⡀
⠀⠀⣿⠿⣾⣳⣼⣏⠛⠛⢿⣯⣶⣿⢋⣼⣿⢱⣟⣷⣮⠻⣷⠘⠿⣿⣭⣉⡉⣠⣤⣤⣄⣉⣉⣁⣾⡿⠟⣠⣾⡏⣡⠎⠀⢸⣿⡌⣿⣿⣿⣿⣟⡂⠠⢿⡅⢨⡏⣾⣟⠀⠀⠈⠁
⠀⢸⡿⠓⢀⣿⣿⣿⡷⣦⣼⠟⣹⡵⠛⢳⢟⣾⣿⣿⡿⠀⣿⠄⡀⣿⣯⠙⣿⡟⠛⢛⠛⣿⣿⡏⢉⡇⠀⢯⣿⡇⡅⢴⠀⢸⣾⡇⠸⣟⠹⣿⢿⡏⢰⣿⣆⣈⠁⣽⣿⠀⠀⠀⠀
- ⢀⡖⠘⠃⢠⣿⣯⡟⠻⣿⣻⡟⠃⠀⠀⠸⣿⢿⣿⣿⣿⣾⣿⣿⣿⣿⣿⣧⢸⣧⣤⣭⣤⣿⣿⡔⢿⣿⡿⣿⣿⣿⣷⣤⣠⣿⣿⠃⠀⣿⣇⣿⣿⣷⣿⣿⠿⢽⣷⣩⣿⠀⠀⠀⠀
+ ⢀⡖⠘⠃⢠⣿⣯⡟⠻⣿⣻⡟⠃⠀⠀⠸⣿⢿⣿⣿⣿⣾⣿⣿⣿⣿⣿⣧⢸⣧⣤⣭⣤⣿⣿⡔⢿⣿⡿⣿⣿⣿⣷⣤⣠⣿⣿⠃⠀⣿⣇⣿⣿⣷⣿⣿⠿⢽⣷⣩⣿⠀⠀⠀⠀
⣾⠁⣠⠹⣿⣿⡟⠻⣶⣿⢻⡇⠀⠀⠀⠀⠈⢹⡿⣿⣿⣿⣿⢟⣟⢿⢿⣿⣿⣿⡷⠶⠶⠶⠈⢯⡻⡄⢻⣿⢀⠙⢿⣿⣿⣷⡟⠁⢀⣴⢟⣺⣿⣿⣿⣥⣽⣶⣄⣈⣿⣿⠀⠀⠀⠀
⣭⠎⠿⢠⡟⢿⣿⣷⣽⣿⣼⡇⠀⠀⠀⠀⢠⣿⢿⡛⢿⡿⣿⡾⣿⡇⢠⣿⣿⡇⠀⠀⠀⠀⣈⢻⡖⢸⣿⢿⣾⢏⠟⠛⢿⣧⣀⣸⣴⡿⢻⣿⣻⣍⠉⣉⠛⣛⠛⠛⢿⡷⠀⠀⢀
- ⢳⣶⠖⠈⢿⣿⣛⠹⣿⣿⢸⡃⠀⠀⠀⣠⠟⣩⠞⠀⠈⣿⡟⣵⡿⠃⣼⣿⣿⠁⠐⠀⠘⠃⠉⣸⣇⠀⠹⣦⢻⣟⠀⠀⠀⠹⣿⣴⣯⣼⣿⣿⣿⣿⡄⣿⡀⢿⣰⡇⢸⡇⠀⠠⠋
+ ⢳⣶⠖⠈⢿⣿⣛⠹⣿⣿⢸⡃⠀⠀⠀⣠⠟⣩⠞⠀⠈⣿⡟⣵⡿⠃⣼⣿⣿⠁⠐⠀⠘⠃⠉⣸⣇⠀⠹⣦⢻⣟⠀⠀⠀⠹⣿⣴⣯⣼⣿⣿⣿⣿⡄⣿⡀⢿⣰⡇⢸⡇⠀⠠⠋
⠸⣹⡶⠀⢸⣿⣿⣿⣷⣛⢻⡇⠀⠀⢠⡷⠃⠁⠀⠀⠀⣿⠸⣿⠀⠠⣿⣿⣧⡀⠀⠀⠀⠀⢰⣿⣄⠁⠀⣹⢦⣿⣦⠀⠀⠀⣿⣿⣿⡏⣿⡏⡛⠟⢲⣶⢶⣾⣷⡭⣸⡴⠊⠀⠀
⠀⢹⡄⣄⡘⣿⣿⣿⣿⠹⡿⠁⠀⠀⣿⠇⠀⠀⠀⠀⡶⠘⡇⣿⡃⠂⣻⣿⣿⣿⣷⡄⠀⠀⠀⢸⣿⣝⡓⢰⣿⣾⡏⣿⣦⠀⠀⢹⣾⣿⡎⢰⣷⣓⠀⣼⣿⢸⣿⢹⡆⢿⠇⠀⠀⠀
⠀⠀⠙⠻⣿⣿⠧⠭⠭⠟⠁⠀⠀⣸⡽⢐⠀⠀⠀⢸⣇⣸⡷⣿⠃⠀⢿⣿⣿⣿⣿⣿⣷⣦⣿⣿⣯⡟⢺⣿⣿⣇⣸⡿⡇⠀⢀⡟⣿⡧⢸⣷⡌⢀⣿⣿⣼⣿⠮⣿⠋⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠈⠁⠀⠀⠀⠀⠀⢀⣟⣷⡿⠀⠀⠀⠀⠉⢸⡇⡷⠀⢀⠈⠻⣿⣿⢿⣿⢿⣿⣿⣿⣿⣷⣾⣿⣿⡇⠉⠀⠀⠀⢸⡇⢸⣿⡾⡿⣧⣼⣿⠵⣿⣇⡾⠁⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣾⣼⡛⢸⡄⠀⠀⠀⢸⣧⢳⣀⠀⠀⠀⣿⢋⡟⠈⢧⢻⣿⣿⣿⣿⣿⣿⣿⣷⡀⠀⠀⠀⠈⡇⠀⢯⡇⠀⠉⠙⠙⠉⠉⠋⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⢹⡸⣷⠀⠀⠀⠄⠻⢷⣄⣀⢀⣼⣣⠟⠀⠀⠈⢣⠹⣿⣿⣿⣿⣿⣿⠿⢷⡄⠀⠀⠀⠀⠀⢸⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⡈⢁⣽⣷⡆⠀⠀⠀⠀⢈⣽⣿⡿⠃⠀⠀⠀⠀⠀⠙⣌⢻⣿⣿⣿⣿⠀⠈⢿⣦⠓⠀⠀⠀⣸⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⠚⢿⣷⣿⣿⣯⣻⡄⠀⠀⢀⣾⠟⡿⠁⠀⠀⠀⠀⠀⠀⠀⠈⢦⡻⣿⣿⣷⡀⢠⣾⣫⡿⣬⡃⠆⠛⣧⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣻⣷⢾⣟⠛⠁⠉⢻⣿⣆⣠⡾⢿⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣇⣸⣿⣿⣾⣾⣿⠇⠀⠈⢙⡟⠿⢻⣆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣫⡾⠿⣦⣀⣀⣠⡿⢿⣏⡴⣿⡏⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⣿⣿⣿⣿⣤⣤⣤⡞⠁⢂⣹⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⣉⣿⡳⠀⠀⠈⠁⠀⠀⠈⢿⡄⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣿⢿⣿⣿⣿⣷⣄⠀⠀⠀⢀⡀⠘⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣰⣷⣻⣿⡟⠶⠶⠤⠤⠀⠀⠀⣸⣿⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⣿⣿⣿⣿⣿⣿⣶⣖⣾⠭⡁⠈⢿⣳⣦⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢰⣟⣿⣽⠋⣿⠃⢤⣭⣭⠀⠀⠀⣠⣟⣿⣆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣾⡿⣿⣿⣿⣿⣯⡥⠶⠀⣛⠀⢶⡿⡬⣷⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣶⡿⢱⣿⣰⡿⠿⠶⢭⣦⠀⠀⣰⡿⢁⢿⣾⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⣿⣢⣿⣿⣿⣿⣷⣶⠖⠛⠙⢷⣌⡉⠹⣷⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⢰⣿⣗⣿⣿⣿⡀⣶⣶⠀⣹⣷⣾⣿⣷⡼⣯⣿⣆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣾⢿⣿⣿⣿⣿⣿⣿⣿⣗⣼⠄⠀⣿⣷⣀⣿⢷⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠈⢹⣿⡿⢻⣿⣷⣽⣏⣰⣿⣿⣿⣷⣶⣧⢹⣷⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣭⠿⣿⣿⣿⣿⣿⣿⣿⣿⣥⣤⣾⠏⠻⡇⣿⡏⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⢿⣿⣿⣟⠻⠿⠿⠛⠹⣿⣿⣿⣿⣾⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⡇⣻⣿⣿⣿⣿⣿⣿⡍⠛⠛⠋⠁⠀⣀⢿⣿⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢸⡿⣿⣿⣿⣿⡗⠓⣤⠀⢀⣤⣿⢃⣸⠟⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⢿⠻⣿⣿⣿⣿⣿⣿⣏⣹⣧⢀⣾⡉⢸⣿⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣼⡿⣿⣿⣿⣛⠁⠘⡋⠙⣋⣥⣿⢾⡏⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢣⡜⣿⣿⣿⣿⡏⠛⢩⡉⠀⡛⢸⣿⢷⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⢀⣿⣷⣿⣿⣿⣯⣤⡤⡒⣛⣭⣭⢾⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⣿⢹⣿⣿⣿⣿⡦⠼⣷⠚⣩⠏⠹⣯⡀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⣿⡅⢠⣿⣿⣿⣧⣤⠾⠟⢛⣫⡵⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣳⢿⣿⣿⣿⣿⡶⣿⡞⠋⠀⠀⢻⣧⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⢰⣿⢡⢿⣿⣿⣿⣀⡀⠚⣠⣼⠁⢀⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣇⣼⣿⣿⣿⣿⣇⣼⣷⣶⣿⠟⠀⣿⣇⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⣞⠇⣶⣷⣬⣭⣉⣛⢛⣛⠉⣩⡷⢾⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡯⢽⣿⣿⣿⣿⣟⣉⣩⣤⡤⠶⠂⠸⣾⡄⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⣾⣿⡷⣿⣽⣾⣟⣿⣭⠈⠁⠀⣿⣠⣼⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣷⢦⣿⣿⣿⣿⣿⣯⣁⣾⣷⣶⣿⠣⣷⣵⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⢀⣾⢻⠤⠟⠓⠚⠻⢧⣀⠀⠀⠀⠙⣿⣿⣯⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⢻⣿⣿⢡⠿⠛⠋⠉⠩⠀⠀⠀⠒⠄⠞⣦⠀⠀⠀⠀⠀
⠀⠀⠀⠀⢸⣷⣴⢞⣏⣀⠀⡀⠀⣹⣦⠾⠟⢂⡍⠻⣷⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢨⠿⢛⣿⡶⢷⣤⣄⣀⣀⡀⢠⣴⣀⠠⡼⣿⡁⠀⠀⠀⠀
⠀⠀⠀⠀⢘⡃⢰⠀⡀⠀⠀⢀⡀⠀⠀⠈⠀⠀⢩⠈⣽⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣼⣿⣿⡀⣀⠒⣿⣿⠇⠀⠀⠀⠀⡀⡇⠘⣿⡀⠀⠀⠀
⠀⠀⠀⠀⠀⡇⢸⠈⠁⠀⠀⢸⡇⠀⡇⠀⢖⣔⣾⣾⡋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠑⣟⢿⣀⣿⣾⢿⣿⡇⠈⠁⠀⠀⠀⣿⣤⣧⠇⠀⠀⠀
</pre>"""
STOP_COMMANDS = {"stop", "hold", "halt", "pause", "freeze", "wait"}
AUTO_ON_WORDS = {"take over", "you drive", "auto mode"}
AUTO_OFF_WORDS = {"manual", "my turn", "stop auto"}
MILKING_CUES = {"i'm close", "make me cum", "finish me"}
EDGING_CUES = {"edge me", "start edging", "tease and deny"}

# ---------- DEPTH / RANGE CONSTRAINTS ----------
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

def _allowed_bounds():
    lo = float(getattr(settings, "min_depth", 0))
    hi = float(getattr(settings, "max_depth", 100))
    if lo > hi:
        lo, hi = hi, lo
    lo = _clamp(lo, 0, 100)
    hi = _clamp(hi, 0, 100)
    return lo, hi

def _zone_center(zone, lo, hi):
    span = hi - lo
    if zone == "tip": return hi - span * 0.08
    if zone in ("base", "deep"): return lo + span * 0.08
    if zone == "mid": return (lo + hi) / 2.0
    return (lo + hi) / 2.0

def enforce_move(sp, dp, rng, tag=None):
    sp = _clamp(float(sp), 0.0, 100.0)
    dp = _clamp(float(dp), 0.0, 100.0)
    rng = _clamp(float(rng), 5.0, 100.0)
    lo, hi = _allowed_bounds(); span = max(5.0, hi - lo)

    zone = None
    if tag:
        t = str(tag).lower()
        if "tip" in t: zone = "tip"
        elif any(k in t for k in ("deep", "throat", "base")): zone = "deep"
        elif any(k in t for k in ("mid", "shaft")): zone = "mid"
        elif "full" in t: zone = "full"

    if zone:
        center = _zone_center(zone, lo, hi)
        half = rng / 2.0
        dp = _clamp(center, lo + half, hi - half)
    else:
        rel = dp / 100.0
        dp = lo + rel * span
        half = rng / 2.0
        dp = _clamp(dp, lo + half, hi - half)
    return int(round(sp)), int(round(dp)), int(round(max(5.0, rng)))

# --- INTENT / POLICY ---
_TIP_WORDS = {"tip", "head", "glans", "just the tip"}
_MID_WORDS = {"mid", "middle", "shaft"}
_BASE_WORDS = {"base", "root", "deep", "throat"}
_FULL_WORDS = {"full", "long", "full strokes", "long strokes"}

def _set_zone_lock(zone: str, ttl_sec: int = 120, no_connectors: bool = False):
    z = (zone or "").lower()
    if z not in ("tip", "mid", "base", "deep", "full"): return False
    if z == "deep": z = "base"
    _zone_lock["zone"] = z
    _zone_lock["expires_at"] = time.time() + max(30, int(ttl_sec))
    _zone_lock["no_connectors"] = bool(no_connectors)
    return True

def _get_zone_lock():
    now = time.time()
    if _zone_lock["zone"] and _zone_lock["expires_at"] > now:
        return dict(_zone_lock)
    _zone_lock["zone"] = None; _zone_lock["expires_at"] = 0.0; _zone_lock["no_connectors"] = False
    return dict(_zone_lock)

def _reset_zone_lock():
    _zone_lock["zone"] = None; _zone_lock["expires_at"] = 0.0; _zone_lock["no_connectors"] = False

def _allow_full_for(ttl_sec: int = 120):
    global _allow_full_until
    _allow_full_until = time.time() + max(30, int(ttl_sec))

def _full_allowed():
    return time.time() < _allow_full_until

def parse_and_apply_intent(user_text: str):
    if not user_text: return None
    t = user_text.lower()
    status = None
    if any(w in t for w in _FULL_WORDS):
        _allow_full_for(120)
        status = "Full strokes permitted for 2 minutes."
        
    lock_triggers = (" only", "only ", "stay", "focus", "just", "nothing but", "keep to", "stick to")
    wants_lock = any(p in t for p in lock_triggers)
    
    def _maybe_lock(zone):
        return _set_zone_lock(zone, ttl_sec=150, no_connectors=wants_lock)

    # Silently set the lock without creating a chat message.
    if any(w in t for w in _TIP_WORDS):
        _maybe_lock("tip")
    elif any(w in t for w in _MID_WORDS):
        _maybe_lock("mid")
    elif any(w in t for w in _BASE_WORDS):
        _maybe_lock("base")
        
    return status

# --- CONTEXT / HELPERS ---
def get_current_context(chat_history: deque = None):
    global edging_start_time, special_persona_mode
    context = {
        'persona_desc': settings.persona_desc, 'current_mood': current_reply_length and "Curious" or "Curious",
        'user_profile': settings.user_profile, 'patterns': settings.patterns,
        'rules': settings.rules, 'last_stroke_speed': handy.last_relative_speed,
        'last_depth_pos': handy.last_depth_pos, 'use_long_term_memory': use_long_term_memory,
        'reply_length_preference': current_reply_length,
        'edging_elapsed_time': None, 'special_persona_mode': special_persona_mode,
        'allowed_depth_min': getattr(settings, "min_depth", 0),
        'allowed_depth_max': getattr(settings, "max_depth", 100),
        'zone_lock': _get_zone_lock().get("zone"),
        'zone_lock_no_connectors': _get_zone_lock().get("no_connectors"),
        'full_allowed': _full_allowed(),
        'recent_chat': list(chat_history)[-4:] if chat_history else []
    }
    if edging_start_time:
        elapsed_seconds = int(time.time() - edging_start_time)
        minutes, seconds = divmod(elapsed_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        context['edging_elapsed_time'] = f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"
    return context

def add_message_to_queue(text, add_to_history=True):
    if not text: return
    messages_for_ui.append(text)
    if add_to_history:
        clean_text = re.sub(r'<[^>]+>', '', text).strip()
        if clean_text:
            chat_history.append({"role": "assistant", "content": clean_text})
    threading.Thread(target=audio.generate_audio_for_text, args=(text,)).start()

def remember_pattern(name: str):
    global last_pattern_name
    if name: last_pattern_name = name

def log_move_telemetry(zone: str, dp: int, rng: int):
    lo, hi = _allowed_bounds()
    span = max(1.0, hi - lo)
    move_telemetry.append((time.time(), zone or "", int(dp), int(rng), int(span)))

def get_policy_callbacks():
    return {
        'send_message': add_message_to_queue,
        'get_context': get_current_context,
        'on_stop': lambda: None,
        'update_mood': lambda m: None,
        'user_signal_event': user_signal_event,
        'message_queue': mode_message_queue,
        'get_timings': lambda n: {
            'auto': (settings.auto_min_time, settings.auto_max_time),
            'milking': (settings.milking_min_time, settings.milking_max_time),
            'edging': (settings.edging_min_time, settings.edging_max_time)
        }.get(n, (3, 5)),
        'enforce_move': enforce_move,
        'remember_pattern': remember_pattern,
        'log_telemetry': log_move_telemetry,
        'get_zone_lock': _get_zone_lock,
        'set_zone_lock': _set_zone_lock,
        'full_allowed': _full_allowed,
    }

def start_background_mode(mode_logic, initial_message, mode_name):
    global auto_mode_active_task, edging_start_time
    handy.set_mode_context(mode_name)
    if auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
    user_signal_event.clear()
    mode_message_queue.clear()
    if mode_name == 'edging':
        edging_start_time = time.time()
    def on_stop():
        global auto_mode_active_task, edging_start_time
        auto_mode_active_task = None
        edging_start_time = None
        handy.set_mode_context(None)
    callbacks = get_policy_callbacks()
    callbacks['on_stop'] = on_stop
    services = {'llm': llm, 'handy': handy, 'scripts': scripts, 'chat_history': chat_history}
    auto_mode_active_task = AutoModeThread(mode_logic, initial_message, services, callbacks, mode_name=mode_name)
    auto_mode_active_task.start()

# --- ROUTES ---
@app.route('/')
def home_page():
    base_path = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base_path, 'index.html'), 'r', encoding='utf-8') as f:
        return render_template_string(f.read())

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@app.route('/user_content/<path:path>')
def send_user_content(path):
    return send_from_directory('user_content', path)

def _konami_code_action():
    def pattern_thread():
        handy.move(speed=100, depth=50, stroke_range=100, context=get_current_context(chat_history=chat_history))
        time.sleep(5)
        handy.stop()
    threading.Thread(target=pattern_thread).start()
    add_message_to_queue(f"Kept you waiting, huh?{SNAKE_ASCII}")

def _doom_guy_action():
    def pattern_thread():
        # God Mode Action
        context = get_current_context(chat_history=chat_history)
        handy.move(speed=100, depth=50, stroke_range=100, context=context)
        
        # Timed messages
        time.sleep(5)
        add_message_to_queue("*Usually, I use the Super Shotgun for close encounters... Yours seems effective.*")
        time.sleep(5)
        add_message_to_queue("*The only moaning I usually hear involves disembowelment. This is... different.*")
        
        # End of event
        time.sleep(5)
        handy.stop()

    add_message_to_queue(DOOM_SLAYER_ASCII, add_to_history=False)
    add_message_to_queue("Ever see a demon SUCK A FUCKEN' DICK, BRO?!")
    threading.Thread(target=pattern_thread).start()

def stop_all():
    """Stop any running mode and the device. Reset locks."""
    global auto_mode_active_task, edging_start_time, _allow_full_until
    try:
        if auto_mode_active_task:
            try:
                auto_mode_active_task.stop()
                auto_mode_active_task.join(timeout=5)
            except Exception:
                pass
            auto_mode_active_task = None
        handy.stop()
    except Exception:
        pass
    edging_start_time = None
    _zone_lock["zone"] = None
    _zone_lock["expires_at"] = 0.0
    _zone_lock["no_connectors"] = False
    _allow_full_until = 0.0

def _handle_chat_commands(text):
    if any(cmd in text for cmd in STOP_COMMANDS):
        stop_all()
        add_message_to_queue("Stopping.", add_to_history=False)
        return True, jsonify({"status": "stopped"})
    if "up up down down left right left right b a" in text:
        _konami_code_action()
        return True, jsonify({"status": "konami_code_activated"})
    if "iddqd" in text:
        _doom_guy_action()
        return True, jsonify({"status": "god_mode_activated"})
    if any(cmd in text for cmd in AUTO_ON_WORDS) and not auto_mode_active_task:
        start_background_mode(auto_mode_logic, "Okay, I'll take over.", mode_name='auto')
        return True, jsonify({"status": "auto_started"})
    if any(cmd in text for cmd in AUTO_OFF_WORDS) and auto_mode_active_task:
        stop_all()
        return True, jsonify({"status": "auto_stopped"})
    if any(c in text for c in EDGING_CUES):
        start_background_mode(edging_mode_logic, "Let's play an edging game...", mode_name='edging')
        return True, jsonify({"status": "edging_started"})
    if any(c in text for c in MILKING_CUES):
        start_background_mode(milking_mode_logic, "You're so close... I'm taking over completely now.", mode_name='milking')
        return True, jsonify({"status": "milking_started"})
    return False, None

@app.route('/send_message', methods=['POST'])
def handle_user_message():
    global special_persona_mode, special_persona_interactions_left, current_reply_length
    data = request.json
    user_input = data.get('message', '').strip()

    if (p := data.get('persona_desc')) and p != settings.persona_desc:
        settings.persona_desc = p
    if (k := data.get('key')) and k != settings.handy_key:
        handy.set_api_key(k); settings.handy_key = k
    if (rl := data.get('reply_length')) and rl != current_reply_length:
        current_reply_length = rl; settings.reply_length = rl

    # Apply intents regardless of mode state
    if user_input:
        status = parse_and_apply_intent(user_input)
        if status: add_message_to_queue(status, add_to_history=False)

    if not handy.handy_key:
        return jsonify({"status": "no_key_set"})
    if not user_input:
        # allow persona-only updates
        settings.save()
        return jsonify({"status": "empty_message"})

    chat_history.append({"role": "user", "content": user_input})

    # Consolidate memory immediately so response can use it
    if use_long_term_memory:
        try:
            chunk = list(chat_history)[-12:]
            new_profile = llm.consolidate_user_profile(chunk, settings.user_profile or {})
            settings.user_profile = new_profile
            # persist low-cost
            settings.save()
        except Exception as e:
            print("Profile consolidation error:", e)

    handled, response = _handle_chat_commands(user_input.lower())
    if handled: return response

    if auto_mode_active_task:
        mode_message_queue.append(user_input)
        return jsonify({"status": "message_relayed_to_active_mode"})

    llm_response = llm.get_chat_response(list(chat_history), get_current_context(chat_history=chat_history))

    if special_persona_mode is not None:
        special_persona_interactions_left -= 1
        if special_persona_interactions_left <= 0:
            special_persona_mode = None
            add_message_to_queue("(Personality core reverted to standard operation.)", add_to_history=False)

    if (chat_text := llm_response.get("chat")): add_message_to_queue(chat_text)
    if (new_mood := llm_response.get("new_mood")): pass

    # Optional one-off move
    action_tag = llm_response.get("action_tag")
    modifiers = llm_response.get("modifiers")
    if action_tag and isinstance(modifiers, dict):
        # Use dynamic values from LLM, with fallbacks
        sp = modifiers.get("speed", 45)
        dp = modifiers.get("depth", 50)
        rng = modifiers.get("range", 25) # Fallback to old value if missing

        sp, dp, rng = enforce_move(sp, dp, rng, tag=action_tag)
        handy.move(sp, dp, rng, context=get_current_context(chat_history=chat_history)) # <-- Pass context here
        log_move_telemetry(action_tag, dp, rng)

    return jsonify({"status": "ok"})

@app.route('/toggle_memory', methods=['POST'])
def toggle_memory_route():
    global use_long_term_memory
    use_long_term_memory = not use_long_term_memory
    return jsonify({"status": "ok", "memories_on": use_long_term_memory})

@app.route('/check_settings')
def check_settings_route():
    if settings.handy_key and settings.min_depth < settings.max_depth:
        return jsonify({
            "configured": True,
            "persona": settings.persona_desc,
            "handy_key": settings.handy_key,
            "ai_name": settings.ai_name,
            "elevenlabs_key": settings.elevenlabs_api_key,
            "pfp": settings.get_profile_picture_url(),
            "reply_length": settings.reply_length,
            "timings": {
                "auto_min": settings.auto_min_time, "auto_max": settings.auto_max_time,
                "milking_min": settings.milking_min_time, "milking_max": settings.milking_max_time,
                "edging_min": settings.edging_min_time, "edging_max": settings.edging_max_time
            }
        })
    return jsonify({"configured": False})

@app.route('/save_onboarding_settings', methods=['POST'])
def save_onboarding_settings_route():
    data = request.json or {}
    try:
        if 'handy_key' in data:
            settings.handy_key = data['handy_key']
            handy.set_api_key(data['handy_key'])
        if 'persona_desc' in data:
            settings.persona_desc = data['persona_desc']
        if 'min_speed' in data:
            settings.min_speed = float(data['min_speed'])
        if 'max_speed' in data:
            settings.max_speed = float(data['max_speed'])
        if 'min_depth' in data:
            settings.min_depth = float(data['min_depth'])
        if 'max_depth' in data:
            settings.max_depth = float(data['max_depth'])
        
        handy.update_settings(
            settings.min_speed, settings.max_speed,
            settings.min_depth, settings.max_depth
        )
        settings.save()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/set_reply_length', methods=['POST'])
def set_reply_length_route():
    global current_reply_length
    length = request.json.get('length')
    if length in ['short', 'medium', 'long']:
        current_reply_length = length; settings.reply_length = length
        print(f"Reply length set to: {length}")
        return jsonify({"status": "success", "length": length})
    return jsonify({"status": "error", "message": "Invalid length"}), 400

@app.route('/set_ai_name', methods=['POST'])
def set_ai_name_route():
    global special_persona_mode, special_persona_interactions_left
    name = request.json.get('name', 'BOT').strip() or 'BOT'
    if name.lower() == 'glados':
        special_persona_mode = "GLaDOS"; special_persona_interactions_left = 5
        settings.ai_name = "GLaDOS"; settings.save()
        return jsonify({"status": "special_persona_activated", "persona": "GLaDOS", "message": "Oh, it's *you*."})
    settings.ai_name = name; settings.save()
    return jsonify({"status": "success", "name": name})

@app.route('/signal_edge', methods=['POST'])
def signal_edge_route():
    if auto_mode_active_task and auto_mode_active_task.name == 'edging':
        user_signal_event.set(); return jsonify({"status": "signaled"})
    return jsonify({"status": "ignored", "message": "Edging mode not active."}), 400

@app.route('/set_profile_picture', methods=['POST'])
def set_pfp_route():
    data_url = request.json.get('pfp_b64')
    if not data_url: return jsonify({"status": "error", "message": "Missing image data"}), 400
    try:
        rel_path = settings.save_profile_picture_data_url(data_url)
        settings.save()
        return jsonify({"status": "success", "pfp_url": f"/user_content/{rel_path}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/set_handy_key', methods=['POST'])
def set_handy_key_route():
    key = request.json.get('key')
    if not key: return jsonify({"status": "error", "message": "Key is missing"}), 400
    handy.set_api_key(key); settings.handy_key = key; settings.save()
    return jsonify({"status": "success"})

@app.route('/like_last_move', methods=['POST'])
def like_last_move_route():
    global last_pattern_name
    if not last_pattern_name: return jsonify({"status": "no_active_pattern"})
    scripts.boost_pattern(last_pattern_name, 1.0)
    return jsonify({"status": "boosted", "name": last_pattern_name})

@app.route('/nudge', methods=['POST'])
def nudge_route():
    global calibration_pos_mm
    if calibration_pos_mm == 0.0 and (pos := handy.get_position_mm()):
        calibration_pos_mm = pos
    direction = request.json.get('direction')
    calibration_pos_mm = handy.nudge(direction, 0, 100, calibration_pos_mm)
    return jsonify({"status": "ok", "depth_percent": handy.mm_to_percent(calibration_pos_mm)})

@app.route('/setup_elevenlabs', methods=['POST'])
def elevenlabs_setup_route():
    api_key = request.json.get('api_key')
    if not api_key or not audio.set_api_key(api_key): return jsonify({"status": "error"}), 400
    settings.elevenlabs_api_key = api_key; settings.save()
    return jsonify(audio.fetch_available_voices())

@app.route('/set_elevenlabs_voice', methods=['POST'])
def set_elevenlabs_voice_route():
    voice_id, enabled = request.json.get('voice_id'), request.json.get('enabled', False)
    ok, message = audio.configure_voice(voice_id, enabled)
    if ok: settings.elevenlabs_voice_id = voice_id; settings.save()
    return jsonify({"status": "ok" if ok else "error", "message": message})

@app.route('/get_updates')
def get_ui_updates_route():
    messages = [messages_for_ui.popleft() for _ in range(len(messages_for_ui))]
    if audio_chunk := audio.get_next_audio_chunk():
        return send_file(io.BytesIO(audio_chunk), mimetype='audio/mpeg')
    return jsonify({"messages": messages})

@app.route('/set_timings', methods=['POST'])
def set_timings_route():
    data = request.json or {}
    try:
        settings.auto_min_time = float(data.get('auto_min', settings.auto_min_time))
        settings.auto_max_time = float(data.get('auto_max', settings.auto_max_time))
        settings.milking_min_time = float(data.get('milking_min', settings.milking_min_time))
        settings.milking_max_time = float(data.get('milking_max', settings.milking_max_time))
        settings.edging_min_time = float(data.get('edging_min', settings.edging_min_time))
        settings.edging_max_time = float(data.get('edging_max', settings.edging_max_time))
        settings.save()
        return jsonify({"status": "ok", "timings": {
            "auto_min": settings.auto_min_time, "auto_max": settings.auto_max_time,
            "milking_min": settings.milking_min_time, "milking_max": settings.milking_max_time,
            "edging_min": settings.edging_min_time, "edging_max": settings.edging_max_time
        }})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/get_status')
def get_status_route():
    active_mode = auto_mode_active_task.name if auto_mode_active_task else None
    last_dp = handy.last_depth_pos
    last_rng = getattr(handy, "last_stroke_range", 0)
    zl = _get_zone_lock()
    return jsonify({
        "mood": "Curious",
        "speed": handy.last_stroke_speed,
        "depth": last_dp,
        "active_mode": active_mode,
        "zone_lock": zl.get("zone"),
        "zone_lock_no_connectors": zl.get("no_connectors"),
        "full_allowed": _full_allowed(),
        "last_rng": last_rng,
    })

@app.route('/stop_everything', methods=['POST', 'GET'])
def stop_everything_route():
    stop_all()
    return jsonify({"status": "stopped_all"})

# Back-compat alias
@app.route('/stop_auto_mode', methods=['POST', 'GET'])
def stop_auto_route():
    return stop_everything_route()

# Convenience endpoints (aliases)
@app.route('/start_auto_mode', methods=['POST'])
def start_auto_mode_route():
    start_background_mode(auto_mode_logic, "Okay, I'll take over.", mode_name='auto')
    return jsonify({"status": "auto_started"})

@app.route('/start_edging_mode', methods=['POST'])
def start_edging_mode_route():
    start_background_mode(edging_mode_logic, "Edging started.", mode_name='edging')
    return jsonify({"status": "edging_started"})

@app.route('/start_milking_mode', methods=['POST'])
def start_milking_mode_route():
    start_background_mode(milking_mode_logic, "Milking started.", mode_name='milking')
    return jsonify({"status": "milking_started"})

# --- APP SHUTDOWN ---
def on_exit():
    print("Saving settings on exit...")
    # Try to consolidate one last time using the last 12 messages
    try:
        chunk = list(chat_history)[-12:]
        if use_long_term_memory:
            new_profile = llm.consolidate_user_profile(chunk, settings.user_profile or {})
            settings.user_profile = new_profile
    except Exception:
        pass
    settings.save()

if __name__ == '__main__':
    atexit.register(on_exit)
    print(f"Starting Handy AI app at {time.strftime('%Y-%m-%d %H:%M:%S')}...")
    app.run(host='0.0.0.0', port=5000, debug=False)