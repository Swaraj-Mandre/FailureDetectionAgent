"""Render the Mermaid diagrams in mermaid/ to PNG files in png/, each with a title and a colour key.

    python report/diagrams/render_diagrams.py

Uses headless Microsoft Edge and mermaid.js from a CDN, so it needs internet.
Temporary HTML pages and Edge profiles are written to _render/ and removed.
"""
import json, shutil, subprocess, tempfile, time
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "mermaid"
OUT = BASE / "png"
HERE = BASE / "_render"
HERE.mkdir(exist_ok=True)
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

KEY = {
    "ex": ("#DBEAFE", "#2563EB", "Executor (LLM)"),
    "mo": ("#FEF3C7", "#D97706", "Monitor (rules, no LLM)"),
    "rc": ("#D1FAE5", "#059669", "Recovery"),
    "pg": ("#EDE9FE", "#7C3AED", "A person"),
    "st": ("#F1F5F9", "#64748B", "Tool or storage"),
    "bad": ("#FFE4E6", "#E11D48", "Wrong or stopped"),
    "agent1": ("#DBEAFE", "#2563EB", "The LLM agent"),
    "check1": ("#FEF3C7", "#D97706", "The check (route_after_tools)"),
    "fix1": ("#D1FAE5", "#059669", "Failure handler"),
}
LINES = {"red": ("#E11D48", "failure path"), "green": ("#059669", "retry with advice")}

DIAGRAMS = {
    "01_pba1_agent": ("PBA 1: how one agent notices a failed tool call", ["agent1", "st", "check1", "fix1", "bad"], ["red"], (1100, 1050)),
    "02_pba2_team": ("PBA 2: who does what when a question comes in", ["ex", "st", "mo", "rc", "pg", "bad"], ["red", "green"], (1800, 640)),
    "03_memory_rag": ("Memory and RAG: what the team reads and writes", ["ex", "mo", "rc", "st"], [], (1400, 950)),
    "04_containment": ("Error propagation: the same bad result, Monitor off vs on", ["bad", "mo", "rc", "st"], [], (1000, 820)),
    "05_one_recovery": ("A real run: one question, two mistakes, both caught", ["ex", "mo", "rc", "st"], [], (1500, 1080)),
}

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<style>
 html,body{margin:0;background:#fff;font-family:'Segoe UI',sans-serif;color:#14213D;width:100%%;height:100%%;overflow:hidden}
 .wrap{display:flex;flex-direction:column;height:100%%;padding:22px 30px 18px;box-sizing:border-box}
 h1{font-family:Bahnschrift,'Segoe UI';font-weight:600;font-size:30px;margin:0 0 10px}
 #d{flex:1;min-height:0;display:flex;align-items:center;justify-content:center}
 #d svg{width:100%%!important;height:100%%!important;max-width:none!important}
 .key{display:flex;flex-wrap:wrap;gap:8px 26px;font-size:22px;justify-content:center;margin-top:10px;align-items:center}
 .key span{display:inline-flex;align-items:center;gap:7px}
 .sw{width:22px;height:16px;border-radius:4px;border:2px solid}
 .ln{width:30px;height:0;border-top:3px solid}
</style></head><body><div class="wrap" title="%s"><div id="d"></div><div class="key">%s</div></div>
<script>
mermaid.initialize({startOnLoad:false});
mermaid.render('g', %s).then(r => { document.getElementById('d').innerHTML = r.svg; document.title = 'done'; });
</script></body></html>"""

for name, (title, keys, lines, (w, h)) in DIAGRAMS.items():
    code = (SRC / f"{name}.mmd").read_text(encoding="utf-8")
    size = "20px" if name == "04_containment" else "26px"   # subgraph titles clip at bigger sizes
    code = code.replace('"fontSize": "16px"', f'"fontSize": "{size}"').replace('"fontSize": "18px"', f'"fontSize": "{size}"')
    code = code.replace('"messageFontSize": "17px"', '"messageFontSize": "26px"').replace('"actorFontSize": "18px"', '"actorFontSize": "24px"')
    code = code.replace('"theme": "base",', '"theme": "base", "flowchart": {"nodeSpacing": 25, "rankSpacing": 35}, "sequence": {"messageMargin": 22, "actorMargin": 30, "mirrorActors": false},', 1)
    key = "".join(f'<span><i class="sw" style="background:{KEY[k][0]};border-color:{KEY[k][1]}"></i>{KEY[k][2]}</span>' for k in keys)
    key += "".join(f'<span><i class="ln" style="border-color:{LINES[l][0]}"></i>{LINES[l][1]}</span>' for l in lines)
    page = HERE / f"{name}.html"
    page.write_text(PAGE % (title, key, json.dumps(code)), encoding="utf-8")
    profile = Path(tempfile.mkdtemp(prefix="edge_", dir=HERE))
    target = OUT / f"{name}.png"
    target.unlink(missing_ok=True)
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
                    f"--window-size={w},{h}", "--force-device-scale-factor=2", "--hide-scrollbars",
                    "--virtual-time-budget=10000", f"--screenshot={target}", page.as_uri()], capture_output=True, timeout=120)
    for _ in range(40):
        if target.exists() and target.stat().st_size > 0: break
        time.sleep(0.5)
    time.sleep(1)
    for _ in range(10):
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists(): break
        time.sleep(1)
    print(name, target.stat().st_size // 1024 if target.exists() else "MISSING", "KB")

shutil.rmtree(HERE, ignore_errors=True)
