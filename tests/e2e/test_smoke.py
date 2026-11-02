import os
import sys

print("Smoke test: starting...", flush=True)

this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(this_dir))

from harness import E2ETestContext, main_app

print(f"Total routes in main_app: {len(main_app.app.routes)}", flush=True)
for r in main_app.app.routes[:5]:
    print("Route:", getattr(r, "__route_method__", None), getattr(r, "__route_path__", None), flush=True)

with E2ETestContext() as ctx:
    print("Smoke test: context entered!", flush=True)
    r_func, p_params = ctx.client._match_route("/api/series/rows", "GET")
    print("Matched route:", r_func, p_params, flush=True)
    resp = ctx.client.get("/api/series/rows")
    print("Response:", resp.status_code, resp.text, flush=True)

print("Smoke test: SUCCESS!", flush=True)
