"""Exercise actual mute frontends, backend and ACR transaction without hardware."""
import argparse
import os
import ast
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(os.environ["KERNEL_TEST_OUTPUT"]).resolve()
HERE.mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--original", action="store_true")
parser.add_argument("--v1", action="store_true")
parser.add_argument("--drm", action="store_true")
parser.add_argument("--source", required=True)
args = parser.parse_args()
if args.original and args.v1:
    raise ValueError("Choose one baseline.")
source = args.source
base = "drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/"


def read(path):
    command = ["git", "-C", source, "show", "b3fdef79fcf04d602849315f70edae9583138b72:" + path] if args.original else [
        "cat", source + "/" + path]
    return subprocess.check_output([*command]).decode()


def extract(text, signature):
    begin = text.index(signature)
    brace = text.index("{", begin)
    end, depth = brace + 1, 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[begin:end]


old = Path(__file__).resolve().parent / "state_constants.py"
constants = {node.targets[0].id: ast.literal_eval(node.value)
             for node in ast.parse(old.read_text()).body
             if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
             and node.targets[0].id in ("prefix", "suffix")}
hardware = read(base + "hw/hdmi_tx_hw.c")
main = read(base + ("hdmi_tx_main_drm.c" if args.drm else "hdmi_tx_main.c"))
header = read("include/linux/amlogic/media/vout/hdmi_tx/hdmi_tx_module.h")
public = extract(main, "void hdmitx_audio_mute_op(unsigned int flag)")
mute = extract(hardware, "static void audio_mute_op(")
updated = "enum audio_mute_action action" in mute
reapply = next(line.strip() for line in hardware.splitlines()
               if line.strip() in ("audio_mute_op(hdev->tx_aud_cfg);",
                                    "audio_mute_op(hdev, AUDIO_MUTE_CURRENT, 0);"))
dispatch = hardware[hardware.index("\tcase CONF_AUDIO_MUTE_OP:"):
                    hardware.index("\tcase CONF_VIDEO_MUTE_OP:")]
enum = extract(hardware, "enum audio_mute_action") + ";" if updated else ""
if "static unsigned int audio_cfg_clk_borrowers;" in hardware:
    enum += "\nstatic unsigned int audio_cfg_clk_borrowers;\n"
if updated:
    acr = extract(hardware, "static void audio_acr_confirm(void)")
else:
    start = hardware.index("\tdata32 = hdmitx_rd_reg", hardware.index("/* double confirm"))
    end = hardware.index("\thdmitx_set_reg_bits(HDMITX_DWC_FC_DATAUTO3", start)
    acr = "static void audio_acr_confirm(void) {\nunsigned int data32;\n" + hardware[start:end] + "}\n"
definitions = "\n".join(line for line in header.splitlines()
                        if line.startswith("#define ") and line.split()[1] in (
                            "CMD_CONF_OFFSET", "CONF_AUDIO_MUTE_OP", "CONF_AUDIO_SET_STATE",
                            "AUDIO_MUTE", "AUDIO_UNMUTE"))
prefix = constants["prefix"]
for name in ("CONF_AUDIO_MUTE_OP", "AUDIO_MUTE", "AUDIO_UNMUTE"):
    prefix = "\n".join(line for line in prefix.splitlines()
                       if not line.startswith("#define " + name + " ")) + "\n"
prefix = prefix.replace("#define DEFINE_SPINLOCK", definitions + """
#define READ_ONCE(value) (value)
#define WRITE_ONCE(target, value) ((target) = (value))
#define HW ""
#define pr_info(...) ((void)0)
static void modeled_lock(pthread_mutex_t *lock);
#define DEFINE_SPINLOCK""")
prefix = prefix.replace("pthread_mutex_lock(lock);", "modeled_lock(lock);")
prefix += r"""
static bool other_blocked, other_done;
static _Thread_local bool pause_read;
static void modeled_lock(pthread_mutex_t *lock) {
  int result = pthread_mutex_trylock(lock);
  if (!result) return;
  if (result != EBUSY) abort();
  pthread_mutex_lock(&gate);
  other_blocked = true;
  pthread_cond_broadcast(&condition);
  pthread_mutex_unlock(&gate);
  if (pthread_mutex_lock(lock)) abort();
}
static unsigned int hdmitx_rd_reg(unsigned int addr) {
  unsigned int value = registers[addr];
  if (pause_read) {
    pause_call = true; hold(); pause_read = false;
  }
  return value;
}
"""
prefix = "#include <errno.h>\n" + prefix
middle = """
static int dispatch(struct hdmitx_dev *hdev, unsigned int cmd, unsigned int argv) {
  hold();
  switch (cmd) {
""" + dispatch + "\ndefault: abort();\n}\nreturn 0;\n}\n"
suffix = constants["suffix"]
if updated:
    suffix = suffix.replace(
        "static void held_mute_call(bool flag)", """static void held_mute_call(
struct hdmitx_dev *hdev, enum audio_mute_action action, unsigned int flag)""")
    suffix = suffix.replace("audio_mute_op(flag);", "audio_mute_op(hdev, action, flag);")
    suffix = suffix.replace("#define audio_mute_op(flag) held_mute_call(flag)",
        "#define audio_mute_op(hdev, action, flag) held_mute_call(hdev, action, flag)")
