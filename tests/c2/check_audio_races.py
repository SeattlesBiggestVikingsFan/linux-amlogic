"""Exercise actual kernel mute and bit-update bodies under a deterministic IRQ interleaving."""
import argparse
import os
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(os.environ["KERNEL_TEST_OUTPUT"]).resolve()
HERE.mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--original", action="store_true")
parser.add_argument("--original-register-body", action="store_true")
parser.add_argument("--original-mute-body", action="store_true")
parser.add_argument("--source", required=True)
parser.add_argument("--tag", default="")
args = parser.parse_args()
SOURCE = args.source

def read(relative, original=False):
    command = (["git", "-C", SOURCE, "show", "5a4eaf46237b12a6c1c5c951262fa2d5cbcbdfbb:" + relative]
               if args.original or original else ["cat", SOURCE + "/" + relative])
    return subprocess.check_output([*command]).decode()

def function(text, declaration):
    begin = text.index(declaration)
    brace = text.index("{", begin)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[begin:end]

hardware = read("drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/hw/hdmi_tx_hw.c",
                args.original_mute_body)
registers = read("drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/hw/reg_ops.c",
                 args.original_register_body)
mute = function(hardware, "static void audio_mute_op(")
updated = "enum audio_mute_action action" in mute
if updated:
    mute = mute.replace("audio_mute_op(", "audio_mute_apply(", 1)
    mute += "\nstatic void audio_mute_op(bool flag) { audio_mute_apply(&hdmitx_device, AUDIO_MUTE_APPLY, flag); }\n"

bits = function(registers, "void hdmitx_set_reg_bits(unsigned int addr")
prefix = r"""
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#define HDMITX_TOP_CLK_CNTL 0
#define HDMITX_DWC_FC_PACKET_TX_EN 1
#define DEFINE_SPINLOCK(name) int name
static unsigned int registers[2];
static unsigned long irq_depth, fiq_depth;
static int pending = -1, injection, injected, amute_flag = -1;
static DEFINE_SPINLOCK(audio_mute_lock);
static DEFINE_SPINLOCK(reg_bits_lock);
static void lab_hbr_tx_trace(const char *phase, int codec, int channels) {
  (void)phase; (void)codec; (void)channels;
}
static void audio_mute_op(bool flag);
void hdmitx_set_reg_bits(unsigned int, unsigned int, unsigned int, unsigned int);
static void dispatch(void) {
  if (pending >= 0 && !irq_depth && !fiq_depth) {
    int flag = pending;
    pending = -1;
    audio_mute_op(flag);
  }
}
#define raw_local_save_flags(flags) ((flags) = fiq_depth)
#define local_fiq_disable() (++fiq_depth)
#define raw_local_irq_restore(flags) do { fiq_depth = (flags); dispatch(); } while (0)
#define spin_lock_irqsave(lock, flags) do { (void)(lock); (flags) = irq_depth++; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(lock); irq_depth = (flags); dispatch(); } while (0)
static void interrupt(int flag) {
  if (irq_depth || fiq_depth) pending = flag;
  else audio_mute_op(flag);
}
static unsigned int hdmitx_rd_reg(unsigned int addr) {
  unsigned int value = registers[addr];
  if (injection == 1 && addr == HDMITX_DWC_FC_PACKET_TX_EN && !injected) {
    injected = 1;
    interrupt(0);
  }
  return value;
}
static void hdmitx_wr_reg(unsigned int addr, unsigned int value) {
  registers[addr] = value;
  if (injection == 2 && addr == HDMITX_TOP_CLK_CNTL && !injected) {
    injected = 1;
    interrupt(1);
  }
}
"""

if updated:
    prefix = prefix.replace("static unsigned int registers[2];",
        "struct hdmitx_dev { unsigned int tx_aud_cfg; };\n"
        "static struct hdmitx_dev hdmitx_device;\n"
        + function(hardware, "enum audio_mute_action") + ";\n"
        "#define READ_ONCE(value) (value)\n"
        "#define WRITE_ONCE(target, value) ((target) = (value))\n"
        "static unsigned int registers[2];")
