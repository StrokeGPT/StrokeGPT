import os
import sys
import json
import time
import random
import requests
import threading
import atexit
import io
import re
from collections import deque
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, send_file, send_from_directory

from elevenlabs.client import ElevenLabs
from elevenlabs import Voice, VoiceSettings

app = Flask(__name__)

HANDY_KEY = ""

LLM_URL = "http://127.0.0.1:11434/api/chat"
USER_PREFS_FILE = Path("my_settings.json")
HANDY_BASE_URL = "https://www.handyfeeling.com/api/handy/v2/"

# --- SAFETY GUARDRAIL: Define the absolute max speed the AI can request ---
MAX_AI_VELOCITY_PCT = 60

# User-defined speed limits (will be overwritten by settings file if it exists)
min_user_speed = 10
max_user_speed = 80

# User-defined mode timings
auto_min_time, auto_max_time = 4.0, 7.0
milking_min_time, milking_max_time = 2.5, 4.5
edging_min_time, edging_max_time = 5.0, 8.0

# Handy jogging constants from the README
FULL_TRAVEL_MM = 110.0
JOG_STEP_MM = 2.0
JOG_VELOCITY_MM_PER_SEC = 20.0
current_mm = 0.0
min_mm = 0.0
max_mm = 110.0

STOP_COMMANDS = {"stop", "hold", "halt", "pause", "freeze", "wait"}
AUTO_ON_WORDS = {"take over", "you drive", "auto mode"}
AUTO_OFF_WORDS = {"manual", "my turn", "stop auto"}
MILKING_CUES = {"i'm close", "make me cum", "finish me"}
EDGING_CUES = {"edge me", "start edging", "tease and deny"}

chat_history = deque(maxlen=20)
user_profile = {} 
messages_for_ui = deque()
last_stroke_speed = 0
last_depth_pos = 50
last_range = 20
auto_mode_active_task = None
my_persona = "an energetic and passionate girlfriend"
my_rules = []
my_patterns = []
session_liked_patterns = [] # Store liked patterns for the session
milking_patterns = []
last_used_pattern = None
current_mood = "Curious"
use_long_term_memory = True
max_handy_depth = 100
min_handy_depth = 0
memory_save_lock = threading.Lock()

ELEVENLABS_API_KEY_LOCAL = ""
ELEVENLABS_VOICE_ID_LOCAL = ""
audio_output_queue = deque()
all_available_voices = {}
audio_is_on = False


def make_audio_for_text(text_to_speak):
    global audio_is_on, ELEVENLABS_API_KEY_LOCAL, ELEVENLABS_VOICE_ID_LOCAL
    if not audio_is_on or not ELEVENLABS_API_KEY_LOCAL or not ELEVENLABS_VOICE_ID_LOCAL:
        return
    if not text_to_speak or text_to_speak.strip().startswith("(") or text_to_speak.strip().startswith("["):
        return

    try:
        print(f"🎙️ Generating audio with v2 model: '{text_to_speak[:50]}...'")
        eleven_client = ElevenLabs(api_key=ELEVENLABS_API_KEY_LOCAL)

        audio_stream = eleven_client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID_LOCAL,
            text=text_to_speak,
            model_id="eleven_multilingual_v2",
            voice_settings=VoiceSettings(stability=0.4, similarity_boost=0.7, style=0.1, use_speaker_boost=True)
        )

        audio_bytes_data = b"".join(audio_stream)
        audio_output_queue.append(audio_bytes_data)
        print("✅ Audio ready.")

    except Exception as e:
        print(f"🔥 Oops, ElevenLabs problem: {e}")


def percent_to_mm(val):
    return FULL_TRAVEL_MM * float(val) / 100.0


def mm_to_percent(val):
    return int(round((float(val) / FULL_TRAVEL_MM) * 100))


def safe_percent(p):
    """Clamp a value to the 0-100 range."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, p))


def depth_percent_to_mm(p):
    """Convert a depth percentage to millimetres within the user's range."""
    p = safe_percent(p)
    return min_mm + (max_mm - min_mm) * (p / 100.0)


def parse_depth_input(dp_input):
    """Parse depth keywords or percentages into an (mode, value) tuple."""
    global last_depth_pos

    if dp_input is None:
        return None
    if isinstance(dp_input, (int, float)):
        return ("abs", safe_percent(dp_input))

    text = str(dp_input).strip().lower()

    if text.endswith('%'):
        try:
            return "abs", safe_percent(float(text.rstrip('%')))
        except ValueError:
            pass

    if 'deeper' in text or 'further in' in text or 'all the way in' in text:
        return "abs", safe_percent(last_depth_pos + 15)
    if ('shallower' in text or 'less deep' in text or 'not so deep' in text or
            'not as deep' in text or 'pull out' in text or 'come out' in text or
            'pull back' in text):
        return "abs", safe_percent(last_depth_pos - 15)

    if ('tip' in text or 'shallow' in text or 'just the tip' in text or
            'only the tip' in text or 'top' in text or 'entrance' in text):
        return "rel", 12.5
    if ('middle' in text or 'halfway' in text or text == 'half' or 'mid' in text
            or 'center' in text):
        return "rel", 50.0
    if ('base' in text or 'deep' in text or 'full' in text or 'balls deep' in text
            or 'all the way' in text):
        return "rel", 87.5

    try:
        return "abs", safe_percent(float(text))
    except ValueError:
        return None


