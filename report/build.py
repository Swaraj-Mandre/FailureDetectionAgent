"""Turn the report HTML into the PDF we hand in.

The slides are built separately: python presentation/build_deck.py --pdf

    python report/build.py

Uses the headless mode of Microsoft Edge, which ships with Windows, so nothing
extra is installed. Edge gets a throwaway profile folder inside the repo for each file,
deleted right after. The diagrams are rendered by report/diagrams/render_diagrams.py.
"""
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

JOBS = [
    ("pdf", REPO_DIR / "report" / "report.html", REPO_DIR / "report" / "report.pdf", None),
]


def run_edge(kind, source, target, size, profile):
    args = [str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
            f"--user-data-dir={profile}"]
    if kind == "png":
        args += [f"--window-size={size}", "--hide-scrollbars", f"--screenshot={target}"]
    else:
        args += ["--no-pdf-header-footer", f"--print-to-pdf={target}"]
    target.unlink(missing_ok=True)
    subprocess.run(args + [source.as_uri()], check=True, capture_output=True, timeout=120)
    for _ in range(60):   # the launcher can exit before the file is written
        if target.exists() and target.stat().st_size > 0:
            break
        time.sleep(0.5)
    time.sleep(1)
    print(f"{target.relative_to(REPO_DIR)}  ({target.stat().st_size // 1024} KB)")


def remove_profile(profile):
    for _ in range(10):   # Edge can hold the profile open for a moment after it exits
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists():
            return
        time.sleep(1)


def main():
    for kind, source, target, size in JOBS:
        if not source.exists():
            print(f"skipped, not written yet: {source.relative_to(REPO_DIR)}")
            continue
        # a fresh profile per job, or Edge hands the job to the instance still closing
        profile = Path(tempfile.mkdtemp(prefix="edge_profile_", dir=REPO_DIR))
        try:
            run_edge(kind, source, target, size, profile)
        finally:
            remove_profile(profile)


if __name__ == "__main__":
    main()
