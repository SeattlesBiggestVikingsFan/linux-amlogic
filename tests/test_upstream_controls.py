"""Exercise published PQ argument/lifetime and CVM indexing bodies on the host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def function(source, name):
    match = re.search(r"(?m)^(?:static\s+)?(?:inline\s+)?(?:ssize_t|int|bool)\s+" +
                      re.escape(name) + r"\s*\(", source)
    if not match:
        raise ValueError(name)
    start = source.index("{", match.start())
    end, depth = start + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end] + "\n"


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
base = args.source / "drivers/amlogic/media/enhancement"
pq = (base / "amvecm/amvecm.c").read_text()
dv = (base / "amdolby_vision/amdolby_vision.c").read_text()
table = dv[dv.index("static bool skip_cvm_tbl"):dv.index("static bool need_skip_cvm(")]
pq_descriptor = pq[pq.index("case AMVECM_IOC_G_PQ_CTRL:"):pq.index("case ", pq.index("case AMVECM_IOC_G_PQ_CTRL:") + 5)]
assert "memset(&pq_ctrl, 0, sizeof(pq_ctrl))" in pq_descriptor
assert "pq_ctrl.length = sizeof(pq_cfg)" in pq_descriptor and "pq_ctrl.ptr =" not in pq_descriptor
source = r"""
#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
#include <sys/types.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define GFP_KERNEL 0
#define kstrdup(p, flags) strdup(p)
#define kfree free
#define READ_ONCE(v) (v)
#define NUM_LUT 4
#define VI_HIST_CTRL 0
#define VI_HIST_H_START_END 1
#define VI_HIST_V_START_END 2
#define cpu_after_eq(...) false
#define pr_info(...) ((void)0)
#define FORMAT_INVALID 0xff
#define FORMAT_SDR 2
#define FORMAT_DOVI_LL 3
#define FLAG_CERTIFICAION 1
#define FLAG_FORCE_CVM 2
struct class { int unused; };
struct class_attribute { int unused; };
static unsigned int dolby_vision_flags, dolby_vision_graphics_priority;
static struct { unsigned int src_format, dst_format; bool dovi_ll_enable; } new_dovi_setting;
static int writes, loads;
#define WRITE_VPP_REG_BITS(...) ((void)writes++)
#define WRITE_VPP_REG(...) ((void)writes++)
static int kstrtoint(const char *text, int base, int *out) {
    char *end; long value;
    if (!text || !*text) return -EINVAL;
    errno = 0; value = strtol(text, &end, base);
    if (errno || *end || value < INT_MIN || value > INT_MAX) return -EINVAL;
    *out = value; return 0;
}
static int kstrtouint(const char *text, int base, unsigned int *out) {
    int value;
    if (kstrtoint(text, base, &value) || value < 0) return -EINVAL;
    *out = value; return 0;
}
static void vpp_lut_curve_load(unsigned int lut, const char *path) {
    assert(lut == 2 && !strcmp(path, "public-lut")); loads++;
}
""" + table + function(dv, "need_skip_cvm") + function(pq, "parse_param_amvecm") + \
    function(pq, "custom_lut_store") + function(pq, "amvecm_dump_vpp_hist_store") + r"""
int main(void) {
    char text[] = " a\tb\r\n"; char *parts[2];
    assert(parse_param_amvecm(text, parts, 2) == 2);
    assert(!strcmp(parts[0], "a") && !strcmp(parts[1], "b"));
    char overflow[] = "a b c d e f";
    assert(parse_param_amvecm(overflow, parts, 2) == -E2BIG);
    const char *valid = " 2\tpublic-lut\r\n";
    assert(custom_lut_store(NULL, NULL, valid, strlen(valid)) == strlen(valid) && loads == 1);
    const char *invalid[] = { "", "2", "2 public-lut extra", "-1 public-lut", "4 public-lut" };
    for (unsigned int i = 0; i < ARRAY_SIZE(invalid); i++)
        assert(custom_lut_store(NULL, NULL, invalid[i], strlen(invalid[i])) == -EINVAL);
    assert(loads == 1);
    const char *window = "3 10 20 100 200";
    assert(amvecm_dump_vpp_hist_store(NULL, NULL, window, strlen(window)) == strlen(window));
    assert(writes == 4);
    const char *bad_hist[] = { "", "-1", "x", "1 2 3 4 5 6", "2147483648", "1 -2147483649" };
    for (unsigned int i = 0; i < ARRAY_SIZE(bad_hist); i++) {
        int previous = writes;
        assert(amvecm_dump_vpp_hist_store(NULL, NULL, bad_hist[i], strlen(bad_hist[i])) == -EINVAL);
        assert(writes == previous);
    }
    for (unsigned int graphic = 0; graphic < 2; graphic++)
        for (unsigned int priority = 0; priority < 2; priority++)
            for (unsigned int src = 0; src < 4; src++)
                for (unsigned int dst = 0; dst < 4; dst++) {
                    dolby_vision_graphics_priority = priority;
                    new_dovi_setting.src_format = src;
                    new_dovi_setting.dst_format = dst;
                    assert(need_skip_cvm(graphic) == skip_cvm_tbl[graphic][priority][src][dst]);
                }
    assert(!need_skip_cvm(2));
    dolby_vision_graphics_priority = 2;
    assert(!need_skip_cvm(0));
    dolby_vision_graphics_priority = 0; new_dovi_setting.src_format = 6;
    assert(!need_skip_cvm(0));
    new_dovi_setting.src_format = 0; new_dovi_setting.dst_format = 7;
    assert(!need_skip_cvm(0));
    return 0;
}
"""
cases = [("accepted", source, True),
         ("unbounded-tokens", source.replace("if (n == capacity)", "if (false)"), False),
         ("freed-lut-path", source.replace("vpp_lut_curve_load(lut, argv[1]);",
                                           "free(command); command = NULL; vpp_lut_curve_load(lut, argv[1]);"), False)]
rows = []
for name, data, expected in cases:
    directory = args.output / name
    directory.mkdir(exist_ok=True)
    file = directory / "actual-body.c"
    file.write_text(data)
    exe = directory / "actual-body"
    compiled = subprocess.run(["gcc", "-std=gnu11", "-O1", "-g", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                               "-fsanitize=address,undefined", "-Werror=implicit-function-declaration",
                               str(file), "-o", str(exe)], capture_output=True, text=True)
    if compiled.returncode:
        raise RuntimeError(compiled.stderr)
    env = os.environ.copy()
    env["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
    result = subprocess.run([str(exe)], capture_output=True, text=True, env=env, timeout=30)
    log = result.stdout + result.stderr
    (directory / "result.log").write_text(log)
    met = (result.returncode == 0) == expected
    rows.append({"id": name, "met_expectation": met, "expected_pass": expected, "exit": result.returncode,
                 "actual_body_sha256": hashlib.sha256(data.encode()).hexdigest(),
                 "log_sha256": hashlib.sha256(log.encode()).hexdigest()})
    print(("PASS " if met else "FAIL ") + name, flush=True)
    if not met:
        print(log)
receipt = {"passed": all(row["met_expectation"] for row in rows), "cases": rows,
           "pq_descriptor_zeroes_pointer": True, "scope": "Actual extracted host bodies with mocked register writes; no device/PQ programming or firmware compilation."}
(args.output / "upstream-control-tests.json").write_text(json.dumps(receipt, indent=2) + "\n")
if not receipt["passed"]:
    raise SystemExit(1)
