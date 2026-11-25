"""Reads uploaded files in a process of its own. Only used online (QM_PUBLIC=1).

A file can be made on purpose to need endless time or memory. Here it can use up only this process,
which is stopped after a minute and, on Linux, held to a memory limit. The web app keeps running.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import importer
from visitors import IMPORT_MB, IMPORT_SECONDS


def read(files, folder):
    """Builds folder/shop.db from the files and returns its description."""
    inbox = folder / "inbox"
    inbox.mkdir()
    try:
        for i, (_, data) in enumerate(files):
            (inbox / str(i)).write_bytes(data)
        (inbox / "names.json").write_text(json.dumps([name for name, _ in files]), encoding="utf-8")
        try:
            subprocess.run([sys.executable, "-X", "utf8", __file__, str(folder)], timeout=IMPORT_SECONDS,
                           env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "1"},   # otherwise reserves far more memory than it uses
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise importer.ImportProblem("This file takes too long to read. Please try a smaller one.")
        result = inbox / "result.json"
        if not result.exists():   # out of memory, or a file nobody expected
            raise importer.ImportProblem("This file could not be read. Please try a smaller one, or export it again as Excel or CSV.")
        reply = json.loads(result.read_text(encoding="utf-8"))
        if "problem" in reply:
            raise importer.ImportProblem(reply["problem"])
        return reply["about"]
    finally:
        shutil.rmtree(inbox, ignore_errors=True)


def main(folder):
    import shops

    inbox = folder / "inbox"
    names = json.loads((inbox / "names.json").read_text(encoding="utf-8"))
    files = [(name, (inbox / str(i)).read_bytes()) for i, name in enumerate(names)]
    try:
        reply = {"about": shops.describe(importer.import_files(files, folder / "shop.db"), folder / "shop.db")}
    except importer.ImportProblem as problem:
        reply = {"problem": str(problem)}
    (inbox / "result.json").write_text(json.dumps(reply, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    try:
        import resource   # Linux, where the online copy runs
        resource.setrlimit(resource.RLIMIT_AS, (IMPORT_MB * 1024 * 1024,) * 2)
    except ImportError:
        pass
    main(Path(sys.argv[1]))
