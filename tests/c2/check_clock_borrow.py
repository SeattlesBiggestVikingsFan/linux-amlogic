"""C2 sampler-clock borrow regression on the actual hdmi_tx_hw.c bodies (host model).

Extracts amute_flag..audio_mute_op (incl. lock/enum/borrow counter), audio_cfg_clk_borrow
when present, and the actual FIFO-reset window of hdmitx_set_audmode(), then checks the
sampler clocks (HDMITX_TOP_CLK_CNTL bits 2-3) are running across set_aud_fifo_rst()/udelay
while muted, are re-gated afterwards only if still muted, and that ACR/sample packet bits
are never touched by the borrow. Base/S sources are negative controls (expected to FAIL).
Scope: deterministic host model of the actual source bodies; not hardware causality.
"""
import argparse
import os
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(os.environ["KERNEL_TEST_OUTPUT"]).resolve()
HERE.mkdir(parents=True, exist_ok=True)
HW = "drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/hw/hdmi_tx_hw.c"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", required=True)
parser.add_argument("--original", action="store_true", help="use immutable accepted pre-C2 source as negative control")
parser.add_argument("--tag", required=True)
parser.add_argument("--expect", choices=("pass", "fail"), required=True)
args = parser.parse_args()


def wsl(*command):
    return subprocess.check_output([*command]).decode()


hardware = (wsl("git", "-C", args.source, "show", '5a4eaf46237b12a6c1c5c951262fa2d5cbcbdfbb:' + HW) if args.original
            else wsl("cat", args.source + "/" + HW))


def function(signature, start=0):
    begin = hardware.index(signature, start)
    brace = hardware.index("{", begin)
    end, depth = brace + 1, 1
    while depth:
        depth += (hardware[end] == "{") - (hardware[end] == "}")
        end += 1
    return begin, end


head = hardware.index("static int amute_flag = -1;")
_, mute_end = function("static void audio_mute_op(", head)
state = hardware[head:mute_end]
updated = "enum audio_mute_action action" in state
has_borrow = "static bool audio_cfg_clk_borrow(" in hardware
borrow = ""
if has_borrow:
    b, e = function("static bool audio_cfg_clk_borrow(")
    borrow = hardware[b:e]
anchor = 'pr_info(HW "[0x10e3] = 0x%x\\n", data32);'
w0 = hardware.index(anchor, hardware.index("static int hdmitx_set_audmode(")) + len(anchor)
w1 = hardware.index("\thdmitx_wr_reg(HDMITX_DWC_AUD_N1", w0)
window = hardware[w0:w1]
if window.count("set_aud_fifo_rst();") != 1:
    raise RuntimeError("unexpected reset window: " + window)

mute_call = ("audio_mute_op(&hdmitx_device, AUDIO_MUTE_APPLY, flag);" if updated
             else "audio_mute_op(flag);")
