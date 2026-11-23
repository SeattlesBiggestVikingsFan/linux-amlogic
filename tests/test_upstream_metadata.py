"""Compile published metadata-reader/OOTF bodies; no kernel build or hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def function(source, name):
    match = re.search(r"(?m)^(?:static\s+)?(?:inline\s+)?(?:unsigned\s+)?(?:int|bool|void)\s+" +
                      re.escape(name) + r"\s*\(", source)
    if not match:
        raise ValueError("Missing production function: " + name)
    start = source.index("{", match.start())
    end, depth = start + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end] + "\n"


def fixture(windows=1, percentiles=9, anchors=9, matrix=0):
    bits = []
    def put(value, width):
        bits.extend((value >> shift) & 1 for shift in range(width - 1, -1, -1))
    for value, width in ((0xb5, 8), (0x003c, 16), (1, 16), (4, 8), (1, 8), (windows, 2)):
        put(value, width)
    for _ in range(1, windows):
        for width in (16, 16, 16, 16, 16, 16, 8, 16, 16, 16, 1):
            put(0, width)
    put(1000, 27)
    put(bool(matrix), 1)
    if matrix:
        put(matrix, 5)
        put(matrix, 5)
        for _ in range(matrix * matrix):
            put(7, 4)
    for _ in range(windows):
        for value in (10000, 20000, 30000):
            put(value, 17)
        put(10000, 17)
        put(percentiles, 4)
        for index in range(percentiles):
            put(index * 7, 7)
            put((index + 1) * 1000, 17)
        put(0, 10)
    put(0, 1)
    for _ in range(windows):
        put(1, 1)
        put(100, 12)
        put(200, 12)
        put(anchors, 4)
        for index in range(anchors):
            put(index * 50, 10)
        put(0, 1)
    bits.extend([0] * ((-len(bits)) % 8))
    return bytes(sum(bits[index + shift] << (7 - shift) for shift in range(8))
                 for index in range(0, len(bits), 8))


def array(name, data):
    return "static const u8 %s[] = {%s};\n" % (name, ",".join(str(byte) for byte in data))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
hdr = args.source / "drivers/amlogic/media/enhancement/amvecm/hdr"
source = (hdr / "am_hdr10_plus.c").read_text()
header = (hdr / "am_hdr10_plus.h").read_text()
ootf = (hdr / "am_hdr10_plus_ootf.c").read_text()
ootf_header = (hdr / "am_hdr10_plus_ootf.h").read_text()
aux = (args.source / "drivers/amlogic/media/common/vfm/vframe_aux.h").read_text()
structs = header[header.index("#define HDR10_PLUS_PEAK_DIM"):header.index("struct cuva_md_bits_s")]
ootf_structs = ootf_header[ootf_header.index("#define N 10"):ootf_header.index("#define ORDER")]
readers = source[source.index("static int GetBits("):source.index("void cuva_metadata_bits_parse(")]
sei = source[source.index("static bool read_sei_value("):source.index("static void hdr10_plus_vf_md_parse(")]
parts = [r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define U32_MAX UINT32_MAX
#define GFP_ATOMIC 0
#define kmalloc(size, flags) malloc(size)
#define kfree free
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define pr_hdr(...) ((void)0)
#define NAL_UNIT_SEI 39
#define NAL_UNIT_SEI_SUFFIX 40
#define SEI_Syntax 4
static int hdr10_plus_printk;
static int cuva_calls;
static void cuva_hdr_metadata_parse(char *data, u32 size) {
    (void)data; (void)size; cuva_calls++;
}
static void *memchr_inv(const void *p, int value, size_t size) {
    const u8 *bytes = p;
    for (size_t i = 0; i < size; i++)
        if (bytes[i] != value) return (void *)(bytes + i);
    return NULL;
}
static u32 get_unaligned_be32(const void *p) {
    const u8 *b = p;
    return (u32)b[0] << 24 | (u32)b[1] << 16 | (u32)b[2] << 8 | b[3];
}
""", structs, ootf_structs, "struct vframe_hdr_plus_sei hdr_plus_sei;\n", readers, sei,
    function(aux, "vframe_aux_record"), function(ootf, "vframe_hdr_plus_sei_s_init"),
    function(ootf, "getpercentile_50_99"),
    array("one", fixture()), array("maximum", fixture(3, 15, 15, 25)),
    array("zero_windows", fixture(0)),
    array("invalid_matrix", fixture(matrix=1)),
    r"""
static void be32(u8 *p, u32 value) {
    p[0] = value >> 24; p[1] = value >> 16; p[2] = value >> 8; p[3] = value;
}
static size_t aux_packet(u8 *packet, const u8 *metadata, size_t length) {
    size_t i = 8;
    packet[i++] = 39 << 1; packet[i++] = 1;
    packet[i++] = 4;
    size_t remaining = length;
    while (remaining >= 255) { packet[i++] = 255; remaining -= 255; }
    packet[i++] = remaining;
    memcpy(packet + i, metadata, length); i += length;
    packet[i++] = 0x80;
    be32(packet, i - 8); be32(packet + 4, 0x02000000);
    return i;
}
int main(void) {
    int value;
    const char high_bit[] = { (char)0x80, (char)0xff };
    assert(GetBits(high_bit, 0, &value, 2, 8) == 8 && value == 128);
    assert(GetBits(high_bit, 7, &value, 2, 9) == 9 && value == 255);
    assert(GetBits(high_bit, 16, &value, 2, 1) < 0 && value == 0);
    assert(GetBits(high_bit, -1, &value, 2, 1) < 0);
    assert(GetBits(high_bit, 0, &value, 2, 32) < 0);
    assert(GetBits(high_bit, INT_MAX, &value, 2, 31) < 0);
    struct vframe_hdr_plus_sei parsed;
    assert(parse_hdr10_plus((const char *)one, sizeof(one), &parsed));
    assert(parsed.num_windows == 1 && parsed.maxscl[0][2] == 30000);
    assert(parsed.num_distribution_maxrgb_percentiles[0] == 9);
    for (size_t size = 0; size < sizeof(one); size++)
        assert(!parse_hdr10_plus((const char *)one, size, &parsed));
    assert(!parse_hdr10_plus((const char *)zero_windows, sizeof(zero_windows), &parsed));
    assert(!parse_hdr10_plus((const char *)invalid_matrix, sizeof(invalid_matrix), &parsed));
    assert(parse_hdr10_plus((const char *)maximum, sizeof(maximum), &parsed));
    assert(parsed.num_windows == 3 && parsed.num_bezier_curve_anchors[2] == 15);
    assert(parsed.num_rows_tgt_sys_disp_act_pk_lumi == 25);
    assert(parsed.tgt_sys_disp_act_pk_lumi[24][24] == 7);
    struct { struct hdr10_plus_sei_s value; u32 guard; } mapped = { .guard = 0x12345678 };
    hdr_plus_sei = parsed;
    vframe_hdr_plus_sei_s_init(&mapped.value);
    assert(mapped.guard == 0x12345678);
    assert(mapped.value.num_distributions[0] == 15);
    assert(mapped.value.distribution_values[0][14] == 15000);
    assert(mapped.value.num_bezier_curve_anchors[0] == N - 1);
    struct percentiles pct = { .num_percentile = 15 };
    for (int i = 0; i < 15; i++) {
        pct.percentilepercent[i] = i * 7;
        pct.percentilevalue[i] = i * 700;
    }
    pct.percentilepercent[14] = 99; pct.percentilevalue[14] = 9900;
    int p50, p99;
    getpercentile_50_99(&pct, &p50, &p99);
    assert(p50 == 5000 && p99 == 9900);
    u8 packet[8192];
    size_t length = aux_packet(packet, one, sizeof(one));
    assert(parse_aux_metadata(packet, length));
    struct vframe_hdr_plus_sei previous = hdr_plus_sei;
    for (size_t size = 1; size < length; size++) {
        assert(!parse_aux_metadata(packet, size));
        assert(!memcmp(&hdr_plus_sei, &previous, sizeof(previous)));
    }
    length = aux_packet(packet, maximum, sizeof(maximum));
    assert(parse_aux_metadata(packet, length));
    assert(hdr_plus_sei.num_windows == 3);
    previous = hdr_plus_sei;
    length = aux_packet(packet, one, sizeof(one));
    packet[length] = 0;
    assert(!parse_aux_metadata(packet, length + 1));
    assert(!memcmp(&hdr_plus_sei, &previous, sizeof(previous)));
    u32 record_size, record_type;
    assert(vframe_aux_record((const char *)packet, length, &record_size, &record_type));
    assert(record_size == length - 8 && record_type == 0x02000000);
    assert(!vframe_aux_record((const char *)packet, 7, &record_size, &record_type));
    be32(packet, UINT32_MAX);
    assert(!vframe_aux_record((const char *)packet, length, &record_size, &record_type));
    return 0;
}
"""]
program = "".join(parts)
cases = [("accepted", program, True),
         ("no-bit-length-check", program.replace("(u64)offset + width > (u64)size * 8", "false"), False),
         ("commit-invalid-frame", program.replace("if (valid && pending)", "if (pending)"), False),
         ("truncate-percentiles", program.replace("int npsll = scenepercentiles->num_percentile;",
                                                  "int npsll = 9;"), False)]