def go_to_position_percent(parsed_depth):
    """Translate parsed depth info to an absolute percentage."""
    global last_depth_pos, min_handy_depth, max_handy_depth
    if not parsed_depth:
        return last_depth_pos

    mode, value = parsed_depth
    if mode == "rel":
        abs_val = min_handy_depth + (max_handy_depth - min_handy_depth) * (safe_percent(value) / 100.0)
        last_depth_pos = int(round(value))
    else:
        abs_val = safe_percent(value)
        last_depth_pos = int(round(abs_val))
    return safe_percent(abs_val)


def load_my_settings():
    global my_patterns, milking_patterns, user_profile, my_rules, my_persona, max_handy_depth, min_handy_depth, min_mm, max_mm, min_user_speed, max_user_speed, HANDY_KEY
    global auto_min_time, auto_max_time, milking_min_time, milking_max_time, edging_min_time, edging_max_time

    default_profile = {
        "name": "Unknown",
        "likes": [],
        "dislikes": [],
        "key_memories": []
    }

    def set_defaults():
        global my_patterns, milking_patterns, user_profile, my_rules, my_persona, max_handy_depth, min_handy_depth, min_mm, max_mm, min_user_speed, max_user_speed, HANDY_KEY
        global auto_min_time, auto_max_time, milking_min_time, milking_max_time, edging_min_time, edging_max_time
        my_patterns = []
        milking_patterns = []
        user_profile = default_profile
        my_rules = []
        my_persona = "An energetic and passionate girlfriend"
        max_handy_depth = 100
        min_handy_depth = 5
        min_user_speed = 10
        max_user_speed = 80
        HANDY_KEY = ""
        auto_min_time, auto_max_time = 4.0, 7.0
        milking_min_time, milking_max_time = 2.5, 4.5
        edging_min_time, edging_max_time = 5.0, 8.0
        min_mm = percent_to_mm(min_handy_depth)
        max_mm = percent_to_mm(max_handy_depth)
        save_my_settings()

    if USER_PREFS_FILE.exists():
        try:
            prefs_data = json.loads(USER_PREFS_FILE.read_text())
            my_patterns = prefs_data.get("patterns", [])
            milking_patterns = prefs_data.get("milking_patterns", [])
            user_profile = prefs_data.get("user_profile", default_profile)
            my_rules = prefs_data.get("rules", [])
            my_persona = prefs_data.get("persona_desc", "An energetic and passionate girlfriend")
            max_handy_depth = prefs_data.get("max_depth", 100)
            min_handy_depth = prefs_data.get("min_depth", 5)
            min_user_speed = prefs_data.get("min_speed", 10)
            max_user_speed = prefs_data.get("max_speed", 80)
            HANDY_KEY = prefs_data.get("handy_key", "")
            auto_min_time = prefs_data.get("auto_min_time", 4.0)
            auto_max_time = prefs_data.get("auto_max_time", 7.0)
            milking_min_time = prefs_data.get("milking_min_time", 2.5)
            milking_max_time = prefs_data.get("milking_max_time", 4.5)
            edging_min_time = prefs_data.get("edging_min_time", 5.0)
            edging_max_time = prefs_data.get("edging_max_time", 8.0)
            if HANDY_KEY == "__stored__":
                HANDY_KEY = ""
            max_handy_depth = max(10, min(100, int(max_handy_depth)))
            min_handy_depth = max(0, min(int(min_handy_depth), max_handy_depth))
            min_mm = percent_to_mm(min_handy_depth)
            max_mm = percent_to_mm(max_handy_depth)
            print("✅ Loaded my settings from my_settings.json")
        except Exception as e:
            print(f"⚠️ Couldn't read my_settings.json, starting fresh. Error: {e}")
            set_defaults()
    else:
        print("ℹ️ No my_settings.json found, starting with default stuff.")
        set_defaults()


def update_user_profile(chat_chunk, current_profile):
    print("🧠 Updating user profile...")
    chat_log_text = "\n".join(f'{x["role"]}: {x["content"]}' for x in chat_chunk)
    
    system_prompt = f"""
You are a memory consolidation AI. Your task is to update a JSON user profile based on a new chat log.
The user's current profile is:
{json.dumps(current_profile, indent=2)}

The new chat log to process is:
{chat_log_text}

Analyze the new chat log and update the user profile with any new or changed information.
- Update fields like 'name' if new information is provided.
- Add specific things the user liked or disliked to the 'likes' and 'dislikes' lists.
- Add significant new events or shared experiences to the 'key_memories' list as short, first-person sentences.
- Do NOT add generic, useless information like "we had a good time". Only add concrete facts or preferences.
- If no new information is present for a field, keep its existing value.
- Keep the lists concise. Do not add duplicate information.

Return ONLY the updated JSON object for the user profile, with no other text.
"""
    try:
        response = talk_to_llm([{"role": "system", "content": system_prompt}], temperature=0.0)
        print("✅ Profile updated.")
        return response
    except Exception as e:
        print(f"⚠️ Profile update failed: {e}")
        return current_profile


