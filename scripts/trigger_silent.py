#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Silent wrapper for Windows Scheduled Tasks to eliminate console window popup (黑框一闪).
Executes trigger_podcast_sync.ps1 using CREATE_NO_WINDOW flag so that no console window is ever allocated.
"""
from pathlib import Path
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
PS_SCRIPT = SCRIPT_DIR / "trigger_podcast_sync.ps1"

# Parse workflow argument
workflow = "podcast_sync.yml"
args = sys.argv[1:]
if args:
    if args[0] in ("-Workflow", "-w", "--workflow") and len(args) > 1:
        workflow = args[1]
    else:
        workflow = args[0]

cmd = [
    "powershell.exe",
    "-WindowStyle",
    "Hidden",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    str(PS_SCRIPT),
    "-Workflow",
    workflow,
]

# 0x08000000 = CREATE_NO_WINDOW (Windows API)
res = subprocess.run(cmd, creationflags=0x08000000)
sys.exit(res.returncode)