suffix = suffix.replace("REAPPLY", reapply)
extra = r"""
static void *acr_first(void *unused) {
  (void)unused; pause_read = true; audio_acr_confirm(); return NULL;
}
static void *mute_second(void *unused) {
  (void)unused;
  hdmitx_audio_mute_op(0);
  pthread_mutex_lock(&gate);
  other_done = true;
  pthread_cond_broadcast(&condition);
  pthread_mutex_unlock(&gate);
  return NULL;
}
static bool acr_trial(void) {
  reached = released = other_blocked = other_done = false;
  amute_flag = 1; registers[0] = 0xc; registers[1] = 8;
  hdmitx_device.tx_aud_cfg = 1;
  pthread_t a, b;
  if (pthread_create(&a, NULL, acr_first, NULL)) abort();
  pthread_mutex_lock(&gate);
  while (!reached) pthread_cond_wait(&condition, &gate);
  pthread_mutex_unlock(&gate);
  if (pthread_create(&b, NULL, mute_second, NULL)) abort();
  pthread_mutex_lock(&gate);
  while (!other_blocked && !other_done) pthread_cond_wait(&condition, &gate);
  released = true; pthread_cond_broadcast(&condition);
  pthread_mutex_unlock(&gate);
  if (pthread_join(a, NULL) || pthread_join(b, NULL)) abort();
  bool ok = amute_flag == 0 && !(registers[0] & 0xc) && !(registers[1] & 9);
  printf("Late ACR confirmation versus mute: %s (cache=%d clocks=%x packets=%x)\n",
         ok ? "PASS" : "FAIL", amute_flag, registers[0], registers[1]);
  return ok;
}
static void serial_rules(void) {
  amute_flag = -1; registers[0] = 0; registers[1] = 0;
  hdmitx_device.tx_aud_cfg = 0;
  for (unsigned int state = 0; state <= 2; ++state) {
    hdmitx_audio_mute_op(state);
    if (hdmitx_device.tx_aud_cfg != state || amute_flag != (state != 0)) abort();
    if ((registers[0] & 0xc) != (state ? 0xc : 0) ||
        (registers[1] & 9) != (state ? 9 : 0)) abort();
    hdmitx_audio_mute_op(state);
  }
  dispatch(&hdmitx_device, CONF_AUDIO_MUTE_OP, AUDIO_MUTE);
  if (hdmitx_device.tx_aud_cfg != 2 || amute_flag != 0 ||
      (registers[0] & 0xc) || (registers[1] & 9)) abort();
  reapply(&hdmitx_device);
  if (hdmitx_device.tx_aud_cfg != 2 || amute_flag != 1 ||
      (registers[0] & 0xc) != 0xc || (registers[1] & 9) != 9) abort();
  puts("States 0/1/2, repeated requests, transient mute and current reapply: PASS");
}
"""
suffix = suffix.replace("int main(void)", extra + "\nint main(void)")
suffix = suffix.replace("return public_ok && reapply_ok ? 0 : 1;",
                        "bool acr_ok = acr_trial(); serial_rules();\n"
                        "return public_ok && reapply_ok && acr_ok ? 0 : 1;")
program = prefix + enum + mute + acr + middle + public + suffix
name = ("original" if args.original else "v1" if args.v1 else "candidate") + (
    "-drm" if args.drm else "")
case = HERE / ("state-" + name)
case.mkdir(exist_ok=True)
file = case / "actual-body.c"
file.write_bytes(program.encode())
linux = str(case)
subprocess.run(["gcc", "-std=c11", "-O1", "-g",
                "-pthread", "-fsanitize=address,undefined", "-no-pie", "-fno-omit-frame-pointer",
                linux + "/actual-body.c", "-o", linux + "/actual-body"], check=True)
result = subprocess.run([linux + "/actual-body"],
                        capture_output=True, text=True, timeout=30)
expected = 1 if args.original or args.v1 else 0
receipt = {"source": source, "expected_exit": expected, "exit": result.returncode,
           "stdout": result.stdout, "stderr": result.stderr,
           "program_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
           "frontend": "drm" if args.drm else "normal",
           "scope": "Actual frontend/backend/dispatch bodies, mode call expression and ACR block; host model, not hardware causality."}
(case / "receipt.json").write_text(json.dumps(receipt, indent=2))
print(result.stdout)
if (result.returncode != expected or result.stderr or
        result.stdout.count("FAIL") != (3 if expected else 0)):
    raise RuntimeError("Actual state/ACR regression did not meet expected outcome.")
