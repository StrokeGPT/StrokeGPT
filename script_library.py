import json
import random
import time
from pathlib import Path

# Convention reminder: 0 = base/deep, 100 = tip.

RHYTHM_CLASSES = [
    ("wave", {"wave", "sine", "resonance"}),
    ("pecks", {"pecks", "peck", "micro"}),
    ("staccato", {"staccato", "punchy", "doublet", "triplet"}),
    ("triangle", {"triangle"}),
    ("circle", {"circle", "swirl", "loops"}),
    ("hover", {"hover", "hold", "linger"}),
    ("stairs", {"stair", "ladder", "riser", "fall"}),
    ("grind", {"grind", "roll", "hammer", "compression"}),
    ("bounce", {"bounce", "lilt", "swing", "alternator"}),
    ("random", {"random", "organic"}),
    ("pulse", {"pulse", "heartbeat", "flicker"}),
    ("sweep", {"sweep", "cascade"}),
    ("accel", {"accel", "decel", "dynamic"}),
    ("melody", {"melody"}),
]

def _infer_class(tags):
    tset = set((tags or []))
    lower = {x.lower() for x in tset}
    for cname, keys in RHYTHM_CLASSES:
        if lower.intersection(keys):
            return cname
    return "generic"

class ScriptLibrary:
    def __init__(self, possible_paths):
        self._patterns = []
        self._by_zone = {"tip": [], "mid": [], "base": [], "deep": [], "full": []}
        self._weights = {}      # name -> boost
        self._last_used = {}    # name -> timestamp
        self._load_first_existing(possible_paths)

    def _load_first_existing(self, paths):
        for p in paths:
            if p and Path(p).exists():
                try:
                    data = json.loads(Path(p).read_text(encoding="utf-8"))
                    self._ingest(data)
                    return
                except Exception:
                    continue
        # No file found => empty bank; fallback moves will be used.

    def _guess_zone(self, name, tags):
        n = (name or "").lower()
        t = [s.lower() for s in (tags or [])]
        if "tip" in n or "tip" in t:
            return "tip"
        if any(k in n for k in ("deep", "throat", "base")) or any(k in t for k in ("deep", "deepthroat", "base")):
            return "base"
        if "mid" in n or "mid" in t or "shaft" in t:
            return "mid"
        if "full" in n or "full" in t:
            return "full"
        # Heuristics
        if any(k in n for k in ("pounding", "cowgirl", "missionary", "doggystyle")):
            return "full"
        return "mid"

    def _ingest(self, data_dict):
        for key, pat in (data_dict or {}).items():
            name = pat.get("name") or key
            tags = pat.get("tags") or []
            actions = pat.get("actions") or []
            if not actions:
                continue
            zone = pat.get("zone") or self._guess_zone(name, tags)
            klass = _infer_class(tags)
            entry = {
                "name": name,
                "tags": tags,
                "zone": zone,
                "actions": sorted(
                    [{"at": float(a.get("at", 0)), "pos": float(a.get("pos", 50))} for a in actions],
                    key=lambda x: x["at"]
                ),
                "class": klass
            }
            self._patterns.append(entry)
            self._by_zone.setdefault(zone, []).append(entry)

    # ---------- Public API ----------
    def select(self, zone: str, avoid_names=None, avoid_classes=None, recent_seconds: float = 60.0,
               allow_full: bool = False, preferred_tags=None):
        zone = (zone or "mid").lower()
        avoid_names = set(avoid_names or [])
        avoid_classes = set(avoid_classes or [])
        preferred_tags = [t.lower() for t in (preferred_tags or [])]

        # Build candidate set
        if zone == "full" and not allow_full:
            # If full not permitted, degrade to 'mid'
            zone = "mid"

        bank = list(self._by_zone.get(zone, [])) or list(self._patterns)
        if not bank:
            return None

        now = time.time()
        scored = []
        for p in bank:
            if p["name"] in avoid_names:
                continue
            if p.get("class") in avoid_classes:
                continue

            w = 1.0 + self._weights.get(p["name"], 0.0)

            # novelty penalty if used recently
            last = self._last_used.get(p["name"], 0.0)
            if last and now - last < recent_seconds:
                # linear penalty down to 0.25x
                w *= max(0.25, (now - last) / recent_seconds)

            # small bonus for preferred tags
            tags_lower = {t.lower() for t in p.get("tags", [])}
            if preferred_tags and tags_lower.intersection(preferred_tags):
                w *= 1.35

            # avoid too generic when we have better
            if p.get("class") == "generic":
                w *= 0.85

            scored.append((w, p))

        if not scored:
            # if we filtered too hard, ignore class avoidance and pick again
            scored = [(1.0 + self._weights.get(p["name"], 0.0), p) for p in bank]

        weights, candidates = zip(*scored)
        pick = random.choices(candidates, weights=weights, k=1)[0]
        return pick

    def boost_pattern(self, name: str, amount: float = 1.0):
        if not name:
            return
        self._weights[name] = self._weights.get(name, 0.0) + float(amount)

    def mark_used(self, name: str):
        if name:
            self._last_used[name] = time.time()

    def _pattern_length_ms(self, pat):
        acts = pat["actions"]
        if not acts:
            return 0.0
        return max(0.0, acts[-1]["at"] - acts[0]["at"])

    def scale_to_user(self, pat, zone, lo, hi, target_seconds,
                      jitter_dp_frac=0.0, jitter_rng_frac=0.0,
                      rng_cap_frac_override=None, seed=None):
        """
        Map a pattern's rhythm into bounded strokes for the requested zone.
        We use shape only; absolute 'pos' in the file is not treated as physical depth.
        Returns steps: [{sp, dp, rng, sleep}, ...]
        """
        rng = random.Random(seed)

        # Fallback if needed
        if not pat or not pat.get("actions") or len(pat["actions"]) < 2:
            return self._fallback_steps(zone, lo, hi, target_seconds)

        actions = pat["actions"]
        # --- FIX: Invert the position values to match the application's coordinate system (0=base, 100=tip) ---
        vals = [(100 - a["pos"]) for a in actions]
        # -----------------------------------------------------------------------------------------------------
        vmin, vmax = min(vals), max(vals)
        span_src = max(1e-6, vmax - vmin)
        # normalized around 0: roughly in [-1,1]
        norm = [((v - (vmin + vmax) / 2.0) / (span_src / 2.0)) for v in vals]

        orig_ms = max(1.0, self._pattern_length_ms(pat))
        factor = (target_seconds * 1000.0) / orig_ms

        lo = float(lo); hi = float(hi)
        usable = max(5.0, hi - lo)
        
        # Re-introduce proportional zone caps to control stroke SIZE
        ZONE_CAPS = {"tip": 0.10, "mid": 0.22, "base": 0.16, "deep": 0.16, "full": 1.00}
        cap_frac = ZONE_CAPS.get((zone or "mid").lower(), 0.22)
        if rng_cap_frac_override is not None:
            cap_frac = max(0.05, float(cap_frac) * float(rng_cap_frac_override))
        cap = cap_frac * usable

        centers = {
            "tip": hi - usable * 0.08,
            "base": lo + usable * 0.08,
            "deep": lo + usable * 0.08,
            "mid": (lo + hi) / 2.0,
            "full": (lo + hi) / 2.0
        }
        center = centers.get((zone or "mid").lower(), (lo + hi) / 2.0)

        steps = []
        last_at = actions[0]["at"]
        for i, a in enumerate(actions):
            dp = center + norm[i] * (cap * 0.50)
            # jitter dp within small fraction of span
            if jitter_dp_frac > 0:
                dp += (rng.random() * 2 - 1) * (usable * jitter_dp_frac)
            dp = max(lo, min(hi, dp))

            # rng inside cap with some jitter
            delta = 0.0 if i == 0 else abs(norm[i] - norm[i - 1])
            rn = max(5.0, min(cap, (cap * 0.35) + delta * cap * 0.15))
            if jitter_rng_frac > 0:
                rn *= (1.0 + (rng.random() * 2 - 1) * jitter_rng_frac)
                rn = max(5.0, min(cap, rn))

            dt_ms = max(30.0, (a["at"] - last_at) * factor)
            last_at = a["at"]
            sp = 25.0 + min(75.0, 5000.0 / dt_ms)  # 25..100

            steps.append({
                "sp": int(round(sp)),
                "dp": int(round(dp)),
                "rng": int(round(rn)),
                "sleep": dt_ms / 1000.0
            })

        # Merge near-duplicates to reduce spam commands
        merged = []
        acc = None
        for st in steps:
            if acc is None:
                acc = dict(st)
            else:
                if st["sleep"] < 0.06 and abs(st["dp"] - acc["dp"]) < 2 and abs(st["rng"] - acc["rng"]) < 2:
                    acc["sleep"] += st["sleep"]
                else:
                    merged.append(acc); acc = dict(st)
        if acc:
            merged.append(acc)

        # mark last used
        self.mark_used(pat.get("name"))

        return merged

    def _fallback_steps(self, zone, lo, hi, target_seconds):
        lo = float(lo); hi = float(hi)
        usable = max(5.0, hi - lo)
        centers = {
            "tip": hi - usable * 0.08,
            "base": lo + usable * 0.08,
            "deep": lo + usable * 0.08,
            "mid": (lo + hi) / 2.0,
            "full": (lo + hi) / 2.0
        }
        
        cap = {"tip": 0.10, "mid": 0.22, "base": 0.16, "deep": 0.16, "full": 1.0}.get((zone or "mid").lower(), 0.22) * usable
        center = centers.get((zone or "mid").lower(), (lo + hi) / 2.0)
        rng = max(5.0, cap * 0.40)
        sp = 45
        steps = []
        per = max(0.05, float(target_seconds) / 10.0)
        for _ in range(10):
            steps.append({"sp": sp, "dp": int(round(center)), "rng": int(round(rng)), "sleep": per})
        return steps