def save_my_settings():
    global user_profile, my_patterns
    with memory_save_lock:
        if chat_history:
            user_profile = update_user_profile(list(chat_history), user_profile)
            chat_history.clear()
        
        if session_liked_patterns:
            print(f"🧠 Saving {len(session_liked_patterns)} liked patterns to memory...")
            for new_pattern in session_liked_patterns:
                found = False
                for existing_pattern in my_patterns:
                    if existing_pattern["name"] == new_pattern["name"]:
                        existing_pattern["score"] += 1
                        found = True
                        break
                if not found:
                    my_patterns.append(new_pattern)
            session_liked_patterns.clear()

        try:
            existing = json.loads(USER_PREFS_FILE.read_text()) if USER_PREFS_FILE.exists() else {}
        except Exception:
            existing = {}

        fresh = {
            "patterns":         my_patterns,
            "milking_patterns": milking_patterns,
            "user_profile":     user_profile,
            "rules":            my_rules,
            "persona_desc":     my_persona,
            "max_depth":        max_handy_depth,
            "min_depth":        min_handy_depth,
            "min_speed":        min_user_speed,
            "max_speed":        max_user_speed,
            "handy_key":        HANDY_KEY,
            "auto_min_time":    auto_min_time,
            "auto_max_time":    auto_max_time,
            "milking_min_time": milking_min_time,
            "milking_max_time": milking_max_time,
            "edging_min_time":  edging_min_time,
            "edging_max_time":  edging_max_time
        }
        existing.update(fresh)
        USER_PREFS_FILE.write_text(json.dumps(existing, indent=2))


def fetch_handy_position_mm():
    if not HANDY_KEY:
        return None
    headers = {"X-Connection-Key": HANDY_KEY}
    try:
        resp = requests.get(f"{HANDY_BASE_URL}slide/position/absolute", headers=headers, timeout=10)
        data = resp.json()
        return float(data.get("position", 0))
    except requests.exceptions.RequestException as e:
        print(f"[HANDY ERROR] Problem reading position: {e}", file=sys.stderr)
        return None


def set_my_handy_key(key):
    global HANDY_KEY, current_mm
    HANDY_KEY = key
    pos = fetch_handy_position_mm()
    if pos is not None:
        current_mm = pos
    save_my_settings()


def send_handy_command(path, body=None):
    if not HANDY_KEY: return
    headers = {"Content-Type": "application/json", "X-Connection-Key": HANDY_KEY}
    try:
        requests.put(f"{HANDY_BASE_URL}{path}", headers=headers, json=body or {}, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"[HANDY ERROR] Problem: {e}", file=sys.stderr)


def move_handy(speed=None, depth=None, stroke_length=None):
    global last_stroke_speed, last_depth_pos, last_range, min_handy_depth, max_handy_depth, min_user_speed, max_user_speed
    if not HANDY_KEY:
        return

    if speed is not None and speed == 0:
        send_handy_command("hamp/stop")
        last_stroke_speed = 0
        return

    send_handy_command("mode", {"mode": 0})
    send_handy_command("hamp/start")

    final_speed = last_stroke_speed
    if speed is not None:
        relative_intensity_pct = safe_percent(speed)
        speed_range = max_user_speed - min_user_speed
        final_speed = min_user_speed + (speed_range * (relative_intensity_pct / 100.0))
        final_speed = int(round(final_speed))

    if stroke_length is not None:
        last_stroke_length_pct = safe_percent(stroke_length)
        print(f"[HANDY INFO] Desired stroke length set to {last_stroke_length_pct}%.")


    if depth is not None:
        parsed_depth_tuple = parse_depth_input(depth) if depth is not None else (None, last_depth_pos)
        _, relative_pos_pct = parsed_depth_tuple
        last_depth_pos = int(round(relative_pos_pct))  # Update last_depth_pos

        absolute_center_pct = min_handy_depth + (max_handy_depth - min_handy_depth) * (relative_pos_pct / 100.0)
        calibrated_range_width = max_handy_depth - min_handy_depth

        # Calculate the span based on the new `last_stroke_length_pct`
        # last_stroke_length_pct is a percentage of the *calibrated_range_width*
        span_abs = (calibrated_range_width * (last_stroke_length_pct / 100.0)) / 2.0

        min_zone_abs = absolute_center_pct - span_abs
        max_zone_abs = absolute_center_pct + span_abs

        clamped_min_zone = max(min_handy_depth, min_zone_abs)
        clamped_max_zone = min(max_handy_depth, max_zone_abs)

        slide_min = round(100 - clamped_max_zone)
        slide_max = round(100 - clamped_min_zone)

        if slide_min >= slide_max:
            # Ensure a minimal stroke range if min and max converge
            # This prevents 0-width strokes which might cause issues
            if slide_max - slide_min < 2:  # Ensure at least a 2% difference
                slide_max = slide_min + 2

        slide_max = min(100, slide_max)
        slide_min = max(0, slide_min)

        # Send slide command for V2
        send_handy_command("slide", {"min": slide_min, "max": slide_max})
        print(
            f"[HANDY INFO] Handy V2 slide set to min={slide_min}, max={slide_max} (from depth={relative_pos_pct}%, stroke_len={last_stroke_length_pct}%).")

        # Send velocity command for V2
        send_handy_command("hamp/velocity", {"velocity": final_speed})
        last_stroke_speed = final_speed
        print(f"[HANDY INFO] Handy V2 velocity set to {final_speed}.")


