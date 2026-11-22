"""Execute accepted ABI1 store/expiry/restore bodies with deterministic host stubs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
text = (args.source / "drivers/net/ethernet/stmicro/stmmac/dwmac-meson.c").read_text()


def extract(signature):
    start = text.index(signature)
    pos = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[pos] == "{") - (text[pos] == "}")
        pos += 1
    return text[start:pos]


prefix = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
#define HZ 100
struct device { int unused; };
struct device_attribute { int unused; };
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
struct net_device { bool running; };
struct meson_dwmac {
  struct device *dev;
  struct net_device *calibration_dev;
  struct delayed_work calibration_timeout;
  bool calibration_lease, calibration_applied, calibration_suspending;
  unsigned long calibration_expires;
  int calibration_index, calibration_accepted, calibration_error;
};
struct plat { struct meson_dwmac *bsp_priv; };
struct stmmac_priv { struct plat *plat; };
static struct meson_dwmac dwmac;
static struct net_device ndev;
static struct plat plat = { &dwmac };
static struct stmmac_priv priv = { &plat };
static unsigned long jiffies, last_delay;
static int lock_depth, switch_error, closes, schedules, cancels, warnings;
static void *system_wq;
static struct net_device *to_net_dev(struct device *d) { (void)d; return &ndev; }
static struct stmmac_priv *netdev_priv(struct net_device *d) { (void)d; return &priv; }
static void rtnl_lock(void) { assert(!lock_depth++); }
static void rtnl_unlock(void) { assert(lock_depth-- == 1); }
static bool netif_running(struct net_device *d) { return d->running; }
static void dev_close(struct net_device *d) { d->running = false; ++closes; }
static int meson_calibration_switch(struct meson_dwmac *d, int index) {
  assert(lock_depth == 1);
  if (switch_error) return switch_error;
  d->calibration_index = index; d->calibration_applied = true; return 0;
}
static bool sysfs_streq(const char *a, const char *b) {
  size_t n = strlen(a); if (n && a[n - 1] == '\n') --n;
  return n == strlen(b) && !strncmp(a, b, n);
}
static int kstrtoint(const char *s, int base, int *out) {
  char *end; long n; errno = 0; n = strtol(s, &end, base);
  if (errno || s == end || (*end && strcmp(end, "\n")) || n < INT32_MIN || n > INT32_MAX)
    return -EINVAL;
  *out = (int)n; return 0;
}
static void mod_delayed_work(void *q, struct delayed_work *w, unsigned long delay) {
  (void)q; (void)w; last_delay = delay; ++schedules;
}
static void cancel_delayed_work(struct delayed_work *w) { (void)w; ++cancels; }
#define to_delayed_work(w) ((struct delayed_work *)(w))
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define time_before(a, b) ((long)((a) - (b)) < 0)
static void dev_warn(struct device *d, const char *format, ...) {
  (void)d; (void)format; ++warnings;
}
"""
suffix = r"""
static ssize_t command(const char *s) {
  ssize_t result = ce_calibration_store(NULL, NULL, s, strlen(s));
  assert(lock_depth == 0); return result;
}
int main(void) {
  dwmac.calibration_dev = &ndev; ndev.running = true;
  dwmac.calibration_index = dwmac.calibration_accepted = -1;
  assert(command("set 2") == -EINVAL);
  assert(command("accept") == -EINVAL);
  assert(command("restore") == -EINVAL);
  assert(command("profile 5") == 9 && dwmac.calibration_accepted == 5);
  assert(command("begin") == 5 && dwmac.calibration_lease);
  assert(command("begin") == -EBUSY);
  assert(command("profile 6") == -EINVAL);
  assert(command("default") == -EBUSY);
  assert(command("set 32") == -EINVAL && command("set -1") == -EINVAL);
  assert(command("set bad") == -EINVAL && command("unknown") == -EINVAL);
  assert(command("set 7") == 5 && dwmac.calibration_index == 7);
  dwmac.calibration_applied = false;
  assert(command("accept") == -EINVAL);
  dwmac.calibration_applied = true;
  jiffies = 500; assert(command("keepalive") == 9);
  assert(dwmac.calibration_expires == 1500);
  jiffies = 1000; meson_calibration_expire(&dwmac.calibration_timeout.work);
  assert(dwmac.calibration_lease && dwmac.calibration_index == 7 && last_delay == 500);
  jiffies = 1500; meson_calibration_expire(&dwmac.calibration_timeout.work);
  assert(!dwmac.calibration_lease && dwmac.calibration_index == 5 && warnings == 1);
  assert(command("keepalive") == -EINVAL);
  assert(command("begin") == 5 && command("set 8") == 5);
  assert(command("accept") == 6 && !dwmac.calibration_lease && dwmac.calibration_accepted == 8);
  assert(command("begin") == 5 && command("set 9") == 5);
  jiffies += 100; assert(command("keepalive") == 9);
  meson_calibration_expire(&dwmac.calibration_timeout.work);
  assert(dwmac.calibration_lease && dwmac.calibration_index == 9);
  assert(command("restore") == 7 && !dwmac.calibration_lease && dwmac.calibration_index == 8);
  assert(command("begin") == 5 && command("set 10") == 6);
  switch_error = -EIO;
  assert(command("restore") == -EIO && !dwmac.calibration_lease);
  assert(!ndev.running && !dwmac.calibration_applied &&
         dwmac.calibration_index == 8 && dwmac.calibration_error == -EIO && closes == 1);
  switch_error = 0;
  dwmac.calibration_suspending = true;
  assert(command("begin") == -EBUSY);
  dwmac.calibration_suspending = false;
  assert(command("default") == 7 && dwmac.calibration_index == -1 &&
         dwmac.calibration_accepted == -1);
  assert(lock_depth == 0 && schedules > 0 && cancels > 0);
  puts("PASS ABI1 lease admission, ranges, profile exclusion, accept guards, renewal, stale expiry, rollback, rebegin, suspend and default");
  return 0;
}
"""
program = prefix + "\n".join(extract(s) for s in (
    "static int meson_calibration_restore(", "static void meson_calibration_expire(",
    "static ssize_t ce_calibration_store(")) + suffix
args.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="ethernet-lease-") as directory:
    source = Path(directory) / "actual-body.c"
    binary = Path(directory) / "test"
    source.write_text(program)
    subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Wno-sign-compare",
                    "-Werror", "-O1", "-g", "-fsanitize=address,undefined", "-no-pie",
                    str(source), "-o", str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    print(result.stdout, end="")
    if result.returncode or result.stderr:
        raise RuntimeError(result.stderr + result.stdout)
(args.output / "ethernet-lease.json").write_text(json.dumps({
    "passed": True, "actual_body_sha256": hashlib.sha256(program.encode()).hexdigest(),
    "source_sha256": hashlib.sha256(text.encode()).hexdigest(), "stdout": result.stdout,
    "scope": "Actual store/expiry/restore source bodies with deterministic host stubs; no networking, PHY calibration, MMIO or hardware acceptance."
}, indent=2) + "\n")
