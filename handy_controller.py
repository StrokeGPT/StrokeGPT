import sys
import requests
import time
from script_engine import ScriptEngine, Intent
from script_player import ScriptPlayer
from llm_service import LLMService

class HandyController:
    def __init__(self, handy_key="", llm_service: LLMService = None, base_url="https://www.handyfeeling.com/api/handy/v2/"):
        self.handy_key = handy_key
        self.base_url = base_url
        self.last_stroke_speed = 0
        self.last_depth_pos = 50
        self.last_relative_speed = 50
        self.min_user_speed = 10
        self.max_user_speed = 80
        self.max_handy_depth = 100
        self.min_handy_depth = 0
        self.FULL_TRAVEL_MM = 110.0
        self.current_max_velocity_mm_s = 400.0
        
        self.script_engine = ScriptEngine(llm=llm_service) if llm_service else None
        
        self.script_player = ScriptPlayer(self)
        self.script_player.start()
        self._mode_context = None

    def _speed_pct_to_max_vel_mm_s(self, pct):
        MAX_PHYSICAL_VELOCITY = 400.0
        return max(5.0, (pct / 100.0) * MAX_PHYSICAL_VELOCITY)

    def set_api_key(self, key):
        self.handy_key = key

    def update_settings(self, min_speed, max_speed, min_depth, max_depth):
        self.min_user_speed = min_speed
        self.max_user_speed = max_speed
        self.min_handy_depth = min_depth
        self.max_handy_depth = max_depth

    def _send_command(self, path, body=None):
        if not self.handy_key:
            return
        headers = {"Content-Type": "application/json", "X-Connection-Key": self.handy_key}
        try:
            requests.put(f"{self.base_url}{path}", headers=headers, json=body or {}, timeout=10)
        except requests.exceptions.RequestException as e:
            print(f"[HANDY ERROR] Problem: {e}", file=sys.stderr)

    def _safe_percent(self, p):
        try:
            p = float(p)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(100.0, p))

    def move(self, speed, depth, stroke_range, context):
        """Generative AI move function. Takes an intent and creates a script from scratch."""
        if not self.handy_key or not self.script_engine:
            return

        if speed is not None and speed == 0:
            self.stop()
            return

        relative_speed_pct = self._safe_percent(speed if speed is not None else 50)
        speed_range_width = self.max_user_speed - self.min_user_speed
        final_absolute_speed_pct = self.min_user_speed + (speed_range_width * (relative_speed_pct / 100.0))
        self.current_max_velocity_mm_s = self._speed_pct_to_max_vel_mm_s(final_absolute_speed_pct)

        tags = set()
        if (zone := context.get("zone_lock")):
            tags.add(zone)
        
        intent = Intent(
            speed_pct=relative_speed_pct,
            depth_center_pct=float(depth),
            range_pct=float(stroke_range),
            tags=tags
        )

        generated_script = self.script_engine.generate_script(
            intent, context, float(self.min_handy_depth), float(self.max_handy_depth)
        )

        if generated_script:
            for action in generated_script["actions"]:
                action["pos_pct"] = action.pop("pos")
            self.script_player.set_script(generated_script)
            self.last_relative_speed = intent.speed_pct
            self.last_depth_pos = intent.depth_center_pct

    def play_pattern(self, steps: list):
        """Plays a pre-made pattern from the script library."""
        if not self.handy_key or not steps:
            return
        
        # Convert steps from scale_to_user into a script for the player
        script_actions = []
        current_time = 0
        for step in steps:
            # The player needs absolute time, so we accumulate the sleep durations
            script_actions.append({"at": current_time, "pos_pct": step["dp"]})
            current_time += int(step["sleep"] * 1000)

        if not script_actions:
            return
            
        final_script = {
            'name': 'pattern_playback',
            'actions': script_actions,
            'duration_ms': current_time
        }
        self.script_player.set_script(final_script)
        
        # We don't have fine-grained speed control here, so we set an average
        self.last_relative_speed = 50

    def stop(self):
        self.script_player.set_script(None)
        try:
            self._send_command("hamp/stop")
        except Exception:
            pass
        self.last_stroke_speed = 0
        self.last_relative_speed = 0

    def nudge(self, direction, min_depth_pct, max_depth_pct, current_pos_mm):
        JOG_STEP_MM = 2.0
        JOG_VELOCITY_MM_PER_SEC = 20.0
        min_mm = self.FULL_TRAVEL_MM * float(min_depth_pct) / 100.0
        max_mm = self.FULL_TRAVEL_MM * float(max_depth_pct) / 100.0
        
        target_mm = current_pos_mm
        if direction == 'up':
            target_mm = min(current_pos_mm + JOG_STEP_MM, max_mm)
        elif direction == 'down':
            target_mm = max(current_pos_mm - JOG_STEP_MM, min_mm)
        
        self._send_command(
            "hdsp/xava",
            {"position": target_mm, "velocity": JOG_VELOCITY_MM_PER_SEC, "stopOnTarget": True},
        )
        return target_mm

    def get_position_mm(self):
        if not self.handy_key:
            return None
        headers = {"X-Connection-Key": self.handy_key}
        try:
            resp = requests.get(f"{self.base_url}slide/position/absolute", headers=headers, timeout=10)
            data = resp.json()
            return float(data.get("position", 0))
        except requests.exceptions.RequestException as e:
            print(f"[HANDY ERROR] Problem reading position: {e}", file=sys.stderr)
            return None

    def mm_to_percent(self, val):
        return int(round((float(val) / self.FULL_TRAVEL_MM) * 100))

    def set_mode_context(self, mode_name=None):
        self._mode_context = mode_name