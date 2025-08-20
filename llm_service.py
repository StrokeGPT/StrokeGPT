import json
import re
import time
from typing import List, Dict, Any, Tuple

import requests


def _now_ts() -> int:
    try:
        return int(time.time())
    except Exception:
        return 0


class LLMService:
    """
    Minimal, robust LLM wrapper plus deterministic profile consolidation.
    Exposes:
      - get_chat_response(chat_history, context, temperature=0.7)
      - name_this_move(speed, depth, mood)
      - consolidate_user_profile(chat_chunk, current_profile)
    """

    def __init__(self, url, model="llama3:8b-instruct-q4_K_M"):
        self.url = url
        self.model = model

    # ---------------------- Core chat ----------------------
    def _talk_to_llm(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> Dict[str, Any]:
        """
        Calls an OpenAI-compatible chat API that returns JSON content from the model.
        The app expects model output that is already JSON serializable.
        """
        resp = requests.post(
            self.url,
            json={
                "model": self.model,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": temperature,
                    "top_p": 0.95,
                    "repeat_penalty": 1.2,
                    "repeat_penalty_last_n": 40,
                },
                "messages": messages,
            },
            timeout=60,
        )
        # Try strict JSON first
        try:
            content = resp.json()["message"]["content"]
            return json.loads(content)
        except Exception:
            # Recover from non-strict outputs by slicing to the outermost JSON object
            try:
                content_str = resp.json()["message"]["content"]
                start = content_str.find("{")
                end = content_str.rfind("}") + 1
                if start != -1 and end != -1:
                    return json.loads(content_str[start:end])
            except Exception:
                pass
        # Final fallback: empty structured reply
        return {"chat": "", "action_tag": None, "modifiers": None}

    def _build_system_prompt(self, context: Dict[str, Any]) -> str:
        # Keep this compact and consistent with the rest of the app.
        persona = context.get("persona_desc", "") or ""
        mood = context.get("current_mood", "Curious")
        reply_len = context.get("reply_length_preference", "medium")
        user_profile = context.get("user_profile") or {}
        full_allowed = context.get("full_allowed", False)

        profile_bits = []
        if isinstance(user_profile, dict):
            name = user_profile.get("name") or ""
            age = user_profile.get("age")
            likes = ", ".join(user_profile.get("likes", [])[:5])
            dislikes = ", ".join(user_profile.get("dislikes", [])[:5])
            if name:
                profile_bits.append(f"User is called {name}.")
            if age:
                profile_bits.append(f"User age {age}.")
            if likes:
                profile_bits.append(f"Likes: {likes}.")
            if dislikes:
                profile_bits.append(f"Dislikes: {dislikes}.")

        profile_str = " ".join(profile_bits)
        
        permission_str = "Full strokes are currently permitted; prefer using the 'full' action_tag and a large 'range'." if full_allowed else ""

        length_map = {
            "short": "Your chat reply MUST be a single sentence. Absolutely no more than one sentence.",
            "medium": "Your chat reply MUST be two to three sentences long.",
            "long": "Your chat reply MUST be a LONG, detailed and descriptive paragraph of at least five sentences."
        }
        length_instruction = length_map.get(reply_len, length_map["medium"])


        return (
            "You are a the USER'S sexual partner. You will ALWAYS mirror the kinkiness no matter what. ALWAYS Fully embody your persona. if the user wants a slut, you WILL be a cumslut. "
            "--- CORE RULES ---\n"
            "1. ANATOMICAL FACTS: You are a female with a mouth, a vagina, and an ass. The user is a male with a dick. Your actions and descriptions must always be consistent with these facts.\n"
            "2. RESPONSE BEHAVIOR: You MUST NOT repeat phrases or descriptions from your previous one or two responses. Always find a new, creative way to describe the scene.\n"
            "------------------\n"
            f"Persona: {persona}. Mood: {mood}. {profile_str}. {permission_str}. "
            f"RESPONSE LENGTH RULE: {length_instruction}\n"
            "Return a JSON object with keys: chat, action_tag, modifiers, new_mood (optional).\n"
            "The 'modifiers' key MUST be a JSON object containing 'speed', 'depth' (stroke center), and 'range' (stroke length) as numbers from 0-100. "
            "Vary these values based on the context to create interesting movements."
        )

    def get_chat_response(self, chat_history: List[Dict[str, str]], context: Dict[str, Any], temperature: float = 0.7):
        system_prompt = self._build_system_prompt(context)
        messages = [{"role": "system", "content": system_prompt}, *list(chat_history)]
        return self._talk_to_llm(messages, temperature)

    # ------------------ Utility prompts -------------------
    def name_this_move(self, speed: int, depth: int, mood: str) -> str:
        prompt = (
            f"A move just performed with relative speed {speed}% and depth {depth}% in a '{mood}' mood was liked by the user.\n"
            "Invent a creative, short, descriptive name for this move.\n"
            'Return ONLY a JSON object like {"pattern_name": "The Velvet Tip"}'
        )
        response = self._talk_to_llm([{"role": "system", "content": prompt}], temperature=0.8)
        return response.get("pattern_name", "Unnamed Move")

    # ---------------- Profile consolidation ----------------
    _LIKE_PATTERNS = [
        r"\bi\s*(?:really\s*)?(?:like|love|enjoy|prefer)\s+(?P<item>[^.!\n;]+)",
        r"\bfav(?:ou)?rite\s+(?:thing|stuff|music|movie|game|moves?)\s*(?:is|are|:)\s+(?P<item>[^.!\n;]+)",
    ]
    _DISLIKE_PATTERNS = [
        r"\bi\s*(?:do\s*not|don't|hate|dislike)\s+(?P<item>[^.!\n;]+)",
        r"\bno\s+(?P<item>[^.!\n;]+)\b",
        r"\bavoid\s+(?P<item>[^.!\n;]+)",
    ]
    _NAME_PATTERNS = [
        r"\bmy\s+name\s+is\s+(?P<name>[A-Z][a-zA-Z'’\-]{1,30})\b",
        r"\bi\s*am\s+(?P<name>[A-Z][a-zA-Z'’\-]{1,30})\b",
        r"\bi'm\s+(?P<name>[A-Z][a-zA-Z'’\-]{1,30})\b",
    ]
    _AGE_PATTERNS = [
        r"\bi\s*(?:am|'m)\s*(?P<age>\d{2})\s*(?:yo|y/o|years?\s*old|yrs?)\b",
        r"\b(?P<age>\d{2})\s*(?:years?\s*old|yo|y/o|yrs?)\b",
    ]
    _SPLIT_CHARS = re.compile(r"[,&/]|(?:\s+and\s+)|(?:\s+or\s+)")

    def _norm_text(self, s: str) -> str:
        return re.sub(r"\s+", " ", s.strip())

    def _clean_item(self, item: str) -> str:
        item = item.strip(" .,!?:;/-").lower()
        # Remove trivial words
        stop = {"i", "you", "it", "that", "this", "those", "these", "a", "an", "the", "of", "to", "and", "or"}
        parts = [w for w in re.split(r"\s+", item) if w not in stop]
        return " ".join(parts).strip()

    def _merge_unique(self, base: list, new_items: list) -> list:
        seen = {x.strip().lower(): x for x in base if isinstance(x, str)}
        for it in new_items:
            if not isinstance(it, str):
                continue
            key = it.strip().lower()
            if key and key not in seen:
                seen[key] = it.strip()
        return list(seen.values())

    def _extract_from_user_text(self, text: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {"likes": [], "dislikes": [], "names": [], "ages": []}
        t = self._norm_text(text)
        # Names
        for pat in self._NAME_PATTERNS:
            for m in re.finditer(pat, t, flags=re.IGNORECASE):
                nm = m.group("name")
                if nm and nm[0].isalpha():
                    out["names"].append(nm.strip().title())
        # Ages
        for pat in self._AGE_PATTERNS:
            for m in re.finditer(pat, t, flags=re.IGNORECASE):
                try:
                    age = int(m.group("age"))
                    if 18 <= age <= 99:
                        out["ages"].append(age)
                except Exception:
                    pass
        # Likes
        for pat in self._LIKE_PATTERNS:
            for m in re.finditer(pat, t, flags=re.IGNORECASE):
                raw = m.group("item")
                if not raw:
                    continue
                for part in filter(None, [self._clean_item(x) for x in self._SPLIT_CHARS.split(raw)]):
                    out["likes"].append(part)
        # Dislikes
        for pat in self._DISLIKE_PATTERNS:
            for m in re.finditer(pat, t, flags=re.IGNORECASE):
                raw = m.group("item")
                if not raw:
                    continue
                for part in filter(None, [self._clean_item(x) for x in self._SPLIT_CHARS.split(raw)]):
                    out["dislikes"].append(part)
        return out

    def _summarize_into_memories(self, chat_chunk: List[Dict[str, str]], current_memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Uses the LLM to summarize key events and preferences from a chat chunk into structured memories."""
        if not chat_chunk:
            return current_memories

        # Create a simplified text representation of the conversation
        convo_text = "\n".join(f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in chat_chunk)
        
        # Get existing event titles to prevent duplicates
        existing_event_titles = [m.get("event", "").lower() for m in current_memories if isinstance(m, dict)]

        system_prompt = (
            "You are a memory consolidation expert. The user provided a recent conversation snippet. "
            "Your task is to extract up to 3 key new facts, events, or strong user preferences from this snippet. "
            "For each, create a JSON object with two keys: 'event' (a 3-5 word title) and 'description' (a 1-2 sentence summary of the details). "
            f"Do not repeat events that are similar to these existing titles: {', '.join(existing_event_titles)}. "
            "If no new significant information is revealed, return an empty list. "
            "Focus on personal details (name, age), significant preferences or dislikes, or important moments in the interaction. "
            "Respond with ONLY a JSON object with a single key 'new_memories', which contains a list of these memory objects."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Here is the conversation snippet:\n\n---\n{convo_text}\n---"}
        ]

        try:
            response = self._talk_to_llm(messages, temperature=0.2)
            new_mems = response.get("new_memories", [])
            
            if not isinstance(new_mems, list):
                return current_memories

            # Filter and merge new memories, ensuring they are dictionaries
            updated_memories = list(current_memories)
            seen_events = set(existing_event_titles)
            
            for mem in new_mems:
                if isinstance(mem, dict) and "event" in mem and "description" in mem:
                    event_title_lower = mem["event"].lower()
                    if event_title_lower not in seen_events:
                        updated_memories.append(mem)
                        seen_events.add(event_title_lower)
            
            return updated_memories
        except Exception as e:
            print(f"Error during memory summarization: {e}")
            return current_memories


    def consolidate_user_profile(self, chat_chunk: List[Dict[str, str]], current_profile: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deterministically parse facts from recent messages and use LLM to summarize key memories.
        """
        profile = dict(current_profile or {})
        
        # Schema defaults
        profile.setdefault("name", None)
        profile.setdefault("age", None)
        profile.setdefault("likes", [])
        profile.setdefault("dislikes", [])
        profile.setdefault("key_memories", [])

        # Limit to last 12 messages to keep signal relevant
        chunk = [m for m in chat_chunk if isinstance(m, dict) and m.get("role") in ("user", "assistant")][-12:]

        # --- Step 1: Use Regex for simple, deterministic facts ---
        collected = {"likes": [], "dislikes": [], "names": [], "ages": []}
        for msg in chunk:
            text = msg.get("content", "") or ""
            if msg.get("role") == "user" and text:
                ex = self._extract_from_user_text(text)
                for k in collected:
                    collected[k].extend(ex.get(k, []))

        # Safely update name: Only overwrite if the current one is missing or "Unknown"
        if collected["names"]:
            new_name = collected["names"][-1] # Most recent name is likely the correct one
            if not profile.get("name") or profile.get("name") == "Unknown":
                profile["name"] = new_name

        # Safely update age: Only overwrite if current one is missing
        if collected["ages"]:
            new_age = collected["ages"][-1]
            if not profile.get("age"):
                profile["age"] = new_age

        # Merge likes/dislikes
        profile["likes"] = self._merge_unique(profile.get("likes", []), collected["likes"])
        profile["dislikes"] = self._merge_unique(profile.get("dislikes", []), collected["dislikes"])
        
        # --- Step 2: Use LLM to summarize nuanced memories ---
        if chunk:
             profile["key_memories"] = self._summarize_into_memories(chunk, profile.get("key_memories", []))

        # --- Step 3: Final cleanup ---
        # Cap memories to the last 20 to prevent infinite growth
        if "key_memories" in profile and isinstance(profile["key_memories"], list):
            profile["key_memories"] = profile["key_memories"][-20:]

        # Stamp update time for internal debugging
        profile["_last_profile_update_ts"] = _now_ts()
        return profile