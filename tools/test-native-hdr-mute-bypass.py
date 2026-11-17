#!/usr/bin/env python3
"""Compile actual HDR callbacks/worker with kernel-ordered scheduling cutpoints."""
import argparse
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--negative-controls", action="store_true")
args = parser.parse_args()
spec = importlib.util.spec_from_file_location(
    "kernel_test", ROOT / "tools/test-pcm-native-layouts.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
MAIN = ROOT / "drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/hdmi_tx_main.c"
main = MAIN.read_text()
module = (ROOT / "include/linux/amlogic/media/vout/hdmi_tx/hdmi_tx_module.h").read_text()
vinfo = (ROOT / "include/linux/amlogic/media/vout/vinfo.h").read_text()
pip = (ROOT / "drivers/amlogic/media/enhancement/amvecm/amcsc_pip.c").read_text()

PRELUDE = r"""
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t u64;
typedef uint32_t u32;
typedef int atomic_t;
#define U64_MAX UINT64_MAX
#define BIT(n) (1U << (n))
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v) = (x))
static void (*atomic_cut_hook)(void);
static int atomic_cut_at = -1, atomic_reads;
/* Injects a concurrent HPD edge immediately before the selected atomic read. */
static int atomic_read_cut(const atomic_t *p) {
 if (atomic_cut_hook && atomic_reads++ == atomic_cut_at) {
  void (*hook)(void) = atomic_cut_hook; atomic_cut_hook = NULL; hook();
 }
 return *p;
}
#define atomic_read(p) atomic_read_cut(p)
static int atomic_inc_return(atomic_t *p) {
 *p = (int32_t)((uint32_t)*p + 1U); return *p;
}
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
static unsigned int diagnostics;
#define pr_err(...) (++diagnostics)
#define hdmi_debug() ((void)0)
#define GET_LOW8BIT(a) ((a) & 0xff)
#define GET_HIGH8BIT(a) (((a) >> 8) & 0xff)
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
"""

core = PRELUDE
for kind, name, source in (
    ("enum", "hdmi_hdr_transfer", module),
    ("enum", "hdmi_hdr_color", module),
    ("enum", "hdmitx_hdr_origin", module),
    ("struct", "hdmitx_hdr_bypass", module),
    ("struct", "master_display_info_s", vinfo),
):
    core += helpers.declaration(source, kind, name)
core += r"""
enum {
 HDMI_PACKET_DRM, HDMI_PACKET_VEND, STAT_HDR_TYPE,
 CONF_AVI_RGBYCC_INDIC, CONF_CLR_DV_VS10_SIG, CONF_AVI_BT2020,
 SET_AVI_BT2020 = 1, CLR_AVI_BT2020 = 0,
 EXTCON_DISP_HDMI = 0, HDMITX_HDR_EVENT = 0, HDMI_Unknown = 0
};
struct hdr_info {
 unsigned int hdr_support;
 struct { unsigned int ieeeoui, application_version; } hdr10plus_info;
};
struct work_struct { bool pending; };
struct hdmi_format_para { unsigned int cs, cd, cr, VIC; };
struct vinfo_s { unsigned int sync_duration_num, sync_duration_den, video_clk; };
struct hdmitx_dev {
 int edid_spinlock;
 struct hdmitx_hdr_bypass hdr_bypass;
 enum hdmi_hdr_transfer hdr_transfer_feature;
 enum hdmi_hdr_color hdr_color_feature;
 unsigned int colormetry, hdmi_current_hdr_mode, hdmi_last_hdr_mode;
 unsigned int sdr_hdr_feature, hdr10plus_feature, ready, hpd_state;
 unsigned int cur_VIC, frac_rate_policy, phy_idx;
 bool flag_3dfp, flag_3dtb, flag_3dss;
 char fmt_attr[16];
 struct { struct hdr_info hdr_info, hdr_info2; } rxcap;
 struct hdmi_format_para *para, *cur_video_param;
 struct vinfo_s *vinfo;
 struct work_struct work_hdr;
 struct {
  void (*setpacket)(int, unsigned char *, unsigned char *);
  int (*cntlconfig)(struct hdmitx_dev *, int, unsigned int);
  int (*getstate)(struct hdmitx_dev *, unsigned int, unsigned int);
 } hwop;
};
struct vout_device_s {
 void (*fresh_tx_hdr_pkt)(struct master_display_info_s *);
};
static struct hdmitx_dev hdmitx_device;
static struct hdmi_format_para para = {2, 6, 0, 32};
static struct vinfo_s timing = {24000, 1001, 74250000};
static int hdr_status_pos, hdmitx_extcon_hdr;
static unsigned int hdr_mute_frame = 20, locked, slept, uslept;
static unsigned int video_asserts, video_releases, audio_asserts, audio_releases;
static bool kernel_running, inject_assertion, inject_notify, independent_audio;
static unsigned int independent_releases, events[16], event_count;
static unsigned char packet[26];
static void hdmitx_set_drm_pkt(struct master_display_info_s *);
static void hdmitx_set_drm_pkt_from(struct master_display_info_s *,
                                  enum hdmitx_hdr_origin);
static struct vout_device_s hdmitx_vdev = {hdmitx_set_drm_pkt};
struct master_display_info_s hsty_drm_config_data[8], drm_config_data;
unsigned int hsty_drm_config_loc, hsty_drm_config_num;
static void lock(int *p) { (void)p; assert(!locked); locked = 1; }
static void unlock(int *p) { (void)p; assert(locked); locked = 0; }
#define spin_lock_irqsave(p,f) do { (f)=0; lock(p); } while (0)
#define spin_unlock_irqrestore(p,f) do { (void)(f); unlock(p); } while (0)
static bool schedule_work(struct work_struct *work) {
 assert(locked);
 if (work->pending) return false;
 work->pending = true; return true;
}
static bool cancel_work_sync(struct work_struct *work) {
 assert(!locked && !kernel_running);
 bool result = work->pending; work->pending = false; return result;
}
static void setpacket(int kind, unsigned char *db, unsigned char *hb) {
 if (kind == HDMI_PACKET_DRM) {
  memset(packet, 0, sizeof(packet));
  if (db && hb) memcpy(packet, db, sizeof(packet));
 }
}
static int cntlconfig(struct hdmitx_dev *dev, int kind, unsigned int value) {
 (void)dev; (void)kind; (void)value; return 0;
}
static int getstate(struct hdmitx_dev *dev, unsigned int kind, unsigned int value) {
 (void)dev; (void)kind; (void)value; return 0;
}
static bool hdmitx_dv_en(void) { return false; }
static void update_current_para(struct hdmitx_dev *dev) { (void)dev; }
static unsigned int hdmitx_get_frame_duration(void) { return 41701; }
static void msleep(unsigned int duration) {
 assert(!locked && duration == 1500); slept += duration;
}
static void usleep_range(unsigned int low, unsigned int high) {
 assert(!locked && high >= low); uslept += low;
 if (inject_assertion) {
  struct master_display_info_s data = {.features = (9 << 16) | (16 << 8)};
  inject_assertion = false; hdmitx_set_drm_pkt(&data);
 }
}
static void hdmitx_video_mute_op(unsigned int enable) {
 if (enable) ++video_releases; else ++video_asserts;
}
static void hdmitx_audio_mute_op(unsigned int enable) {
 if (enable) {
  ++audio_releases;
  if (independent_audio) ++independent_releases;
 } else ++audio_asserts;
}
static void extcon_set_state_sync(int dev, int id, int state) {
 (void)dev; (void)id; assert(!locked);
 if (inject_notify) {
  struct master_display_info_s data = {.features = (9 << 16) | (18 << 8)};
  inject_notify = false; hdmitx_set_drm_pkt(&data);
 }
 (void)state;
}
static void hdmitx_set_uevent(int kind, int state) {
 (void)kind; assert(!locked && event_count < 16); events[event_count++] = state;
}
"""
core += "static unsigned int hdr_native_last_entry;\n"
for name in (
    "hdmitx_hdr_fail", "hdmitx_hdr_advance", "hdmitx_hdr_forget",
    "hdmitx_queue_hdr", "hdmitx_close_hdr_work",
    "hdmitx_hdr_transport_changed", "hdmitx_hdr_link_changed",
    "hdmitx_hdr_transport_untrusted", "hdmitx_sdr_hdr_uevent",
    "hdmitx_hdr_attr_sep", "hdmitx_hdr_attr_same",
    "hdmitx_hdr_transport_ok", "hdmitx_hdr_commit", "hdmitx_hdr_transport_mismatch",
    "hdmitx_hdr_native",
    "hdr_work_func", "init_drm_db0", "hdmitx_set_drm_pkt_from",
    "hdmitx_set_drm_pkt", "hdmitx_hdr_packet_from",
):
    core += helpers.function(main, name)
core += r"""
static unsigned int phy_edges = 1, hpd_during_phy;
/* Actual hdmitx_cntl_misc(MISC_TMDS_PHY_OP) calls hdmitx_hdr_link_changed. */
static void hdmitx_pre_display_init(void) {
 for (unsigned int i = 0; i < phy_edges; ++i)
  hdmitx_hdr_link_changed(&hdmitx_device);
 if (hpd_during_phy) hdmitx_hdr_link_changed(&hdmitx_device);
}
"""
core += helpers.function(main, "hdmitx_hdr_pre_display_init")
core += r"""
static void (*before_phy)(void);
/* set_disp_mode_auto_locked order: snapshot, transport edge, PHY op, commit. */
static void modeset(void) {
 int link = atomic_read(&hdmitx_device.hdr_bypass.link_gen);
 hdmitx_hdr_transport_changed(&hdmitx_device);
 if (before_phy) before_phy();
 link = hdmitx_hdr_pre_display_init(&hdmitx_device, link);
 hdmitx_hdr_commit(&hdmitx_device, 0, link);
}
"""
core += r"""
static struct hdmitx_hdr_bypass *b = &hdmitx_device.hdr_bypass;
static struct master_display_info_s sdr = {.features = (1 << 16) | (1 << 8)};
static struct master_display_info_s hdr = {.features = (9 << 16) | (16 << 8)};
static void send_hdr(void) {
 assert(hdmitx_hdr_packet_from(&hdmitx_vdev, &hdr,
                              HDMITX_HDR_ORIGIN_PLAIN_HDR10));
}
static void dispatch_begin(void) {
 assert(hdmitx_device.work_hdr.pending && !kernel_running);
 kernel_running = true;
 /* kernel/workqueue.c clears PENDING BEFORE entering the driver callback. */
 hdmitx_device.work_hdr.pending = false;
}
static void dispatch_end(void) {
 hdr_work_func(&hdmitx_device.work_hdr); assert(!locked); kernel_running = false;
}
static void run(void) { dispatch_begin(); dispatch_end(); }
static void setup(void) {
 hdmitx_device.para = hdmitx_device.cur_video_param = &para;
 hdmitx_device.vinfo = &timing;
 hdmitx_device.hwop.setpacket = setpacket;
 hdmitx_device.hwop.cntlconfig = cntlconfig;
 hdmitx_device.hwop.getstate = getstate;
 hdmitx_device.cur_VIC = 32;
 hdmitx_device.frac_rate_policy = 1;
 hdmitx_device.ready = hdmitx_device.hpd_state = 1;
 hdmitx_device.rxcap.hdr_info2.hdr_support = 12;
 b->initialized = b->enabled = true;
 memset(hdmitx_device.fmt_attr, 0xa5, 16); /* no terminator: bounded snapshot */
 hdmitx_hdr_transport_changed(&hdmitx_device);
 hdmitx_hdr_commit(&hdmitx_device, 0, atomic_read(&b->link_gen));
 hdmitx_set_drm_pkt(&sdr);
 assert(!b->outstanding && b->endpoint == 1 && hdmitx_hdr_transport_ok(&hdmitx_device));
}
static void cut_edge(void) { hdmitx_hdr_link_changed(&hdmitx_device); }
static void expect_zero(void) {
 send_hdr(); assert(b->outstanding == 1 && b->zero_next);
 assert(!video_asserts && !audio_asserts);
}
static void test(unsigned int number) {
 setup();
 switch (number) {
 case 0:
  independent_audio = true; expect_zero(); run();
  assert(!video_releases && !audio_releases && !independent_releases);
  assert(b->outstanding == 0 && !b->failed && events[0] == 1);
  break;
 case 1:
  expect_zero(); hdmitx_set_drm_pkt(&sdr);
  assert(b->outstanding == 1 && b->legacy_demand);
  run(); assert(b->outstanding == 1 && hdmitx_device.work_hdr.pending);
  assert(!video_releases && !audio_releases);
  run(); assert(slept == 1500 && b->outstanding == 0);
  break;
 case 2:
  expect_zero(); dispatch_begin();
  assert(!hdmitx_device.work_hdr.pending && b->outstanding == 1);
  hdmitx_set_drm_pkt(&sdr);
  assert(b->outstanding == 2 && hdmitx_device.work_hdr.pending);
  dispatch_end(); assert(b->outstanding == 1 && !audio_releases);
  run(); assert(slept == 1500 && !b->outstanding);
  break;
 case 3:
  expect_zero(); run(); hdmitx_set_drm_pkt(&sdr); send_hdr();
  assert(!video_asserts); /* Known legacy current==last rapid-reentry hole. */
  independent_audio = true; run();
  assert(audio_releases == 1 && independent_releases == 1);
  puts("KNOWN LEGACY LIMIT: busy fallback can unmute without a fresh assertion");
  break;
 case 4:
  b->enabled = false; send_hdr();
  assert(video_asserts == 1 && audio_asserts == 1 && b->owed);
  hdr_mute_frame = 0; run();
  assert(!audio_releases && b->owed && !b->outstanding);
  break;
 case 5:
  b->enabled = false; send_hdr(); inject_assertion = true; run();
  assert(b->owed && b->outstanding == 1 && video_asserts == 2);
  run(); assert(!b->owed && !b->outstanding);
  break;
 case 6:
  expect_zero(); hdmitx_hdr_transport_changed(&hdmitx_device); run();
  assert(!event_count && !audio_releases);
  break;
 case 7:
  expect_zero(); send_hdr(); assert(b->outstanding == 1); run();
  assert(!video_asserts && !audio_releases && event_count == 1);
  break;
 case 8:
  hdmitx_set_drm_pkt(&hdr); assert(video_asserts == 1 && !b->zero_next); run();
  assert(uslept == 20 * 41701 && video_releases == 1 && audio_releases == 1);
  break;
 case 9:
  hdr.features = (9 << 16) | (18 << 8); send_hdr();
  assert(video_asserts == 1 && !b->zero_next); run();
  break;
 case 10:
  b->endpoint = 0; send_hdr(); assert(video_asserts == 1); run();
  break;
 case 11:
  para.cd++; send_hdr(); assert(video_asserts == 1 && !b->zero_next); run();
  break;
 case 12:
  expect_zero(); hdmitx_close_hdr_work(&hdmitx_device);
  assert(b->closing && !b->outstanding && !hdmitx_device.work_hdr.pending);
  lock(&hdmitx_device.edid_spinlock);
  assert(!hdmitx_queue_hdr(&hdmitx_device, false));
  unlock(&hdmitx_device.edid_spinlock);
  assert(!b->outstanding && !audio_releases);
  break;
 case 13:
  hdr_work_func(&hdmitx_device.work_hdr);
  assert(b->failed && b->outstanding == 0 && diagnostics); break;
 case 14:
  b->outstanding = UINT_MAX;
  lock(&hdmitx_device.edid_spinlock);
  assert(!hdmitx_queue_hdr(&hdmitx_device, false));
  unlock(&hdmitx_device.edid_spinlock);
  assert(b->failed && b->outstanding == UINT_MAX && diagnostics); break;
 case 15:
  b->initialized = false;
  lock(&hdmitx_device.edid_spinlock);
  assert(!hdmitx_queue_hdr(&hdmitx_device, false));
  unlock(&hdmitx_device.edid_spinlock);
  assert(b->failed && !hdmitx_device.work_hdr.pending && diagnostics); break;
 case 16:
  b->requests = U64_MAX; hdmitx_set_drm_pkt(&hdr);
  assert(b->failed && !b->zero_next); run(); break;
 case 17:
  expect_zero(); b->enabled = false; run();
  assert(!audio_releases && !video_releases && event_count == 1); break;
 case 18:
  expect_zero(); inject_notify = true; run();
  assert(events[0] == 1 && b->outstanding == 1);
  assert(!video_releases && !audio_releases); run();
  assert(!b->outstanding && event_count == 1 &&
         hdmitx_device.hdmi_last_hdr_mode == 3); break;
 case 19: {
  struct vout_device_s other = {hdmitx_set_drm_pkt};
  assert(!hdmitx_hdr_packet_from(&other, &hdr, HDMITX_HDR_ORIGIN_PLAIN_HDR10));
  assert(!hdmitx_hdr_packet_from(&hdmitx_vdev, &hdr, HDMITX_HDR_ORIGIN_UNKNOWN));
  assert(!hdmitx_hdr_packet_from(&hdmitx_vdev, &hdr, 77));
  assert(!b->outstanding && !video_asserts); break;
 }
 case 20:
  expect_zero(); hdmitx_hdr_transport_untrusted(&hdmitx_device); run();
  assert(!event_count && !audio_releases);
  hdmitx_hdr_commit(&hdmitx_device, 0, atomic_read(&b->link_gen));
  assert(!b->applied_valid); break;
 case 21:
  b->applied_valid = false; send_hdr(); assert(video_asserts == 1);
  assert(hdr_native_last_entry == (1U << 31 | 1U << 12));
  assert(hdmitx_hdr_transport_mismatch(&hdmitx_device) == 1); run(); break;
 case 22:
  b->transport_gen = INT_MAX; hdmitx_hdr_transport_changed(&hdmitx_device);
  assert(b->untrusted); send_hdr(); assert(video_asserts == 1); run(); break;
 case 23:
  expect_zero(); dispatch_begin();
  b->closing = true; hdmitx_set_drm_pkt(&sdr);
  dispatch_end();
  assert(!b->outstanding && !hdmitx_device.work_hdr.pending && !event_count);
  break;
 case 24:
  b->outstanding = 3;
  lock(&hdmitx_device.edid_spinlock);
  assert(!hdmitx_queue_hdr(&hdmitx_device, false));
  unlock(&hdmitx_device.edid_spinlock);
  assert(b->failed && b->outstanding == 3 && diagnostics &&
         !hdmitx_device.work_hdr.pending); break;
 case 25:
  hdmitx_hdr_commit(&hdmitx_device, -1, atomic_read(&b->link_gen));
  assert(!b->applied_valid);
  hdmitx_set_drm_pkt(&sdr); send_hdr(); assert(video_asserts == 1); run(); break;
 case 26: {
  int old_link = atomic_read(&b->link_gen);
  hdmitx_hdr_link_changed(&hdmitx_device);
  hdmitx_hdr_commit(&hdmitx_device, 0, old_link);
  assert(!b->applied_valid);
  hdmitx_set_drm_pkt(&sdr); send_hdr(); assert(video_asserts == 1); run(); break;
 }
 case 27:
 case 28:
 case 29:
  if (number == 27) timing.video_clk = 0;
  if (number == 28) timing.sync_duration_num = 0;
  if (number == 29) timing.sync_duration_den = 0;
  send_hdr(); assert(video_asserts == 1 && !b->zero_next); run(); break;
 case 30:
  b->link_gen = INT_MAX; hdmitx_hdr_link_changed(&hdmitx_device);
  assert(b->untrusted && diagnostics);
  send_hdr(); assert(video_asserts == 1); run(); break;
 case 31:
  hdmitx_close_hdr_work(&hdmitx_device); send_hdr();
  assert(!video_asserts && !audio_asserts && !b->outstanding && !event_count);
  break;
 case 33:
 case 34:
 case 35:
 case 36:
 case 37: {
  /* Lock-free HPD edge at each atomic read inside the actual commit. */
  int old_link = atomic_read(&b->link_gen);
  atomic_reads = 0; atomic_cut_at = number - 33; atomic_cut_hook = cut_edge;
  hdmitx_hdr_commit(&hdmitx_device, 0, old_link);
  if (atomic_cut_hook) { atomic_cut_hook = NULL; cut_edge(); }
  assert(b->applied_link == old_link || !b->applied_valid);
  assert(!hdmitx_hdr_transport_ok(&hdmitx_device));
  hdmitx_set_drm_pkt(&sdr); send_hdr(); assert(video_asserts == 1); run(); break;
 }
 case 38: {
  /* Positive guard: same instrumentation without an edge still qualifies. */
  int link = atomic_read(&b->link_gen);
  atomic_reads = 0; atomic_cut_at = 1000; atomic_cut_hook = cut_edge;
  hdmitx_hdr_commit(&hdmitx_device, 0, link);
  assert(atomic_cut_hook && atomic_reads >= 4); atomic_cut_hook = NULL;
  assert(b->applied_valid && b->applied_link == link &&
         hdmitx_hdr_transport_ok(&hdmitx_device));
  hdmitx_set_drm_pkt(&sdr); expect_zero(); run();
  assert(hdr_native_last_entry == 1U << 31);
  assert(!video_releases && !audio_releases && !b->outstanding); break;
 }
 case 32:
  b->owed = true; send_hdr();
  assert(video_asserts == 1 && !b->zero_next); run(); break;
 case 39:
  /* A real modeset's own PHY disable must not invalidate its commit. */
  b->applied_valid = false; modeset();
  assert(b->applied_valid && hdmitx_hdr_transport_ok(&hdmitx_device));
  hdmitx_set_drm_pkt(&sdr); expect_zero(); run();
  assert(hdr_native_last_entry == 1U << 31 && !video_releases); break;
 case 40:
 case 41:
 case 42:
  /* HPD before the PHY op, HPD during it, or link overflow: no commit. */
  if (number == 40) before_phy = cut_edge;
  if (number == 41) hpd_during_phy = 1;
  if (number == 42) b->link_gen = INT_MAX;
  modeset();
  assert(!b->applied_valid && !hdmitx_hdr_transport_ok(&hdmitx_device));
  hdmitx_set_drm_pkt(&sdr); send_hdr(); assert(video_asserts == 1); run(); break;
 case 43: {
  /* store_attr "now" blanking, echo newline and zero padding are benign. */
  static const char *const same[][2] = {
   {",422,12bit,   ", ",422,12bit"}, {",422,12bit", ",422,12bit\n"},
   {"422,12bit, ,\n", "422,12bit"}, {"", "   "},
   {"422,12bit", ",422,12bit"}, {" ,\n422,12bit", "422,12bit,"}};
  for (unsigned int i = 0; i < sizeof(same) / sizeof(*same); ++i) {
   memset(hdmitx_device.fmt_attr, 0, 16);
   strcpy(hdmitx_device.fmt_attr, same[i][0]);
   memset(hdmitx_device.fmt_attr + strlen(same[i][0]) + 1, 0x5a,
          15 - strlen(same[i][0])); /* bytes after the terminator are ignored */
   modeset();
   memset(hdmitx_device.fmt_attr, 0, 16);
   strcpy(hdmitx_device.fmt_attr, same[i][1]);
   assert(hdmitx_hdr_transport_ok(&hdmitx_device) &&
          !hdmitx_hdr_transport_mismatch(&hdmitx_device));
  }
  hdmitx_set_drm_pkt(&sdr); expect_zero(); run();
  assert(hdr_native_last_entry == 1U << 31 && !video_releases); break;
 }
 case 44: {
  /* Any pending selection change, including one token prefix, still blocks. */
  static const char *const differ[][2] = {
   {",422,12bit", ",444,12bit"}, {",422,12bit", ",422,10bit"},
   {",422,12bit", ",422,12bi"}, {",422,12bit", ",422,12bit,full"},
   {",422,12bit", ",422,,12bit"}, {"rgb", ""}};
  for (unsigned int i = 0; i < sizeof(differ) / sizeof(*differ); ++i)
   for (unsigned int j = 0; j < 2; ++j) {
    memset(hdmitx_device.fmt_attr, 0, 16);
    strcpy(hdmitx_device.fmt_attr, differ[i][j]);
    modeset();
    assert(hdmitx_hdr_transport_ok(&hdmitx_device));
    memset(hdmitx_device.fmt_attr, 0, 16);
    strcpy(hdmitx_device.fmt_attr, differ[i][!j]);
    assert(!hdmitx_hdr_transport_ok(&hdmitx_device) &&
           hdmitx_hdr_transport_mismatch(&hdmitx_device) == 1U << 17);
   }
  hdmitx_set_drm_pkt(&sdr); send_hdr(); assert(video_asserts == 1); run(); break;
 }
 default: abort();
 }
}
int main(int argc, char **argv) {
 assert(argc == 2); test((unsigned int)atoi(argv[1])); return 0;
}
"""

producer = PRELUDE
amcsc_header = (ROOT / "drivers/amlogic/media/enhancement/amvecm/amcsc.h").read_text()
amvecm_header = (ROOT / "include/linux/amlogic/media/amvecm/amvecm.h").read_text()
producer += "\n".join(re.findall(
    r"^#define\s+\w+_SOURCE\s+.+$", amvecm_header, re.M)) + "\n"
for kind, name, source in (
    ("enum", "hdmitx_hdr_origin", module),
    ("enum", "hdr_type_e", amvecm_header),
    ("enum", "output_format_e", amcsc_header),
    ("struct", "master_display_info_s", vinfo),
):
    producer += helpers.declaration(source, kind, name)
producer += r"""
enum vd_path_e { VD1_PATH, VD2_PATH, VD_PATH_MAX };
enum { SIGNAL_SDR, SIGNAL_HDR10, SIGNAL_HLG, SIGNAL_HDR10PLUS, SIGNAL_CUVA };
enum { MESON_CPU_MAJOR_ID_G12B = 0x29, PROC_BYPASS = 0, HDR_MODULE_ON = 2 };
#define SIG_FORCE_CHG 1
#define SIG_PRI_INFO 2
#define pr_csc(...) ((void)0)
struct vframe_master_display_colour_s {
 u32 present_flag, primaries[3][2], white_point[2], luminance[2];
 struct { u32 present_flag, max_content, max_pic_average; } content_light_level;
};
struct hdr10plus_para { int unused; };
struct cuva_hdr_vsif_para { int unused; };
struct cuva_hdr_vs_emds_para { int unused; };
struct vd_signal_info_s { int signal_type; };
struct vout_device_s {
 void (*fresh_tx_hdr_pkt)(struct master_display_info_s *);
 void (*fresh_tx_hdr10plus_pkt)(unsigned int, struct hdr10plus_para *);
 void (*fresh_tx_cuva_hdr_vsif)(struct cuva_hdr_vsif_para *);
 void (*fresh_tx_cuva_hdr_vs_emds)(struct cuva_hdr_vs_emds_para *);
};
struct vinfo_s {
 struct vout_device_s *vout_device;
 struct { struct { int monitor_mode_sup; } cuva_info; } hdr_info;
};
static unsigned int customer_hdr_clipping, content_max_lumin[VD_PATH_MAX];
static enum output_format_e output_format, target_format[VD_PATH_MAX];
static enum hdr_type_e cur_source_format[VD_PATH_MAX];
static unsigned int hdr_process_mode[VD_PATH_MAX], cur_hdr_process_mode[VD_PATH_MAX];
static struct master_display_info_s dbg_hdr_send, captured;
static u32 bt2020_primaries[3][2], bt2020_white_point[2];
static unsigned int drm_calls, tagged, vendor_calls;
static int hdr_policy = 1, force_output = UNKNOWN_FMT, hdr_mode = 2;
static bool dv_enable, dv_on, vd2;
static int get_hdr10_plus_pkt_delay(void) { return 0; }
static int get_cuva_pkt_delay(void) { return 0; }
static int is_video_layer_on(enum vd_path_e p) { return p == VD1_PATH || vd2; }
static int get_cpu_type(void) { return MESON_CPU_MAJOR_ID_G12B; }
static int get_hdr_policy(void) { return hdr_policy; }
static int get_force_output(void) { return force_output; }
static int get_hdr_mode(void) { return hdr_mode; }
static int get_hdr_module_status(enum vd_path_e p) { (void)p; return HDR_MODULE_ON; }
static bool is_dolby_vision_enable(void) { return dv_enable; }
static bool is_dolby_vision_on(void) { return dv_on; }
static void update_hdr10_plus_pkt(bool a, void *b, void *c) { assert(0); }
static void update_cuva_pkt(bool a, void *b, void *c, void *d) { assert(0); }
static int notify_vd_signal_to_amvideo(struct vd_signal_info_s *p) { return 0; }
static void hdmitx_set_drm_pkt_from(struct master_display_info_s *data,
                                  enum hdmitx_hdr_origin origin) {
 assert(!vendor_calls); /* Provenance travels on the FIRST synchronous DRM. */
 ++drm_calls; if (origin == HDMITX_HDR_ORIGIN_PLAIN_HDR10) ++tagged;
 captured = *data;
}
static void old_drm(struct master_display_info_s *data) {
 hdmitx_set_drm_pkt_from(data, HDMITX_HDR_ORIGIN_UNKNOWN);
}
static void plus(unsigned int flag, struct hdr10plus_para *p) { ++vendor_calls; }
static void cuva(struct cuva_hdr_vsif_para *p) { ++vendor_calls; }
static void emds(struct cuva_hdr_vs_emds_para *p) { ++vendor_calls; }
static struct vout_device_s hdmitx_vdev = {old_drm, plus, cuva, emds};
"""
for source, name in (
    (main, "hdmitx_hdr_packet_from"),
    (pip, "prepare_hdr_info"),
    (pip, "hdmi_packet_process"),
):
    producer += helpers.function(source, name)
producer += r"""
int main(int argc, char **argv) {
 assert(argc == 2); int number = atoi(argv[1]);
 struct vout_device_s optional = hdmitx_vdev;
 struct vinfo_s info = {.vout_device = &hdmitx_vdev,
                       .hdr_info.cuva_info.monitor_mode_sup = 1};
 struct vframe_master_display_colour_s metadata = {
  .present_flag = 1, .primaries = {{100,200},{300,400},{500,600}},
  .white_point = {15635,16450}, .luminance = {10000000,50},
  .content_light_level = {1,1000,629}
 };
 struct hdr10plus_para dynamic = {0};
 struct cuva_hdr_vsif_para vsif = {0};
 struct cuva_hdr_vs_emds_para emds_data = {0};
 enum hdr_type_e source[VD_PATH_MAX] = {HDRTYPE_HDR10, HDRTYPE_SDR};
 bool unforced = true;
 output_format = BT709; target_format[VD1_PATH] = BT2020_PQ;
 cur_source_format[VD1_PATH] = HDRTYPE_HDR10;
 switch (number) {
 case 0: break;
 case 1:
  target_format[0] = BT2020_PQ_DYNAMIC;
  source[0] = cur_source_format[0] = HDRTYPE_HDR10PLUS; break;
 case 2:
  target_format[0] = BT2020YUV_BT2020RGB_CUVA;
  source[0] = cur_source_format[0] = HDRTYPE_CUVA_HDR; break;
 case 3: unforced = false; break;
 case 4: hdr_policy = 2; break;
 case 5: force_output = BT2020_PQ; break;
 case 6: dv_enable = true; break;
 case 7: dv_on = true; break;
 case 8: source[0] = cur_source_format[0] = HDRTYPE_SDR; break;
 case 9: source[0] = cur_source_format[0] = HDRTYPE_HLG; break;
 case 10: hdr_process_mode[0] = 1; break;
 case 11: cur_hdr_process_mode[0] = 1; break;
 case 12: vd2 = true; break;
 case 13: info.vout_device = &optional; break;
 case 14: hdr_mode = 1; break;
 case 15: cur_source_format[0] = HDRTYPE_HDR10PLUS; break;
 case 16: break; /* Unexpected HDR10+ metadata on an ordinary output. */
 default: abort();
 }
 hdmi_packet_process(SIG_FORCE_CHG, &info, &metadata,
                     number == 1 || number == 16 ? &dynamic : NULL,
                     number == 2 ? &vsif : NULL,
                     number == 2 ? &emds_data : NULL, VD1_PATH, source, unforced);
 assert(drm_calls == 1 && tagged == (number == 0));
 assert(captured.features == 0x3509100a);
 assert(memcmp(&captured, &dbg_hdr_send, sizeof(captured)) == 0);
 if (number == 0) {
  assert(captured.max_content == 1000 && captured.max_frame_average == 629);
  assert(captured.primaries[0][0] == 100 && captured.primaries[2][1] == 600);
 }
 assert(vendor_calls == (number == 1 || number == 2));
 return 0;
}
"""


def compile_run(program, cases, directory, name, should_fail=False):
    source, binary = directory / (name + ".c"), directory / name
    source.write_text(program)
    subprocess.run(
        ["cc", "-std=gnu99", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-unused-variable",
         "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie",
         str(source), "-o", str(binary)], check=True)
    for case in cases:
        result = subprocess.run([str(binary), str(case)], text=True,
                                capture_output=True)
        if should_fail:
            assert result.returncode != 0, (name, case, "negative control survived")
        else:
            if result.returncode:
                sys.stderr.write(result.stdout + result.stderr)
            assert result.returncode == 0, (name, case)
            if result.stdout:
                print(result.stdout.strip())


assert main.count("schedule_work(&hdev->work_hdr)") == 1
assert main.count("cancel_work_sync(&hdev->work_hdr)") == 1
assert main.count("hdmitx_queue_hdr(hdev, false)") == 4  # original three + replay
assert main.count("INIT_WORK(&hdmitx_device->work_hdr") == 1
assert main.count("hdmitx_close_hdr_work(hdev);") == 1
assert main.count("hdmitx_close_hdr_work(&hdmitx_device);") == 1
assert main.count("amhdmitx_device_init(") == 2  # definition and fresh probe only
kernel_worker = helpers.function((ROOT / "kernel/workqueue.c").read_text(),
                                 "process_one_work")
assert kernel_worker.index("set_work_pool_and_clear_pending(work, pool->id)") < \
       kernel_worker.index("worker->current_func(work)")
HW = ROOT / "drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/hw/hdmi_tx_hw.c"
hw = HW.read_text()
# The modeset stand-in mirrors actual source coupling: the PHY op bumps the link
# generation, and the actual modeset commits the adopted snapshot.
assert "if (cmd == MISC_TMDS_PHY_OP || cmd == MISC_TRIGGER_HPD)\n\t\thdmitx_hdr_link_changed(hdev);" in \
    helpers.function(hw, "hdmitx_cntl_misc")
assert "MISC_TMDS_PHY_OP" in helpers.function(main, "hdmitx_pre_display_init")
assert main.count("hdmitx_pre_display_init();") == 1
modeset_source = main[main.index("static int set_disp_mode_auto_locked(void)\n{"):]
modeset_source = modeset_source[:modeset_source.index("\n}\n")]
assert modeset_source.index("int link = atomic_read(&hdev->hdr_bypass.link_gen);") < \
    modeset_source.index("hdmitx_hdr_transport_changed(hdev);") < \
    modeset_source.index("link = hdmitx_hdr_pre_display_init(hdev, link);") < \
    modeset_source.index("ret = hdmitx_set_display(hdev, vic);") < \
    modeset_source.index("hdmitx_hdr_commit(hdev, ret, link);")
assert modeset_source.count("\tlink = ") == 1 and "hdmitx_pre_display_init();" not in modeset_source
# Attr comparison ignores only trailing ' ', ',' and '\n'; no parser token
# contains them, so trimming cannot change hdmi_parse_attr()'s selection.
params = (ROOT / "drivers/amlogic/media/vout/hdmitx/hdmi_common/hdmi_parameters.c").read_text()
tokens = []
for table in ("parse_cd_", "parse_cs_", "parse_cr_"):
    body = params[params.index("static struct %s %s[] = {" % (table[:-1], table)):]
    tokens += re.findall(r'"([^"]*)"', body[:body.index("};")])
assert len(tokens) == 10 and all(t.isalnum() for t in tokens), tokens
parse_attr = helpers.function(params, "hdmi_parse_attr")
assert parse_attr.count("strstr(name, parse_c") == 3 and "strncmp" not in parse_attr
assert main.count("hdmitx_hdr_attr_same(b->attr, hdev->fmt_attr)") == 2
assert "memcmp(b->attr" not in main
UNTRUST = "\tif (!hdmitx_debug_read_only(tmpbuf))\n\t\thdmitx_hdr_transport_untrusted(hdev);\n"


def check_debug_classification(debug):
    """The debug sysfs path distrusts every command except the interrupt dump."""
    assert debug.count("hdmitx_hdr_transport_untrusted(hdev);") == 1
    assert debug.count(UNTRUST) == 1
    assert debug.index("\ttmpbuf[i] = 0;\n") < debug.index(UNTRUST) < \
        debug.index("if (strncmp(tmpbuf, ")
    dump = debug[debug.index('strncmp(tmpbuf, "dumpintr", 8) == 0) {'):]
    assert dump[dump.index("{") + 1:dump.index("}")].split() == ["hdmitx_dump_intr();"]
    # No earlier command branch captures the dump name before its branch.
    for prefix in re.findall(r'strncmp\(tmpbuf, "([^"]+)", (\d+)\)',
                             debug[:debug.index('"dumpintr", 8')]):
        assert not "dumpintr".startswith(prefix[0][:int(prefix[1])]), prefix


debug_function = helpers.function(hw, "hdmitx_debug")
check_debug_classification(debug_function)
assert main.count('hwop.debugfun(hdev, "dumpintr")') == 1
dump_dump = helpers.function(hw, "hdmitx_dump_intr")
assert "hdmitx_wr_reg" not in dump_dump and "hd_write_reg" not in dump_dump
debug_program = PRELUDE + helpers.function(hw, "hdmitx_debug_read_only") + r"""
int main(int argc, char **argv) {
 (void)argc; (void)argv;
 assert(hdmitx_debug_read_only("dumpintr"));
 static const char *mutating[] = {"", "dump", "dumpint", "testhpll16", "bist",
  "testedid", "stop_vsif", "hdcp_mode1", "avmute_frame5", "hpd_lock1", "ss"};
 for (unsigned int i = 0; i < sizeof(mutating) / sizeof(*mutating); ++i)
  assert(!hdmitx_debug_read_only(mutating[i]));
 return 0;
}
"""
with tempfile.TemporaryDirectory(prefix="hdr-native-bypass-") as directory:
    directory = Path(directory)
    compile_run(core, range(45), directory, "lifetime")
    print("PASS: 45 actual DRM/worker lifetime, ownership, transport, modeset, attr and callback cases")
    compile_run(producer, range(17), directory, "producer")
    print("PASS: actual producer/adapter accepts only plain HDR10 across 17 cases")
    guard = module[module.index("#if defined(CONFIG_AMLOGIC_HDMITX) &&"):
                   module.index("#if defined(CONFIG_DRM_MESON_HDMI)\nstatic inline")]
    configurations = (
        ("native", ("CONFIG_AMLOGIC_HDMITX", "CONFIG_AMLOGIC_MEDIA_ENHANCEMENT_VECM"), True),
        ("no-hdmi", ("CONFIG_AMLOGIC_MEDIA_ENHANCEMENT_VECM",), False),
        ("no-vecm", ("CONFIG_AMLOGIC_HDMITX",), False),
        ("neither", (), False),
        ("drm-y", ("CONFIG_AMLOGIC_HDMITX", "CONFIG_AMLOGIC_MEDIA_ENHANCEMENT_VECM",
                   "CONFIG_DRM_MESON_HDMI"), False),
        ("drm-m", ("CONFIG_AMLOGIC_HDMITX", "CONFIG_AMLOGIC_MEDIA_ENHANCEMENT_VECM",
                   "CONFIG_DRM_MESON_HDMI_MODULE"), False),
    )
    for name, flags, native in configurations:
        program = PRELUDE + helpers.declaration(module, "enum", "hdmitx_hdr_origin")
        program += "struct vout_device_s; struct master_display_info_s;\n"
        program += "".join("#define " + flag + " 1\n" for flag in flags) + guard
        if native:
            program += "\n#ifndef HDMITX_NATIVE_HDR_PROVENANCE\n#error missing native adapter\n#endif\n"
            program += "int main(int argc,char **argv) { (void)argc; (void)argv; return 0; }\n"
        else:
            program += "\n#ifdef HDMITX_NATIVE_HDR_PROVENANCE\n#error unsafe adapter\n#endif\n"
            program += "int main(int argc,char **argv) { (void)argc; (void)argv; assert(!hdmitx_hdr_packet_from(NULL,NULL,HDMITX_HDR_ORIGIN_PLAIN_HDR10)); return 0; }\n"
        compile_run(program, [0], directory, name)
    print("PASS: actual header selects native or link-free legacy fallback in six configs")
    compile_run(debug_program, [0], directory, "debug-read-only")
    print("PASS: internal interrupt dump keeps native HDR transport trusted; other debug commands distrust")
    if args.negative_controls:
        no_replay = core.replace(
            "if (!hdmitx_queue_hdr(hdev, false))",
            "if (false)", 1)
        compile_run(no_replay, [1], directory, "no-replay", True)
        fallthrough = core.replace("\t\tgoto complete;", "\t\tif (!zero) goto complete;", 1)
        compile_run(fallthrough, [0], directory, "zero-fallthrough", True)
        entry_decrement = core.replace(
            "\tassertion = b->assertions;",
            "\tif (b->outstanding) --b->outstanding;\n\tassertion = b->assertions;")
        compile_run(entry_decrement, [2], directory, "entry-decrement", True)
        untagged_dynamic = producer.replace(
            "origin == HDMITX_HDR_ORIGIN_PLAIN_HDR10) ++tagged",
            "origin == HDMITX_HDR_ORIGIN_PLAIN_HDR10 || data->features == 0x3509100a) ++tagged")
        compile_run(untagged_dynamic, [1, 2], directory, "raw-pq-guard", True)
        fixed = ("\t\tb->applied_gen = gen;\n\t\tb->applied_link = link;\n",
                 "\t\tb->applied_valid = gen == atomic_read(&b->transport_gen) &&\n"
                 "\t\t\tlink == atomic_read(&b->link_gen) &&\n"
                 "\t\t\t!READ_ONCE(b->untrusted);\n")
        assert all(core.count(part) == 1 for part in fixed)
        retag = core.replace(fixed[0],
            "\t\tb->applied_gen = atomic_read(&b->transport_gen);\n"
            "\t\tb->applied_link = atomic_read(&b->link_gen);\n").replace(
            fixed[1], "\t\tb->applied_valid = true;\n").replace(
            "\tgen = atomic_read(&b->transport_gen);\n", "\t(void)gen;\n", 1)
        # v3 commit: an edge after the initial link check is retagged as current.
        compile_run(retag, [34, 35], directory, "commit-retag", True)
        adopt = "\t\treturn before + 1;\n\treturn link;\n"
        assert core.count(adopt) == 1
        # v5: the modeset's own PHY edge invalidated every tracked commit.
        compile_run(core.replace(adopt, "\t\treturn link;\n\treturn link;\n"),
                    [39], directory, "self-phy-edge", True)
        # Adopting the current generation would hide HPD edges around the PHY op.
        compile_run(core.replace(adopt, "\t\treturn before + 1;\n"
                                 "\treturn atomic_read(&hdev->hdr_bypass.link_gen);\n"),
                    [40, 41], directory, "adopt-current-link", True)
        compile_run(core.replace("\tif (before == link && before != INT_MAX &&",
                                 "\tif (before != INT_MAX &&"),
                    [40], directory, "adopt-prior-hpd", True)
        print("PASS: v5 self-PHY-edge and HPD-hiding modeset link controls fail")
        same = "\treturn la == lb && !memcmp(a, b, la);\n"
        assert core.count(same) == 1
        # v7: raw 16-byte memcmp rejected benign store_attr rewrites.
        compile_run(core.replace(same, "\treturn !memcmp(a, b, 16);\n"),
                    [43], directory, "raw-attr-memcmp", True)
        compile_run(core.replace(same, "\treturn !memcmp(a, b, la < lb ? la : lb);\n"),
                    [44], directory, "attr-prefix", True)
        compile_run(core.replace("\treturn c == ' ' ||", "\treturn c != 0 ||"),
                    [44], directory, "attr-overtrim", True)
        lead = "\twhile (la && hdmitx_hdr_attr_sep(*a))\n\t\ta++, la--;\n"
        assert core.count(lead) == 1
        compile_run(core.replace(lead, ""), [43], directory, "attr-leading", True)
        print("PASS: raw-attr-memcmp, attr-prefix, attr-overtrim and attr-leading controls fail")
        print("PASS: replay/fallthrough/lifetime/commit-retag and HDR10+/CUVA raw-PQ controls fail")
        v4_debug = debug_function.replace(UNTRUST, "").replace(
            "\tunsigned long value = 0;\n",
            "\tunsigned long value = 0;\n\n\thdmitx_hdr_transport_untrusted(hdev);\n", 1)
        try:
            check_debug_classification(v4_debug)
        except AssertionError:
            pass
        else:
            raise AssertionError("v4 unconditional debug distrust survived")
        all_debug = debug_program.replace(
            'return strncmp(cmd, "dumpintr", 8) == 0;', "return false;")
        assert all_debug != debug_program
        compile_run(all_debug, [0], directory, "debug-all-mutating", True)
        widened = debug_program.replace(
            'return strncmp(cmd, "dumpintr", 8) == 0;', 'return strncmp(cmd, "dump", 4) == 0;')
        assert widened != debug_program
        compile_run(widened, [0], directory, "debug-widened", True)
        print("PASS: v4 boot-time distrust, all-mutating and widened read-only debug controls fail")
