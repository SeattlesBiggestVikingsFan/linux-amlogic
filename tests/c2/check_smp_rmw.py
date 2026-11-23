"""Execute the actual bit-update body in a deterministic two-CPU lost-update schedule."""
import argparse
import os
import json
from pathlib import Path
import subprocess

HERE = Path(os.environ["KERNEL_TEST_OUTPUT"]).resolve()
HERE.mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--original", action="store_true")
parser.add_argument("--source", required=True)
args = parser.parse_args()
SOURCE = args.source
relative = "drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/hw/reg_ops.c"
command = (["git", "-C", SOURCE, "show", "5a4eaf46237b12a6c1c5c951262fa2d5cbcbdfbb:" + relative] if args.original else
           ["cat", SOURCE + "/" + relative])
source = subprocess.check_output([*command]).decode()
start = source.index("void hdmitx_set_reg_bits(unsigned int addr")
brace = source.index("{", start)
end, depth = brace + 1, 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
body = source[start:end]
prefix = r"""
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#define DEFINE_SPINLOCK(name) pthread_mutex_t name = PTHREAD_MUTEX_INITIALIZER
static DEFINE_SPINLOCK(reg_bits_lock);
static pthread_mutex_t gate = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition = PTHREAD_COND_INITIALIZER;
static atomic_uint reg;
static bool read_reached, release_read, other_blocked, other_done;
static _Thread_local bool pause_read;
static void lock(pthread_mutex_t *mutex) {
  if (pthread_mutex_trylock(mutex) == 0) return;
  pthread_mutex_lock(&gate);
  other_blocked = true;
  pthread_cond_broadcast(&condition);
  pthread_mutex_unlock(&gate);
  pthread_mutex_lock(mutex);
}
#define raw_local_save_flags(flags) ((flags) = 0)
#define local_fiq_disable() ((void)0)
#define raw_local_irq_restore(flags) ((void)(flags))
#define spin_lock_irqsave(mutex, flags) do { (flags) = 0; lock(mutex); } while (0)
#define spin_unlock_irqrestore(mutex, flags) do { (void)(flags); pthread_mutex_unlock(mutex); } while (0)
static unsigned int hdmitx_rd_reg(unsigned int addr) {
  (void)addr;
  unsigned int value = atomic_load(&reg);
  if (pause_read) {
    pthread_mutex_lock(&gate);
    read_reached = true;
    pthread_cond_broadcast(&condition);
    while (!release_read) pthread_cond_wait(&condition, &gate);
    pthread_mutex_unlock(&gate);
    pause_read = false;
  }
  return value;
}
static void hdmitx_wr_reg(unsigned int addr, unsigned int value) {
  (void)addr; atomic_store(&reg, value);
}
"""
suffix = r"""
static void *first(void *arg) {
  (void)arg; pause_read = true;
  hdmitx_set_reg_bits(0, 1, 0, 1);
  return NULL;
}
static void *second(void *arg) {
  (void)arg;
  hdmitx_set_reg_bits(0, 1, 1, 1);
  pthread_mutex_lock(&gate);
  other_done = true;
  pthread_cond_broadcast(&condition);
  pthread_mutex_unlock(&gate);
  return NULL;
}
int main(void) {
  pthread_t a, b;
  if (pthread_create(&a, NULL, first, NULL)) abort();
  pthread_mutex_lock(&gate);
  while (!read_reached) pthread_cond_wait(&condition, &gate);
  pthread_mutex_unlock(&gate);
  if (pthread_create(&b, NULL, second, NULL)) abort();
  pthread_mutex_lock(&gate);
  while (!other_done && !other_blocked) pthread_cond_wait(&condition, &gate);
  release_read = true;
  pthread_cond_broadcast(&condition);
  pthread_mutex_unlock(&gate);
  pthread_join(a, NULL);
  pthread_join(b, NULL);
  unsigned int result = atomic_load(&reg);
  printf("SMP independent-field update: %s (register=%x, expected=3)\n",
         result == 3 ? "PASS" : "FAIL", result);
  return result == 3 ? 0 : 1;
}
"""
name = "smp-original" if args.original else "smp-candidate"
out = HERE / name
out.mkdir(exist_ok=True)
(out / "actual-body.c").write_text(prefix + body + suffix)
linux = str(out)
subprocess.run(["gcc", "-std=c11", "-O1", "-g",
                "-pthread", "-fsanitize=address,undefined", "-no-pie", "-fno-omit-frame-pointer",
                linux + "/actual-body.c", "-o", linux + "/actual-body"], check=True)
result = subprocess.run([linux + "/actual-body"],
                        capture_output=True, text=True, timeout=30)
expected = 1 if args.original else 0
(out / "receipt.json").write_text(json.dumps({
    "source": SOURCE, "original": args.original, "expected_exit": expected,
    "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
    "scope": "Actual RMW body; deterministic concurrent register model, not physical MMIO.",
}, indent=2))
print(result.stdout)
if result.returncode != expected or result.stderr:
    raise RuntimeError("SMP actual-body regression failed.")
