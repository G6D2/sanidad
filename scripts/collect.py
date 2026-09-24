#!/usr/bin/env python3
"""Recolecta el estado de salud de CityPass+ Emergencias (Grupo 6) y escribe site/data.json.

Solo hace lecturas. Cada fuente falla por separado: si una no responde, su bloque
queda como {"error": ...} y el resto del snapshot se publica igual.

El historial (data/history.json) guarda únicamente mediciones reales hechas por
este workflow. Los días anteriores de la franja de 30 días salen de
data/historia.json (reconstrucción con evidencia real: deploys de Render,
keep-warm de GitHub Actions y bitácora del equipo).

Variables de entorno opcionales:
  RENDER_API_KEY   para contar logs de nivel error en Render (si falta, se omite).
  GH_READ_TOKEN    token con lectura de Actions en los repos de la app (si falta, se omiten pipelines).
"""
import datetime
import json
import os
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = "https://emergencias-backend-34go.onrender.com"
STATUS_PAGE = "https://citypass-emergencias-g6.betteruptime.com/index.json"
ART = datetime.timezone(datetime.timedelta(hours=-3))
ENDPOINTS = [
    ("api", "API de Emergencias (principal)", B + "/health", '"status":"ok"'),
    ("bff", "Conexión Frontend → API (BFF)", "https://frontend-krds.onrender.com/api/warmup", '"warmed":true'),
    ("web", "Aplicación web (Frontend)", "https://frontend-krds.onrender.com/acceso", None),
    ("backup", "API de Emergencias (backup / failover)", "https://emergencias-backend-npw6.onrender.com/health", '"status":"ok"'),
]
MONITORS = {"4975342": "api", "4975343": "bff", "4975344": "web", "4975345": "backup"}
RENDER_SERVICES = (("back", "srv-dad048v40ujc73ap79pg"), ("front", "srv-dad0akn10e5c73cp56og"))
RENDER_OWNER = "tea-dad017gn74is73cm4c8g"
HISTORY_DAYS = 7


def get(url, headers=None, data=None, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "citypass-sanidad/1.0", **(headers or {})}, data=data)
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body, code = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        body, code = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:  # red caída, timeout, DNS
        body, code = str(e), 0
    return code, body, round((time.time() - t) * 1000)


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


now = datetime.datetime.now(datetime.timezone.utc)
out = {"generatedAt": now.isoformat(timespec="seconds")}
reconstructed = load(os.path.join(ROOT, "data", "historia.json"), {})
history_path = os.path.join(ROOT, "data", "history.json")
history = load(history_path, {"points": []})

# 1. Chequeo de los 4 servicios (3 pedidos, mediana)
sample = {"at": out["generatedAt"]}
checks = {}
for key, name, url, keyword in ENDPOINTS:
    ms_list, ok, last_code = [], True, 0
    for _ in range(3):
        code, body, ms = get(url)
        ms_list.append(ms)
        last_code = code
        if not (200 <= code < 400) or (keyword and keyword not in body.replace(" ", "")):
            ok = False
    checks[key] = {"name": name, "url": url, "check": keyword or "HTTP 2xx", "httpCode": last_code,
                   "checkOk": ok, "latencyMs": statistics.median(ms_list), "latencyMaxMs": max(ms_list)}
    sample[key] = statistics.median(ms_list)
    sample[key + "Ok"] = ok

# 2. Better Stack (status page pública): estado y días monitoreados
bs = {}
code, body, _ = get(STATUS_PAGE)
try:
    sp = json.loads(body)
    out["aggregate"] = sp["data"]["attributes"]["aggregate_state"]
    for inc in sp.get("included", []):
        a = inc["attributes"]
        if inc["type"] == "status_page_resource" and str(a.get("resource_id")) in MONITORS:
            bs[MONITORS[str(a["resource_id"])]] = a
except Exception:
    out["aggregate"] = None

