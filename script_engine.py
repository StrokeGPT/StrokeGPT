import json
import random
from llm_service import LLMService

class Intent:
    def __init__(self, speed_pct=50, depth_center_pct=50, range_pct=50, tags=None):
        self.speed_pct = max(0.0, min(100.0, float(speed_pct if speed_pct is not None else 50)))
        self.depth_center_pct = max(0.0, min(100.0, float(depth_center_pct if depth_center_pct is not None else 50)))
        self.range_pct = max(0.0, min(100.0, float(range_pct if range_pct is not None else 50)))
        self.tags = set(tags or [])

class ScriptEngine:
    def __init__(self, llm: LLMService):
        self.llm = llm

    def _build_generation_prompt(self, intent: Intent, min_depth: float, max_depth: float) -> str:
        duration_s = random.uniform(4.0, 7.0)
        
        # Create a clear, detailed request for the LLM
        request_parts = [f"a {intent.speed_pct}% speed", f"{intent.range_pct}% range", f"centered around {intent.depth_center_pct}% depth"]
        if intent.tags:
            request_parts.append(f"with a feeling of '{', '.join(intent.tags)}'")

        request_str = ", ".join(request_parts)

        return (
            "You are an expert Funscript generator. Your task is to create a high-quality, realistic script based on a user's intent. "
            "Follow these rules precisely:\n"
            "1.  The user's calibrated range is from `min_depth`={min_depth} to `max_depth`={max_depth}. All `pos` values you generate MUST be within this range.\n"
            "2.  The script should last approximately {duration_s:.1f} seconds.\n"
            "3.  The user's request is for: **{request_str}**.\n"
            "4.  Interpret the request: a larger 'range' means the `pos` values should span a wider part of the calibrated range. A 'tip' tag means `pos` values should be high. A 'base' tag means they should be low. 'piston' means simple up/down, 'grind' means tight, small movements.\n"
            "5.  Generate a JSON object with a single key: 'actions'. 'actions' must be an array of `{{\"at\": <milliseconds>, \"pos\": <position>}}` objects.\n"
            "6.  Ensure `at` values are sorted and start at 0. Ensure `pos` values are smooth and realistic.\n"
            "Produce ONLY the raw JSON object and nothing else."
        ).format(
            min_depth=min_depth,
            max_depth=max_depth,
            duration_s=duration_s,
            request_str=request_str
        )

    def generate_script(self, intent: Intent, context: dict, min_depth: float, max_depth: float) -> dict | None:
        prompt = self._build_generation_prompt(intent, min_depth, max_depth)
        
        try:
            # We bypass the normal chat response to get raw JSON
            response_data = self.llm._talk_to_llm(
                messages=[{"role": "system", "content": prompt}],
                temperature=0.6
            )

            if "actions" in response_data and isinstance(response_data["actions"], list):
                # Basic validation of the generated script
                if not all("at" in a and "pos" in a for a in response_data["actions"]):
                    return None
                
                # Sort by time just in case the LLM didn't
                response_data["actions"].sort(key=lambda x: x["at"])

                return {
                    'name': 'generated', 
                    'actions': response_data["actions"],
                    'duration_ms': response_data["actions"][-1]["at"] if response_data["actions"] else 0
                }
        except Exception as e:
            print(f"Error generating script: {e}")
            return None
        
        return None