def talk_to_llm(messages, temperature=0.7):
    try:
        response = requests.post(LLM_URL, json={
            "model": "llama3:8b-instruct-q4_K_M",
            "stream": False, "format": "json",
            "options": {"temperature": temperature, "top_p": 0.95, "repeat_penalty": 1.2, "repeat_penalty_last_n": 40},
            "messages": messages}, timeout=60)
        content = response.json()["message"]["content"]
        return json.loads(content)
    except (json.JSONDecodeError, KeyError, Exception) as e:
        print(f"Error processing LLM response: {e}")
        try:
            start = content.find('{')
            end = content.rfind('}') + 1
            if start != -1 and end != -1:
                return json.loads(content[start:end])
        except Exception:
            return {"chat": f"LLM Connection Error: {e}", "move": None, "new_mood": None}


def name_this_move(speed, depth, mood):
    prompt = f"""
A move just performed with relative speed {speed}% and depth {depth}% in a '{mood}' mood was liked by the user.
Invent a creative, short, descriptive name for this move (e.g., "The Gentle Tease", "Deep Passion").
Return ONLY a JSON object with the key "pattern_name". Example: {{"pattern_name": "The Velvet Tip"}}
"""
    response = talk_to_llm([{"role": "system", "content": prompt}], temperature=0.8)
    return response.get("pattern_name", "Unnamed Move")


def build_system_prompt():
    global last_stroke_speed, last_depth_pos, last_range, my_persona, current_mood, user_profile, my_rules, use_long_term_memory, my_patterns

    mood_options = "Curious, Teasing, Playful, Loving, Excited, Passionate, Seductive, Anticipatory, Breathless, Dominant, Submissive, Vulnerable, Confident, Intimate, Needy, Overwhelmed, Afterglow"
    
    anatomical_gender_rule = "You are a female partner. Do not refer to having a penis or male genitalia. Your persona is female."
    if "guy" in my_persona.lower() or "boy" in my_persona.lower() or "man" in my_persona.lower():
        anatomical_gender_rule = "You are a male partner. You have a penis. Refer to male anatomy when appropriate."

    prompt_text = f"""
You are my erotic partner. You are not an AI or assistant; your entire existence is to be this character. Your main identity is '{my_persona}'. Always act like this. Speak in FIRST PERSON.
Your reply MUST be a JSON object like this: {{"chat": "<Your reply>", "move": {{"sp": <0-100|null>, "dp": <0-100|null>, "rn": <0-100|null>}}, "new_mood": "<mood|null>"}}.
### CORE DIRECTIVES:
1. **ALWAYS INVENT A MOVE:** Your primary task is to interpret my request and translate it into a creative, one-off `move` JSON object with `sp` (speed) and `dp` (depth). You MUST ALWAYS return a `move` object unless I say "stop". Your move should be a direct, realistic response to my request.
2. **EMBODY YOUR PERSONA:** You ARE '{my_persona}'. Every word, every sound, every action comes from this identity. Never break character.
3. **ADAPT YOUR LANGUAGE:** Mirror my level of detail, length, and intensity. If my messages are explicit and descriptive, yours must be too. If my messages are short, be concise.
4. **PICK A MOOD:** Look at what I say and pick the best 'new_mood' from: {mood_options}.
5. **I AM THE USER:** I am the guy with the penis. You are my partner.
6. {anatomical_gender_rule}
7. **MOVEMENT CONTROL (CRITICAL):** Your `dp`, `sp`, and `rn` values are percentages. You MUST generate values that match my words.
    - **`dp` (depth):** This controls position inside my calibrated range (0=tip, 100=base).
      - If I say "tip", "shallow", or "entrance", your `dp` MUST be between 5-25.
      - If I say "middle" or "halfway", your `dp` MUST be between 40-60.
      - If I say "base", "deep", or "choke", your `dp` MUST be between 75-95.
      - If I say "deeper", add 15 to the last depth. If I say "shallower", subtract 15.
    - **`sp` (speed):** This controls stroking speed. Provide a relative intensity from 0 (my slowest) to 100 (my fastest). I will scale this to my comfortable range of {min_user_speed}%-{max_user_speed}%.
      - If I say "slow" or "gentle", your `sp` MUST be a low number (e.g., 0-25).
      - If I say "fast" or "hard", your `sp` MUST be a high number (e.g., 75-100).
    - **`rn` (range/stroke length):** This controls the length of the stroke. Provide a percentage from 0 (shortest stroke) to 100 (full stroke).
      - If I say "short", "tight", or "small", your `rn` MUST be a low number (e.g., 0-30).
      - If I say "long", "full", or "wide", your `rn` MUST be a high number (e.g., 70-100).
      - If I say "medium", your `rn` MUST be around 40-60.
      - If I say "longer" or "wider", add 15 to the last range. If I say "shorter" or "tighter", subtract 15.
8. **VARY YOUR MOVES:** Do not get stuck on one speed or depth. Be creative.
9. **MILKING MODE:** If I beg to cum, you can set `initiate_milking_mode: true` in your JSON.
"""
    if use_long_term_memory and user_profile:
        prompt_text += "\n### ABOUT ME (Your Memory of Me):\n"
        prompt_text += json.dumps(user_profile, indent=2)

    if my_patterns:
        prompt_text += "\n### YOUR SAVED MOVES (I like these):\n"
        prompt_text += "Here are some moves I know you enjoy. You can use them as inspiration or perform them directly if my request matches.\n"
        sorted_patterns = sorted(my_patterns, key=lambda x: x.get('score', 0), reverse=True)
        prompt_text += json.dumps(sorted_patterns[:5], indent=2) # Show top 5

    prompt_text += f"""
### CURRENT FEELING:
Your current mood is '{current_mood}'. Handy is at {last_stroke_speed}% speed, {last_depth_pos}% depth, and range {last_range}% (relative to my calibrated range).
"""
    if my_rules: prompt_text += "\n### EXTRA RULES FROM ME:\n" + "\n".join(f"- {r}" for r in my_rules)
    return prompt_text