# 3. Historial real de este workflow (se poda a HISTORY_DAYS días)
cutoff = (now - datetime.timedelta(days=HISTORY_DAYS)).isoformat()
history["points"] = [p for p in history["points"] if p["at"] >= cutoff] + [sample]
os.makedirs(os.path.dirname(history_path), exist_ok=True)
with open(history_path, "w") as f:
    json.dump(history, f, separators=(",", ":"))


def day_status_from_history(key, day):
    pts = [p for p in history["points"]
           if datetime.datetime.fromisoformat(p["at"]).astimezone(ART).date().isoformat() == day and key + "Ok" in p]
    if not pts:
        return None
    oks = sum(1 for p in pts if p[key + "Ok"])
    status = "operational" if oks == len(pts) else ("downtime" if oks == 0 else "degraded")
    return {"s": status, "src": f"{oks} de {len(pts)} chequeos OK (este monitor)"}


today = now.astimezone(ART).date()
days = [(today - datetime.timedelta(days=29 - i)).isoformat() for i in range(30)]
services = []
for key, *_ in ENDPOINTS:
    c = checks[key]
    strip = []
    for d in days:
        entry = day_status_from_history(key, d)
        if entry is None and key in bs:
            h = next((x for x in bs[key].get("status_history", []) if x["day"] == d), None)
            if h and h["status"] != "not_monitored":
                s = "downtime" if h["downtime_duration"] > 0 else h["status"]
                entry = {"s": s, "src": "Better Stack (monitoreo cada 3 min)"}
        if entry is None and d in reconstructed.get(key, {}):
            entry = reconstructed[key][d]
        if entry is None:
            entry = {"s": "not_created" if d < "2026-09-03" else "no_data", "src": "sin evidencia"}
        strip.append({"d": d, **entry})
    mine = [p for p in history["points"] if key + "Ok" in p]
    c["monitor"] = {"history": strip,
                    "uptime7d": round(100 * sum(p[key + "Ok"] for p in mine) / len(mine), 2) if mine else None,
                    "checks7d": len(mine)}
    services.append({"key": key, **c})
out["services"] = services

# 4. Dominio (solo lecturas, con el dispatcher de demo)
try:
    code, body, _ = get(B + "/auth/dev/login", {"Content-Type": "application/json"},
                        json.dumps({"sub": "DISP-Demo001", "groups": ["dispatcher"]}).encode())
    j = json.loads(body)
    token = j.get("access_token") or j.get("token") or j.get("accessToken")
    H = {"Authorization": "Bearer " + token}

    def total(path):
        code, body, _ = get(B + path, H)
        j = json.loads(body)
        return j.get("pagination", {}).get("total", len(j.get("data", [])))

    dom = {"emergencies": {s: total(f"/api/emergencies?status={s}&limit=1") for s in ("PENDING", "VALIDATED", "DISPATCHED")}}
    dom["emergencies"]["total"] = total("/api/emergencies?limit=1")
    officers = json.loads(get(B + "/api/officers", H)[1])["data"]
    dom["officers"] = {"available": sum(o["status"] == "disponible" for o in officers),
                       "busy": sum(o["status"] != "disponible" for o in officers), "total": len(officers)}
    incidents = json.loads(get(B + "/api/incidents?limit=100", H)[1]).get("data", [])
    dom["incidents"] = {"total": len(incidents), "lastAt": max((i["createdAt"] for i in incidents), default=None),
                        "algorithm": incidents[0]["algorithmVersion"] if incidents else None}
    dom["outbox"] = {s: total(f"/api/outbox-events?status={s}&limit=1") for s in ("PENDING", "PROCESSING", "FAILED", "PUBLISHED")}
    out["domain"] = dom
except Exception as e:
    out["domain"] = {"error": f"no disponible ({type(e).__name__})"}

