"""Run 13 deterministic actual-body C2 cases without hardware or build caches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
HW = Path("drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/hw/hdmi_tx_hw.c")
hardware = (args.source / HW).read_text()
signature = "static bool audio_cfg_clk_borrow("
begin = hardware.index(signature)
brace = hardware.index("{", begin)
end, depth = brace + 1, 1
while depth:
    depth += (hardware[end] == "{") - (hardware[end] == "}")
    end += 1
serialization_only = hardware[:begin] + hardware[end:]
anchor = 'pr_info(HW "[0x10e3] = 0x%x\\n", data32);'
start = serialization_only.index(anchor, serialization_only.index("static int hdmitx_set_audmode(")) + len(anchor)
stop = serialization_only.index("\thdmitx_wr_reg(HDMITX_DWC_AUD_N1", start)
baseline = subprocess.check_output([
    "git", "-C", str(args.source), "show",
    "5a4eaf46237b12a6c1c5c951262fa2d5cbcbdfbb:" + HW.as_posix()]).decode()
oldstart = baseline.index(anchor, baseline.index("static int hdmitx_set_audmode(")) + len(anchor)
oldstop = baseline.index("\thdmitx_wr_reg(HDMITX_DWC_AUD_N1", oldstart)
serialization_only = serialization_only[:start] + baseline[oldstart:oldstop] + serialization_only[stop:]
negative_source = args.output / "serialization-only-negative-source"
(negative_source / HW).parent.mkdir(parents=True, exist_ok=True)
(negative_source / HW).write_text(serialization_only)

source = ["--source", str(args.source)]
cases = [
    ("borrow C2", "check_clock_borrow.py", source + ["--tag", "c2", "--expect", "pass"]),
    ("borrow serialization-only negative", "check_clock_borrow.py",
     ["--source", str(negative_source), "--tag", "serialization", "--expect", "fail"]),
    ("borrow original negative", "check_clock_borrow.py",
     source + ["--original", "--tag", "original", "--expect", "fail"]),
    ("state C2 normal", "check_state_races.py", source),
    ("state C2 DRM", "check_state_races.py", source + ["--drm"]),
    ("state original normal negative", "check_state_races.py", source + ["--original"]),
    ("state original DRM negative", "check_state_races.py", source + ["--original", "--drm"]),
    ("IRQ C2", "check_audio_races.py", source),
    ("IRQ original negative", "check_audio_races.py", source + ["--original"]),
    ("IRQ original register partial control", "check_audio_races.py", source + ["--original-register-body"]),
    ("IRQ original mute partial control", "check_audio_races.py", source + ["--original-mute-body"]),
    ("SMP C2", "check_smp_rmw.py", source),
    ("SMP original negative", "check_smp_rmw.py", source + ["--original"]),
]
rows = []
for i, (name, file, flags) in enumerate(cases):
    env = os.environ.copy()
    env["KERNEL_TEST_OUTPUT"] = str(args.output / ("%02d" % i))
    result = subprocess.run([sys.executable, str(HERE / file), *flags],
                            capture_output=True, text=True, timeout=120, env=env)
    print(("PASS " if result.returncode == 0 else "FAIL ") + name)
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, file=sys.stderr)
    rows.append({"id": name, "met_expectation": result.returncode == 0,
                 "stdout": result.stdout, "stderr": result.stderr})
receipts = {str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in args.output.rglob("actual-body.c")}
result = {"cases": rows, "all_met_expectation": all(r["met_expectation"] for r in rows),
          "actual_program_sha256": receipts,
          "negative_control": "Serialization-only mutant preserves accepted mute/register synchronization but uses immutable pre-C2 FIFO reset window.",
          "scope": "Host ASan/UBSan production-body models, not hardware or HDMI-wire evidence."}
(args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
sys.exit(0 if result["all_met_expectation"] else 1)