prefix = r"""
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#define HDMITX_TOP_CLK_CNTL 0
#define HDMITX_DWC_FC_PACKET_TX_EN 1
#define HW ""
#define pr_info(...) ((void)0)
#define READ_ONCE(v) (v)
#define WRITE_ONCE(t, v) ((t) = (v))
#define DEFINE_SPINLOCK(x) int x
static int lock_depth __attribute__((unused)), warns;
#define spin_lock_irqsave(l, f) do { (void)(l); if (lock_depth++) abort(); (f) = 0; } while (0)
#define spin_unlock_irqrestore(l, f) do { (void)(l); (void)(f); if (--lock_depth) abort(); } while (0)
#define raw_local_save_flags(f) ((f) = 0)
#define local_fiq_disable() ((void)0)
#define raw_local_irq_restore(f) ((void)(f))
#define WARN_ON(c) ({ int w_ = !!(c); warns += w_; w_; })
struct hdmitx_dev { unsigned int tx_aud_cfg; };
static struct hdmitx_dev hdmitx_device __attribute__((unused));
static unsigned int registers[2], clocks_at_reset, clocks_after_delay, packets_at_reset;
static int resets;
static void (*during_reset)(void);
static unsigned int hdmitx_rd_reg(unsigned int a) __attribute__((unused));
static unsigned int hdmitx_rd_reg(unsigned int a) { return registers[a]; }
static void hdmitx_set_reg_bits(unsigned int a, unsigned int v, unsigned int s, unsigned int l) {
  unsigned int m = ((1u << l) - 1) << s;
  registers[a] = (registers[a] & ~m) | ((v << s) & m);
}
static void set_aud_fifo_rst(void) {
  ++resets; clocks_at_reset = registers[0] & 0xc; packets_at_reset = registers[1];
  if (during_reset) during_reset();
}
#define udelay(x) ((void)(x), clocks_after_delay = registers[0] & 0xc)
"""
suffix = r"""
static void reset_window(void) {
  bool clk_muted; (void)clk_muted;
WINDOW
}
static void mute(unsigned int flag) { MUTE_CALL }
static void mute_now(void) { mute(0); }
static void unmute_now(void) { mute(1); }
static void setup(int muted) {
  during_reset = NULL; resets = 0; warns = 0;
  clocks_at_reset = clocks_after_delay = packets_at_reset = 0xdead;
  amute_flag = muted ? 0 : 1;
  registers[0] = muted ? 0x20f3 : 0x20ff;   /* observed muted/unmuted TOP_CLK_CNTL */
  registers[1] = muted ? 0x16 : 0x1f;       /* observed muted/unmuted PACKET_TX_EN */
}
static int report(const char *name, bool ok) {
  printf("%s: %s (reset_clk=%x delay_clk=%x end_clk=%x end_pkt=%x cache=%d warns=%d)\n",
         name, ok ? "PASS" : "FAIL", clocks_at_reset, clocks_after_delay, registers[0],
         registers[1], amute_flag, warns);
  return !ok;
}
int main(void) {
  int failed = 0;
  setup(1); reset_window();
  failed += report("T1 muted reset runs with sampler clocks, re-gated, packets untouched",
    resets == 1 && clocks_at_reset == 0xc && clocks_after_delay == 0xc && packets_at_reset == 0x16 &&
    registers[0] == 0x20f3 && registers[1] == 0x16 && amute_flag == 0 && !warns);
  setup(0); reset_window();
  failed += report("T2 unmuted reset unchanged",
    clocks_at_reset == 0xc && clocks_after_delay == 0xc && registers[0] == 0x20ff &&
    registers[1] == 0x1f && amute_flag == 1 && !warns);
  setup(0); during_reset = mute_now; reset_window();
  failed += report("T3 mute inside window keeps clocks until window ends, then gates",
    clocks_at_reset == 0xc && clocks_after_delay == 0xc && (registers[0] & 0xc) == 0 &&
    (registers[1] & 9) == 0 && amute_flag == 0 && !warns);
  setup(1); during_reset = unmute_now; reset_window();
  failed += report("T4 unmute inside muted window leaves clocks/packets on",
    clocks_at_reset == 0xc && clocks_after_delay == 0xc && (registers[0] & 0xc) == 0xc &&
    (registers[1] & 9) == 9 && amute_flag == 1 && !warns);
BORROW_TESTS
  return failed ? 1 : 0;
}
"""
borrow_tests = r"""
  setup(1);
  bool a = audio_cfg_clk_borrow(true, false), b = audio_cfg_clk_borrow(true, false);
  unsigned int inner = registers[0] & 0xc;
  audio_cfg_clk_borrow(false, b); unsigned int one_left = registers[0] & 0xc;
  audio_cfg_clk_borrow(false, a);
  failed += report("T5 nested borrowers: last out re-gates",
    a && b && inner == 0xc && one_left == 0xc && (registers[0] & 0xc) == 0 &&
    registers[1] == 0x16 && !audio_cfg_clk_borrowers && !warns);
  setup(1); audio_cfg_clk_borrow(false, false);
  failed += report("T6 unbalanced release warns without register change",
    warns == 1 && registers[0] == 0x20f3 && registers[1] == 0x16 && !audio_cfg_clk_borrowers);
""" if has_borrow else '  puts("T5/T6 borrow-counter cases: N/A (no audio_cfg_clk_borrow)");\n'
program = (prefix + state + "\n" + borrow + "\n" + suffix.replace("WINDOW", window)
           .replace("MUTE_CALL", mute_call).replace("BORROW_TESTS", borrow_tests))
case = HERE / ("borrow-" + args.tag)
case.mkdir(exist_ok=True)
(case / "actual-body.c").write_bytes(program.encode())
linux = str(case)
subprocess.run(["gcc", "-std=gnu11", "-O1", "-g", "-Wall",
                "-fsanitize=address,undefined", "-no-pie", "-fno-omit-frame-pointer",
                linux + "/actual-body.c", "-o", linux + "/actual-body"], check=True)
result = subprocess.run([linux + "/actual-body"],
                        capture_output=True, text=True, timeout=30)
print(result.stdout, end="")
expected = 0 if args.expect == "pass" else 1
receipt = {"source": args.source, "original": args.original, "tag": args.tag,
           "expected_exit": expected, "exit": result.returncode, "stdout": result.stdout,
           "stderr": result.stderr, "has_borrow": has_borrow, "updated_mute": updated,
           "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
           "scope": __doc__.strip().splitlines()[-1]}
(case / "receipt.json").write_text(json.dumps(receipt, indent=2))
if result.returncode != expected or result.stderr:
    raise RuntimeError("clock-borrow regression did not meet expected outcome")