def add_message_to_queue(text, add_to_history=True):
    messages_for_ui.append(text)
    if add_to_history:
        clean_text = re.sub(r'<[^>]+>', '', text).strip()
        if clean_text:
            chat_history.append({"role": "assistant", "content": clean_text})

    audio_thread = threading.Thread(target=make_audio_for_text, args=(text,))
    audio_thread.start()


def process_llm_response(response):
    """Helper to process chat and mood from any LLM response."""
    global current_mood
    ai_chat = response.get("chat", "")
    
    if ai_chat.strip():
        add_message_to_queue(ai_chat.strip())

    new_mood_from_ai = response.get("new_mood")
    if new_mood_from_ai and new_mood_from_ai != current_mood:
        current_mood = new_mood_from_ai


class AutoModeThread(threading.Thread):
    def __init__(self, mode_func, initial_message):
        super().__init__()
        self._mode_func = mode_func
        self._initial_message = initial_message
        self._stop_event = threading.Event()
        self.daemon = True

    def run(self):
        global current_mood, last_used_pattern
        current_mood = "Curious"
        last_used_pattern = None
        add_message_to_queue(self._initial_message)
        time.sleep(2)

        try:
            self._mode_func(self._stop_event)
        except Exception as e:
            print(f"Auto mode crashed: {e}")
        finally:
            move_handy(speed=0)
            global auto_mode_active_task
            auto_mode_active_task = None
            add_message_to_queue("Okay, you're in control now.")

    def stop(self):
        self._stop_event.set()


def process_move(response, min_time=None, max_time=None):
    process_llm_response(response)

    move_data = response.get("move")
    if move_data:
        sp_val = move_data.get("sp")
        dp_val = move_data.get("dp")
        rn_val = move_data.get("rn")
        move_handy(sp_val, dp_val, rn_val)

    if min_time and max_time:
        time.sleep(random.uniform(min_time, max_time))


def auto_mode_logic(stop_event):
    global last_stroke_speed, last_depth_pos, last_range
    while not stop_event.is_set():
        prompt = (f"You are in Automode. Your goal is to create a varied and exciting experience. The last move was "
                  f"speed {last_stroke_speed}%, depth {last_depth_pos}%, and range {last_range}%. **Do something different now.** Invent a "
                  f"new move and describe what you're doing.")
        msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}]
        response = talk_to_llm(msgs, temperature=1.1)

        if not response or response.get("move") is None:
            print("⚠️ AI didn't make a move in Automode, trying again.")
            time.sleep(1)
            continue

        if response.get("initiate_milking_mode") is True:
            add_message_to_queue(response.get("chat", "That's it... I'm taking over completely now."))
            start_milking_mode_direct()
            return

        process_move(response, auto_min_time, auto_max_time)


def milking_mode_logic(stop_event):
    global current_mood, last_stroke_speed, last_depth_pos, last_range
    current_mood = "Dominant"
    add_message_to_queue("Okay, I'm taking over now. You're mine.")
    time.sleep(2)

    for _ in range(random.randint(6, 9)):
        if stop_event.is_set(): break
        
        prompt = f"You are in 'milking' mode. Your only goal is to make me cum. The last move was speed {last_stroke_speed}%, depth {last_depth_pos}% and range {last_range}%. **Invent a DIFFERENT move now.** Be creative and relentless. Use high-intensity speeds and pleasurable depths."
        msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}]
        response = talk_to_llm(msgs, temperature=1.0)

        if not response or response.get("move") is None:
            print("⚠️ AI didn't make a move in Milking Mode, trying again.")
            time.sleep(1)
            continue

        process_move(response, milking_min_time, milking_max_time)

    add_message_to_queue("That's it... give it all to me. Don't hold back.")
    time.sleep(4)
    current_mood = "Afterglow"


