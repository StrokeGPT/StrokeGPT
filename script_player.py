import threading
import time
import math

class ScriptPlayer(threading.Thread):
    def __init__(self, handy_controller):
        super().__init__(daemon=True)
        self.handy = handy_controller
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._new_script_event = threading.Event()
        self._current = None
        self._loop = True

    def stop(self):
        self._stop_event.set()
        self._new_script_event.set()

    def set_script(self, script_dict):
        with self._lock:
            self._current = script_dict
            self._new_script_event.set()

    def run(self):
        while not self._stop_event.is_set():
            self._new_script_event.wait()
            if self._stop_event.is_set():
                break
            self._new_script_event.clear()

            with self._lock:
                script = self._current
            if not script:
                continue

            while not self._stop_event.is_set():
                start = time.perf_counter()
                acts = script['actions']
                for i in range(len(acts)):
                    if self._stop_event.is_set() or self._new_script_event.is_set():
                        break
                    a = acts[i]
                    t_ms = a['at']
                    target_pct = a['pos_pct']

                    mm = (self.handy.FULL_TRAVEL_MM * target_pct) / 100.0
                    
                    if i < len(acts)-1:
                        next_t = acts[i+1]['at']
                        dt = max(0.01, (next_t - t_ms)/1000.0)
                        next_mm = (self.handy.FULL_TRAVEL_MM * acts[i+1]['pos_pct']) / 100.0
                        dist = abs(next_mm - mm)
                        # Calculate the ideal velocity based on the script's timing
                        ideal_vel = max(5.0, dist / dt)
                        # Cap the velocity using the controller's current max speed setting
                        vel = min(ideal_vel, self.handy.current_max_velocity_mm_s)
                    else:
                        vel = 60.0

                    now = time.perf_counter()
                    target_time = start + (t_ms / 1000.0)
                    if target_time > now:
                        # Replace blocking sleep with a responsive wait
                        end_sleep = time.time() + (target_time - now)
                        while time.time() < end_sleep and not self._new_script_event.is_set() and not self._stop_event.is_set():
                            time.sleep(0.02)

                    if self._new_script_event.is_set() or self._stop_event.is_set():
                        break

                    try:
                        self.handy._send_command("hdsp/xava", {"position": mm, "velocity": vel, "stopOnTarget": False})
                    except Exception:
                        pass

                if self._new_script_event.is_set() or not self._loop:
                    break
                time.sleep(0.01)