rows = []
for name, data, expected in cases:
    directory = args.output / name
    directory.mkdir(exist_ok=True)
    file = directory / "actual-body.c"
    file.write_text(data)
    executable = directory / "actual-body"
    compiled = subprocess.run(["gcc", "-std=gnu11", "-O1", "-g", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                               "-fsanitize=address,undefined", "-Werror=implicit-function-declaration",
                               str(file), "-o", str(executable)], capture_output=True, text=True)
    if compiled.returncode:
        raise RuntimeError(compiled.stderr)
    env = os.environ.copy()
    env["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30, env=env)
    log = result.stdout + result.stderr
    (directory / "result.log").write_text(log)
    met = (result.returncode == 0) == expected
    rows.append({"id": name, "expected_pass": expected, "met_expectation": met,
                 "exit": result.returncode, "actual_body_sha256": hashlib.sha256(data.encode()).hexdigest(),
                 "log_sha256": hashlib.sha256(log.encode()).hexdigest()})
    print(("PASS " if met else "FAIL ") + name, flush=True)
    if not met:
        print(log)
receipt = {"passed": all(row["met_expectation"] for row in rows), "cases": rows,
           "scope": "Extracted actual upstream C bodies under host ASan/UBSan, not kernel compilation, concurrency or hardware certification."}
(args.output / "upstream-metadata-tests.json").write_text(json.dumps(receipt, indent=2) + "\n")
if not receipt["passed"]:
    raise SystemExit(1)
