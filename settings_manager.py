import json
import os
import uuid
import base64
from pathlib import Path
from typing import Optional, Any, Dict

class SettingsManager:
    def __init__(self, settings_file_path: str = "my_settings.json"):
        self.settings_file = Path(settings_file_path)

        # Core
        self.handy_key: str = ""
        self.ai_name: str = "BOT"
        self.persona_desc: str = ""
        self.user_profile: Dict[str, Any] = {}

        # Device bounds (fallbacks)
        self.min_speed: float = 0
        self.max_speed: float = 100
        self.min_depth: float = 0
        self.max_depth: float = 100

        # Timings
        self.auto_min_time: float = 4.0
        self.auto_max_time: float = 7.0
        self.milking_min_time: float = 2.5
        self.milking_max_time: float = 4.5
        self.edging_min_time: float = 5.0
        self.edging_max_time: float = 8.0

        # LLM + Audio
        self.reply_length: str = "medium"
        self.elevenlabs_api_key: str = ""
        self.elevenlabs_voice_id: Optional[str] = None

        # Misc context
        self.patterns: Dict[str, Any] = {}
        self.rules: Dict[str, Any] = {}

        # Profile picture (path under user_content/, not base64)
        self.profile_picture_path: Optional[str] = None  # e.g., "pfp/abcd.png"

        # ensure user content directory exists
        self.user_content_root = Path("user_content")
        (self.user_content_root / "pfp").mkdir(parents=True, exist_ok=True)

    # ---------- LOAD / SAVE ----------
    def load(self):
        if not self.settings_file.exists():
            return

        try:
            data = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except Exception:
            return

        # Assign with defaults
        self.handy_key = data.get("handy_key", self.handy_key)
        self.ai_name = data.get("ai_name", self.ai_name)
        self.persona_desc = data.get("persona_desc", self.persona_desc)
        self.user_profile = data.get("user_profile", self.user_profile)

        self.min_speed = data.get("min_speed", self.min_speed)
        self.max_speed = data.get("max_speed", self.max_speed)
        self.min_depth = data.get("min_depth", self.min_depth)
        self.max_depth = data.get("max_depth", self.max_depth)

        timings = data.get("timings", {})
        self.auto_min_time = data.get("auto_min_time", timings.get("auto_min", self.auto_min_time))
        self.auto_max_time = data.get("auto_max_time", timings.get("auto_max", self.auto_max_time))
        self.milking_min_time = data.get("milking_min_time", timings.get("milking_min", self.milking_min_time))
        self.milking_max_time = data.get("milking_max_time", timings.get("milking_max", self.milking_max_time))
        self.edging_min_time = data.get("edging_min_time", timings.get("edging_min", self.edging_min_time))
        self.edging_max_time = data.get("edging_max_time", timings.get("edging_max", self.edging_max_time))

        self.reply_length = data.get("reply_length", self.reply_length)
        self.elevenlabs_api_key = data.get("elevenlabs_api_key", self.elevenlabs_api_key)
        self.elevenlabs_voice_id = data.get("elevenlabs_voice_id", self.elevenlabs_voice_id)

        # Preferred key going forward
        self.profile_picture_path = data.get("profile_picture_path", self.profile_picture_path)

        # --- One-time migration from old base64 field ---
        legacy_b64 = data.get("profile_picture_b64")
        if legacy_b64 and not self.profile_picture_path:
            try:
                rel = self.save_profile_picture_data_url(legacy_b64)
                self.profile_picture_path = rel
                # Remove legacy from file on next save
                self.save()
            except Exception:
                # If migration fails, just ignore and keep default avatar
                pass

    def save(self, *_args, **_kwargs):
        payload = {
            "handy_key": self.handy_key,
            "ai_name": self.ai_name,
            "persona_desc": self.persona_desc,
            "user_profile": self.user_profile,
            "min_speed": self.min_speed,
            "max_speed": self.max_speed,
            "min_depth": self.min_depth,
            "max_depth": self.max_depth,
            "auto_min_time": self.auto_min_time,
            "auto_max_time": self.auto_max_time,
            "milking_min_time": self.milking_min_time,
            "milking_max_time": self.milking_max_time,
            "edging_min_time": self.edging_min_time,
            "edging_max_time": self.edging_max_time,
            "reply_length": self.reply_length,
            "elevenlabs_api_key": self.elevenlabs_api_key,
            "elevenlabs_voice_id": self.elevenlabs_voice_id,
            "patterns": self.patterns,
            "rules": self.rules,
            # store only a short relative path, never base64
            "profile_picture_path": self.profile_picture_path,
        }
        self.settings_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- PROFILE PICTURE HELPERS ----------
    def save_profile_picture_data_url(self, data_url: str) -> str:
        """
        Accepts a data URL like 'data:image/png;base64,...'
        Saves it under user_content/pfp/<uuid>.<ext>
        Returns relative path inside user_content folder (e.g., 'pfp/uuid.png').
        """
        if not data_url.startswith("data:"):
            raise ValueError("Invalid data URL")

        try:
            header, b64 = data_url.split(",", 1)
        except ValueError:
            raise ValueError("Malformed data URL")

        mime = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
        ext = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/webp": "webp",
            "image/gif": "gif",
        }.get(mime, "png")

        blob = base64.b64decode(b64)
        fname = f"{uuid.uuid4().hex}.{ext}"
        rel_path = Path("pfp") / fname
        abs_path = self.user_content_root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(blob)

        self.profile_picture_path = str(rel_path).replace("\\", "/")
        return self.profile_picture_path

    def get_profile_picture_url(self) -> str:
        """Return a URL the frontend can use. Falls back to default avatar."""
        if self.profile_picture_path:
            path = (self.user_content_root / self.profile_picture_path)
            if path.exists():
                return f"/user_content/{self.profile_picture_path}"
        return "/static/default-pfp.png"