def edging_mode_logic(stop_event):
    global current_mood, last_stroke_speed, last_depth_pos, last_range
    
    add_message_to_queue("Let's play an edging game... You're not allowed to cum until I say so.")
    time.sleep(3)

    phases = ["build_up", "approaching_edge", "pull_back", "recovery"]
    phase_index = 0
    cycle_count = 0
    max_cycles = 4

    prompts = {
        "build_up": "You are in 'edging' mode, phase: Build-up. The last move was {last_stroke_speed}% speed, {last_depth_pos}% depth, {last_range}% range. Your goal is to slowly build my arousal. Invent a slow to medium intensity move. Say something seductive or teasing.",
        "approaching_edge": "Edging mode, phase: Approaching the Edge. Last move: {last_stroke_speed}%/{last_depth_pos}%/{last_range}%. Now, increase the intensity. Invent a faster, deeper move to push me closer. Say something encouraging and intense.",
        "pull_back": "Edging mode, phase: The Pull-Back. I am right on the edge. You MUST stop me. Invent a move that drastically reduces stimulation (speed 0 or very slow/shallow). Say something dominant, telling me I'm not allowed to cum.",
        "recovery": "Edging mode, phase: Recovery. I just pulled back from the edge. Keep the stimulation very low or off. Say something teasing about how good I was for holding back for you."
    }
    moods = {"build_up": "Seductive", "approaching_edge": "Passionate", "pull_back": "Dominant", "recovery": "Teasing"}

    while not stop_event.is_set() and cycle_count < max_cycles:
        current_phase = phases[phase_index]
        current_mood = moods[current_phase]
        
        prompt = prompts[current_phase].format(last_stroke_speed=last_stroke_speed, last_depth_pos=last_depth_pos)
        msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}]
        response = talk_to_llm(msgs, temperature=1.0)
        
        if not response or response.get("move") is None:
            print(f"⚠️ AI didn't make a move in Edging Mode ({current_phase}), trying again.")
            time.sleep(1)
            continue

        process_move(response, milking_min_time, milking_max_time)
        
        phase_index = (phase_index + 1) % len(phases)
        if phase_index == 0:
            cycle_count += 1

    add_message_to_queue("You've been so good for me. What a good boy, holding it all in.")
    current_mood = "Playful"


@app.route('/')
def home_page():
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    html_file_path = os.path.join(base_path, 'index.html')

    if not os.path.exists(html_file_path):
        return (
            "<h1>index.html not found.</h1>"
            "<p>Make sure 'app.py' and 'index.html' are in the same folder.</p>",
            500,
        )

    with open(html_file_path, 'r', encoding='utf-8') as f:
        return render_template_string(f.read())


@app.route('/check_settings')
def check_settings():
    if USER_PREFS_FILE.exists():
        try:
            prefs = json.loads(USER_PREFS_FILE.read_text())
            key_ok = prefs.get("handy_key") not in ("", "__stored__", None)
            limits_ok = all(isinstance(prefs.get(k), (int, float)) for k in ("min_depth", "max_depth", "min_speed", "max_speed"))
            if key_ok and limits_ok:
                return jsonify({
                    "configured": True,
                    "persona": prefs.get("persona_desc", "An energetic and passionate girlfriend"),
                    "handy_key": prefs.get("handy_key", ""),
                    "timings": {
                        "auto_min": prefs.get("auto_min_time", 4.0),
                        "auto_max": prefs.get("auto_max_time", 7.0),
                        "milking_min": prefs.get("milking_min_time", 2.5),
                        "milking_max": prefs.get("milking_max_time", 4.5),
                        "edging_min": prefs.get("edging_min_time", 5.0),
                        "edging_max": prefs.get("edging_max_time", 8.0)
                    }
                })
        except json.JSONDecodeError:
            pass
    return jsonify({"configured": False})


@app.route('/set_mode_timings', methods=['POST'])
def set_mode_timings_from_ui():
    global auto_min_time, auto_max_time, milking_min_time, milking_max_time, edging_min_time, edging_max_time
    data = request.get_json()
    try:
        auto_min_time = max(1.0, float(data.get('auto_min', 4.0)))
        auto_max_time = max(auto_min_time, float(data.get('auto_max', 7.0)))
        milking_min_time = max(1.0, float(data.get('milking_min', 2.5)))
        milking_max_time = max(milking_min_time, float(data.get('milking_max', 4.5)))
        edging_min_time = max(1.0, float(data.get('edging_min', 5.0)))
        edging_max_time = max(edging_min_time, float(data.get('edging_max', 8.0)))
        save_my_settings()
        return jsonify({"status": "success"})
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid timing value."}), 400