if "static unsigned int audio_cfg_clk_borrowers;" in hardware:
    mute = "static unsigned int audio_cfg_clk_borrowers;\n" + mute
suffix = r"""
static void reset(void) {
  registers[0] = 0x20ff;
  registers[1] = 0x1f;
  irq_depth = fiq_depth = 0;
  amute_flag = 1;
  pending = -1;
  injection = injected = 0;
}
int main(void) {
  int failures = 0;
  reset();
  injection = 1;
  hdmitx_set_reg_bits(HDMITX_DWC_FC_PACKET_TX_EN, 1, 7, 1);
  bool lost_mute = (registers[1] & 9) != 0 || amute_flag != 0 ||
                   (registers[1] & 0x80) == 0;
  printf("packet read/modify/write vs HDR mute: %s (cache=%d packets=%x)\n",
         lost_mute ? "FAIL" : "PASS", amute_flag, registers[1]);
  failures += lost_mute;
  reset();
  injection = 2;
  audio_mute_op(0);
  bool stale_cache = amute_flag != 1 || (registers[0] & 12) != 12 ||
                     (registers[1] & 9) != 9;
  printf("mute sequence vs unmute IRQ: %s (cache=%d clocks=%x packets=%x)\n",
         stale_cache ? "FAIL" : "PASS", amute_flag, registers[0], registers[1]);
  failures += stale_cache;
  reset();
  audio_mute_op(0);
  if (amute_flag != 0 || (registers[0] & 12) || (registers[1] & 9)) abort();
  audio_mute_op(0);
  audio_mute_op(1);
  audio_mute_op(1);
  if (amute_flag != 1 || (registers[0] & 12) != 12 ||
      (registers[1] & 9) != 9 || registers[0] != 0x20ff || registers[1] != 0x1f ||
      irq_depth || fiq_depth) abort();
  puts("serial mute/unmute and unrelated-bit preservation: PASS");
  for (unsigned int mask = 0; mask < 4; ++mask) {
    reset(); irq_depth = mask & 1; fiq_depth = (mask >> 1) & 1;
    audio_mute_op(0); audio_mute_op(1);
    if (irq_depth != (mask & 1) || fiq_depth != ((mask >> 1) & 1)) abort();
  }
  puts("IRQ/FIQ entry-mask preservation: PASS");
  return failures;
}
"""
name = "original" if args.original else "candidate"
if args.original_register_body:
    name += "-original-register"
if args.original_mute_body:
    name += "-original-mute"
if args.tag:
    if not args.tag.isalnum():
        raise ValueError("Tag must contain only letters/digits.")
    name += "-" + args.tag
case = HERE / name
case.mkdir(exist_ok=True)
program = prefix + "\n" + bits + "\n" + mute + "\n" + suffix
(case / "actual-body.c").write_text(program, encoding="ascii")
linux = str(case)
subprocess.run(["gcc", "-std=c11", "-O1", "-g",
                "-fsanitize=address,undefined", "-no-pie", "-fno-omit-frame-pointer",
                linux + "/actual-body.c", "-o", linux + "/actual-body"], check=True)
result = subprocess.run([linux + "/actual-body"],
                        capture_output=True, text=True)
expected = (int(args.original or args.original_register_body) +
            int(args.original or args.original_mute_body))
receipt = {"source": SOURCE, "original": args.original, "expected_exit": expected,
           "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
           "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
           "scope": "Deterministic IRQ/MMIO model executing actual production function bodies; not hardware causality."}
(case / "receipt.json").write_text(json.dumps(receipt, indent=2))
print(result.stdout)
if result.returncode != expected or result.stderr:
    raise RuntimeError("Actual-body IRQ regression did not meet expected result: " + str(receipt))