# 5. Errores (solo conteos: la página es pública)
errors = {}
key_render = os.environ.get("RENDER_API_KEY")
if key_render:
    try:
        logs = {}
        for key, rid in RENDER_SERVICES:
            q = urllib.parse.urlencode([("ownerId", RENDER_OWNER), ("resource", rid), ("level", "error"),
                                        ("startTime", (now - datetime.timedelta(days=7)).isoformat()),
                                        ("endTime", now.isoformat()), ("limit", "100"), ("direction", "backward")])
            for _ in range(3):
                code, body, _ = get("https://api.render.com/v1/logs?" + q,
                                    {"Authorization": "Bearer " + key_render, "Accept": "application/json"}, timeout=40)
                if code == 200:
                    break
                time.sleep(3)
            if code != 200:
                raise RuntimeError(f"HTTP {code}")
            j = json.loads(body)
            per_day = {}
            for item in j.get("logs", []):
                d = datetime.datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).astimezone(ART).date().isoformat()
                per_day[d] = per_day.get(d, 0) + 1
            logs[key] = {"total7d": len(j.get("logs", [])), "masDe100": j.get("hasMore", False), "porDia": per_day, "ultimos": []}
        errors["renderLogs"] = logs
    except Exception as e:
        errors["renderLogs"] = {"error": f"no disponible ({str(e)[:80]})"}
else:
    errors["renderLogs"] = {"error": "no configurado (falta RENDER_API_KEY)"}

today_pts = [p for p in history["points"] if datetime.datetime.fromisoformat(p["at"]).astimezone(ART).date() == today]
errors["chequeos"] = {k: {"fallidos": sum(1 for p in today_pts if not p.get(k + "Ok", True)), "total": len(today_pts)}
                      for k in ("api", "bff", "web", "backup")}
if isinstance(out.get("domain"), dict) and "outbox" in out["domain"]:
    errors["outbox"] = {"fallidos": out["domain"]["outbox"].get("FAILED", 0), "ultimos": []}
else:
    errors["outbox"] = {"error": "no disponible"}

pipelines, ci = [], []
if os.environ.get("GH_READ_TOKEN"):
    env = {**os.environ, "GH_TOKEN": os.environ["GH_READ_TOKEN"]}
    since = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    try:
        for repo, label in (("G6D2/emergencias-backend", "Backend"), ("G6D2/Frontend", "Frontend")):
            r = subprocess.run(["gh", "run", "list", "-R", repo, "--branch", "main", "--created", ">=" + since,
                                "--limit", "100", "--json", "displayTitle,conclusion,status,createdAt,url"],
                               capture_output=True, text=True, timeout=60, env=env)
            runs = [x for x in json.loads(r.stdout or "[]") if x["displayTitle"].startswith(("CI ", "CD "))]
            for wf, kind in (("CD", "Deploy"), ("CI", "Tests")):
                run = next((x for x in runs if x["displayTitle"].startswith(wf + " ")), None)
                if run:
                    pipelines.append({"repo": label, "kind": kind, "conclusion": run["conclusion"] or run["status"],
                                      "at": run["createdAt"], "url": run["url"]})
            red = [x for x in runs if x["conclusion"] == "failure"]
            ci.append({"repo": label, "total": len(runs), "rojos": len(red),
                       "ultimoRojo": {"at": red[0]["createdAt"], "title": red[0]["displayTitle"], "url": red[0]["url"]} if red else None})
        errors["ci"] = ci
    except Exception as e:
        errors["ci"] = {"error": f"no disponible ({type(e).__name__})"}
else:
    errors["ci"] = {"error": "no configurado (falta GH_READ_TOKEN)"}
out["pipelines"] = pipelines
out["errors"] = {"at": out["generatedAt"], **errors}
out["historyPoints"] = [{k: v for k, v in p.items() if not k.endswith("Ok")} for p in history["points"]]

os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
with open(os.path.join(ROOT, "site", "data.json"), "w") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
up = sum(1 for s in services if s["checkOk"])
print(f"{now.astimezone(ART):%H:%M} · {up}/4 arriba · puntos de historial: {len(history['points'])}")