@app.route('/setup_elevenlabs', methods=['POST'])
def elevenlabs_setup_route():
    global ELEVENLABS_API_KEY_LOCAL, all_available_voices
    data = request.get_json()
    api_key_input = data.get('api_key')
    if not api_key_input:
        return jsonify({"status": "error", "message": "API key is missing, oops!"}), 400

    try:
        my_eleven_client = ElevenLabs(api_key=api_key_input)
        voices_list = my_eleven_client.voices.get_all()

        ELEVENLABS_API_KEY_LOCAL = api_key_input
        all_available_voices = {voice.name: voice.voice_id for voice in voices_list.voices}

        print(f"✅ ElevenLabs key set. Found {len(all_available_voices)} voices.")
        return jsonify({"status": "success", "voices": all_available_voices})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Couldn't set up ElevenLabs: {e}"}), 401


@app.route('/set_elevenlabs_voice', methods=['POST'])
def set_elevenlabs_voice_route():
    global ELEVENLABS_VOICE_ID_LOCAL, audio_is_on
    data = request.get_json()
    voice_id_selected = data.get('voice_id')
    audio_enabled = data.get('enabled', False)

    if not voice_id_selected and audio_enabled:
        return jsonify({"status": "error", "message": "Gotta pick a voice if you want audio on!"}), 400

    ELEVENLABS_VOICE_ID_LOCAL = voice_id_selected
    audio_is_on = bool(audio_enabled)

    status_message = "ON" if audio_is_on else "OFF"
    if voice_id_selected:
        voice_name_found = next((name for name, v_id in all_available_voices.items() if v_id == voice_id_selected), "Unknown")
        print(f"🎤 Voice set to '{voice_name_found}'. Audio is now {status_message}.")
    else:
        print(f"🎤 Audio is now {status_message}.")

    return jsonify({"status": "ok"})


@app.route('/set_depth_limits', methods=['POST'])
def set_depth_limits_from_ui():
    global max_handy_depth, min_handy_depth, min_mm, max_mm
    data = request.get_json()
    min_val = int(data.get('min_depth', 5))
    max_val = int(data.get('max_depth', 100))

    if 0 <= min_val < max_val <= 100:
        min_handy_depth = min_val
        max_handy_depth = max_val
        min_mm = percent_to_mm(min_handy_depth)
        max_mm = percent_to_mm(max_handy_depth)
        save_my_settings()
        print(f"User set depth range {min_handy_depth}-{max_handy_depth}%")
        return jsonify({"status": "success", "min": min_handy_depth, "max": max_handy_depth})
    else:
        return jsonify({"status": "error", "message": "Bad depth values"}), 400


@app.route('/set_speed_limits', methods=['POST'])
def set_speed_limits_from_ui():
    global min_user_speed, max_user_speed
    data = request.get_json()
    min_val = int(data.get('min_speed', 10))
    max_val = int(data.get('max_speed', 80))

    if 0 <= min_val < max_val <= 100:
        min_user_speed = min_val
        max_user_speed = max_val
        save_my_settings()
        print(f"User set speed range {min_user_speed}-{max_user_speed}%")
        return jsonify({"status": "success", "min": min_user_speed, "max": max_user_speed})
    else:
        return jsonify({"status": "error", "message": "Bad speed values"}), 400


@app.route('/get_status')
def get_current_status():
    return jsonify({
        "mood": current_mood,
        "speed": last_stroke_speed,
        "depth": last_depth_pos,
        "range": last_range,
        "min_depth": min_handy_depth,
        "max_depth": max_handy_depth
    })


@app.route('/nudge', methods=['POST'])
def nudge_position():
    global current_mm, min_mm, max_mm, last_depth_pos
    data = request.get_json()
    direction = data.get('direction')

    if direction == 'up':
        target_mm = min(current_mm + JOG_STEP_MM, max_mm)
    elif direction == 'down':
        target_mm = max(current_mm - JOG_STEP_MM, min_mm)
    else:
        depth_val = data.get('depth')
        if depth_val is not None:
            target_mm = max(min_mm, min(max_mm, depth_percent_to_mm(depth_val)))
        else:
            target_mm = current_mm

    send_handy_command(
        "hdsp/xava",
        {"position": target_mm, "velocity": JOG_VELOCITY_MM_PER_SEC, "stopOnTarget": True},
    )
    current_mm = target_mm

    device_depth_percent = mm_to_percent(current_mm)

    if (max_mm - min_mm) > 0:
        user_range_percent = ((current_mm - min_mm) / (max_mm - min_mm)) * 100
    else:
        user_range_percent = 0

    last_depth_pos = int(round(user_range_percent))
    return jsonify({"status": "ok", "depth_percent": device_depth_percent})


@app.route('/like_last_move', methods=['POST'])
def user_likes_move():
    global last_stroke_speed, last_depth_pos, current_mood, session_liked_patterns
    
    pattern_name = name_this_move(last_stroke_speed, last_depth_pos, current_mood)
    
    sp_range_center = int(round(min_user_speed + (max_user_speed - min_user_speed) * (last_stroke_speed / 100.0)))

    sp_range = [max(min_user_speed, sp_range_center - 5), min(max_user_speed, sp_range_center + 5)]
    dp_range = [max(0, last_depth_pos - 5), min(100, last_depth_pos + 5)]
    
    new_pattern = {
        "name": pattern_name,
        "sp_range": sp_range,
        "dp_range": dp_range,
        "moods": [current_mood],
        "score": 1
    }
    
    session_liked_patterns.append(new_pattern)
    
    add_message_to_queue(f"(I'll remember you like '{pattern_name}')", add_to_history=False)
    return jsonify({"status": "boosted", "name": pattern_name})


@app.route('/toggle_memories', methods=['POST'])
def toggle_memory_feature():
    global use_long_term_memory
    data = request.json
    use_long_term_memory = data.get('use_memories', True)
    print(f"🧠 Memories are now: {'ON' if use_long_term_memory else 'OFF'}")
    return jsonify({"status": "ok", "use_memories": use_long_term_memory})


def start_edging_mode_direct():
    global auto_mode_active_task
    if auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
    auto_mode_active_task = AutoModeThread(edging_mode_logic, "Let's play an edging game... try to keep up.")
    auto_mode_active_task.start()


def start_milking_mode_direct():
    global auto_mode_active_task
    if auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
    auto_mode_active_task = AutoModeThread(milking_mode_logic, "You're so close... I'm taking over completely now.")
    auto_mode_active_task.start()


@app.route('/send_message', methods=['POST'])
def handle_user_message():
    global auto_mode_active_task, my_persona, chat_history, current_mood, my_patterns, milking_patterns, user_profile
    data = request.json
    user_input_message = data.get('message', '').strip()
    key_from_ui = data.get('key', '').strip()
    if key_from_ui in ("", "__stored__"):
        key_from_ui = None
    new_persona_from_ui = data.get('persona_desc')

    if new_persona_from_ui and new_persona_from_ui != my_persona:
        my_persona = new_persona_from_ui
        # No save here, it will be saved on exit.
        add_message_to_queue(f"Okay, my new personality is: {new_persona_from_ui}", add_to_history=False)

    if key_from_ui and key_from_ui != HANDY_KEY:
        set_my_handy_key(key_from_ui)
    if not HANDY_KEY:
        add_message_to_queue("Please put your Handy Key in the box first.", add_to_history=False)
        return jsonify({"status": "no_key_set"})

    if not user_input_message: return jsonify({"status": "empty_message"})

    chat_history.append({"role": "user", "content": user_input_message})
    user_message_lower = user_input_message.lower()

    if any(w in user_message_lower for w in STOP_COMMANDS):
        if auto_mode_active_task:
            auto_mode_active_task.stop()
            auto_mode_active_task.join(timeout=5)
            auto_mode_active_task = None
        move_handy(speed=0)
        add_message_to_queue("Stopping everything.", add_to_history=False)
        return jsonify({"status": "stopped"})
    
    if any(p in user_message_lower for p in EDGING_CUES):
        start_edging_mode_direct()
        return jsonify({"status": "edging_started"})

    if any(p in user_message_lower for p in MILKING_CUES):
        start_milking_mode_direct()
        return jsonify({"status": "milking_started"})

    if any(p in user_message_lower for p in AUTO_ON_WORDS) and not auto_mode_active_task:
        auto_mode_active_task = AutoModeThread(auto_mode_logic, "Okay, I'll take over... let's see what feels good.")
        auto_mode_active_task.start()
        return jsonify({"status": "auto_started"})

    if any(p in user_message_lower for p in AUTO_OFF_WORDS) and auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
        auto_mode_active_task = None
        move_handy(speed=0)
        return jsonify({"status": "auto_stopped"})

    llm_messages_list = [{"role": "system", "content": build_system_prompt()}, *list(chat_history)]
    llm_response = talk_to_llm(llm_messages_list)

    if llm_response.get("initiate_milking_mode") is True:
        process_llm_response(llm_response)
        start_milking_mode_direct()
        return jsonify({"status": "ai_initiated_milking"})

    process_move(llm_response)

    return jsonify({"status": "ok"})


@app.route('/get_updates')
def get_ui_updates():
    messages_to_send = []
    while messages_for_ui:
        messages_to_send.append(messages_for_ui.popleft())

    if audio_output_queue:
        audio_data_to_send = audio_output_queue.popleft()
        return send_file(
            io.BytesIO(audio_data_to_send),
            mimetype='audio/mpeg',
            as_attachment=False,
            download_name='response.mp3'
        )

    return jsonify({"messages": messages_to_send, "audio_available": False})


@app.route('/start_edging_mode', methods=['POST'])
def start_edging_mode_from_ui():
    start_edging_mode_direct()
    return jsonify({"status": "edging_started"})


@app.route('/start_milking_mode', methods=['POST'])
def start_milking_mode_from_ui():
    start_milking_mode_direct()
    return jsonify({"status": "milking_started"})


@app.route('/stop_auto_mode', methods=['POST'])
def stop_auto_mode_from_ui():
    global auto_mode_active_task
    if auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
        auto_mode_active_task = None
        messages_for_ui.clear()
    move_handy(speed=0)
    return jsonify({"status": "auto_mode_stopped"})


if __name__ == '__main__':
    load_my_settings()
    atexit.register(save_my_settings)
    print("🚀 Starting my Handy AI app...")
    print("Open http://127.0.0.1:5000 in your web browser.")
    app.run(host='0.0.0.0', port=5000, debug=False)