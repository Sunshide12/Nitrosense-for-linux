// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Copyright (C) 2026 Steban Martínez Gutiérrez (Sunshide.com)
 *
 * nitrosense.c - Acer Nitro gaming WMI driver (fans, operating mode, RGB keyboard)
 *
 * Linux port of the hardware layer of Acer NitroSense 3.1 for the Nitro 5
 * AN515-57. The firmware interface was reverse engineered from:
 *
 *   - NitroSense.exe (.NET UI): how every request word is built
 *   - PSSvc.exe (native service): which WMI method each request goes to
 *   - the ACPI tables (SSDB, device \_SB.PC00.WMID): WMBH and WMAA methods
 *
 * Two WMI blocks are used:
 *
 *   7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56  AcerGamingFunction  (ACPI WMBH)
 *   61EF69EA-865C-4BC3-A502-A0DEBA0CB531  APGeAction          (ACPI WMAA)
 *
 * Every request is a little-endian integer passed as a raw buffer; every reply
 * carries a status byte in bits 0-7 (0 = success) followed by the payload.
 */

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/acpi.h>
#include <linux/device.h>
#include <linux/hwmon.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/sysfs.h>
#include <linux/wmi.h>

#define NS_GAMING_GUID		"7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56"
#define NS_GENERIC_GUID		"61EF69EA-865C-4BC3-A502-A0DEBA0CB531"

/* AcerGamingFunction method ids (WmiMethodId qualifiers of the MOF) */
enum ns_gaming_method {
	NS_SET_GAMING_PROFILE		= 1,
	NS_GET_GAMING_PROFILE		= 3,
	NS_GET_GAMING_SYS_INFO		= 5,
	NS_SET_GAMING_RGB_KB		= 6,
	NS_GET_GAMING_RGB_KB		= 7,
	NS_SET_GAMING_FAN_BEHAVIOR	= 14,
	NS_SET_GAMING_FAN_SPEED		= 16,
	NS_GET_GAMING_FAN_SPEED		= 17,
	NS_SET_GAMING_FAN_TABLE		= 18,
	NS_SET_GAMING_KB_BACKLIGHT	= 20,
	NS_GET_GAMING_KB_BACKLIGHT	= 21,
	NS_SET_GAMING_MISC_SETTING	= 22,
	NS_GET_GAMING_MISC_SETTING	= 23,
};

/* APGeAction method ids */
enum ns_generic_method {
	NS_SET_FUNCTION			= 1,
	NS_GET_FUNCTION			= 2,
};

/* GetGamingSysInfo sensor ids (EC registers read by GGSI) */
enum ns_sensor {
	NS_SENSOR_CPU_TEMP		= 0x01,	/* CTML */
	NS_SENSOR_CPU_FAN		= 0x02,	/* C1FR */
	NS_SENSOR_SYS_TEMP		= 0x03,	/* SKTB */
	NS_SENSOR_GPU_FAN		= 0x06,	/* G1FR */
	NS_SENSOR_GPU_TEMP		= 0x0A,	/* SKTC */
};

/* Get/SetGamingMiscSetting indexes */
#define NS_MISC_SUPPORTED_MODES		0x0A
#define NS_MISC_OPERATING_MODE		0x0B

/* Operating modes as stored in the EC (OPMS); NitroSense OPMode_Type */
enum ns_opmode {
	NS_MODE_QUIET		= 0,
	NS_MODE_DEFAULT		= 1,
	NS_MODE_EXTREME		= 4,
	NS_MODE_TURBO		= 5,
	NS_MODE_MAX		= 8,
};

static const char * const ns_mode_names[NS_MODE_MAX] = {
	[NS_MODE_QUIET]		= "quiet",
	[NS_MODE_DEFAULT]	= "default",
	[NS_MODE_EXTREME]	= "extreme",
	[NS_MODE_TURBO]		= "turbo",
};

/* Fan groups: bit in the behavior mask, shift of its 2-bit mode, speed index */
struct ns_fan {
	u8 mask;
	u8 mode_shift;
	u8 speed_index;
	u8 rpm_sensor;
	const char *label;
};

static const struct ns_fan ns_fans[] = {
	{ .mask = 0x01, .mode_shift = 16, .speed_index = 1, .rpm_sensor = NS_SENSOR_CPU_FAN, .label = "CPU" },
	{ .mask = 0x08, .mode_shift = 22, .speed_index = 4, .rpm_sensor = NS_SENSOR_GPU_FAN, .label = "GPU" },
};

/* Fan behavior values written in each fan's 2-bit field */
enum ns_fan_behavior {
	NS_FAN_AUTO	= 1,
	NS_FAN_MAX	= 2,
	NS_FAN_CUSTOM	= 3,
};

/* Keyboard backlight effects (SetGamingKBBacklight byte 0) */
#define NS_KB_EFFECT_WAVE	3
#define NS_KB_WAVE_FLAG		0x08

static unsigned int bk_hotkey = 132;
module_param(bk_hotkey, uint, 0444);
MODULE_PARM_DESC(bk_hotkey, "Keyboard backlight hotkey number (NitroSense BK_Hotkey_Number, default 132)");

/* One lock for every firmware call: both WMI blocks end up in the same EC/SMI path */
static DEFINE_MUTEX(ns_lock);

struct ns_gaming {
	struct wmi_device *wdev;
	u8 fan_behavior[ARRAY_SIZE(ns_fans)];
	u8 fan_speed[ARRAY_SIZE(ns_fans)];	/* percent requested for custom mode */
	u8 supported_modes;
	int win_key;				/* -1 until written: firmware has no getter */
};

/* --------------------------------------------------------------------------
 * Firmware call helpers
 */

static int ns_call(struct wmi_device *wdev, u32 method, const u8 *in, size_t in_len,
		   u8 *out, size_t out_len)
{
	struct acpi_buffer input = { .length = in_len, .pointer = (void *)in };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	union acpi_object *obj;
	acpi_status status;
	int ret = 0;
	size_t i;

	lockdep_assert_held(&ns_lock);

	status = wmidev_evaluate_method(wdev, 0, method, &input, &output);
	if (ACPI_FAILURE(status)) {
		dev_dbg(&wdev->dev, "method %u failed: %s\n", method, acpi_format_exception(status));
		return -EIO;
	}

	memset(out, 0, out_len);
	obj = output.pointer;
	if (!obj)
		return -ENODATA;

	switch (obj->type) {
	case ACPI_TYPE_BUFFER:
		memcpy(out, obj->buffer.pointer, min_t(size_t, obj->buffer.length, out_len));
		break;
	case ACPI_TYPE_INTEGER:
		for (i = 0; i < out_len && i < sizeof(u64); i++)
			out[i] = obj->integer.value >> (8 * i);
		break;
	default:
		ret = -EPROTO;
	}

	kfree(obj);
	return ret;
}

/*
 * Call a method taking a 32 or 64 bit integer and returning one.
 * Windows WMI marshals the parameter as a raw little-endian buffer of the
 * MOF type size, so in_len must be 4 (uint32 parameter) or 8 (uint64).
 */
static int ns_call_int(struct wmi_device *wdev, u32 method, u64 arg, size_t in_len, u64 *result)
{
	u8 in[8], out[8];
	u64 val = 0;
	int ret, i;

	for (i = 0; i < 8; i++)
		in[i] = arg >> (8 * i);

	ret = ns_call(wdev, method, in, in_len, out, sizeof(out));
	if (ret)
		return ret;

	for (i = 0; i < 8; i++)
		val |= (u64)out[i] << (8 * i);

	dev_dbg(&wdev->dev, "method %u in 0x%llx out 0x%llx\n", method, arg, val);

	/* Low byte is the firmware status, 0 means success */
	if (val & 0xff)
		return -EIO;

	if (result)
		*result = val;
	return 0;
}

static int ns_get_sensor(struct ns_gaming *ns, u8 sensor, long *value)
{
	u64 out;
	int ret;

	mutex_lock(&ns_lock);
	ret = ns_call_int(ns->wdev, NS_GET_GAMING_SYS_INFO, 0x01 | (sensor << 8), 4, &out);
	mutex_unlock(&ns_lock);
	if (ret)
		return ret;

	*value = (out >> 8) & 0xffff;
	return 0;
}

static int ns_get_misc(struct ns_gaming *ns, u8 index, u8 *value)
{
	u64 out;
	int ret;

	ret = ns_call_int(ns->wdev, NS_GET_GAMING_MISC_SETTING, index, 4, &out);
	if (!ret)
		*value = (out >> 8) & 0xff;
	return ret;
}

/* --------------------------------------------------------------------------
 * Fans
 */

static int ns_set_fan_behavior(struct ns_gaming *ns, int fan, u8 behavior)
{
	const struct ns_fan *f = &ns_fans[fan];
	int ret;

	lockdep_assert_held(&ns_lock);

	ret = ns_call_int(ns->wdev, NS_SET_GAMING_FAN_BEHAVIOR,
			  f->mask | ((u64)behavior << f->mode_shift), 8, NULL);
	if (ret)
		return ret;
	ns->fan_behavior[fan] = behavior;

	/* NitroSense always follows "custom" with the requested speed */
	if (behavior == NS_FAN_CUSTOM)
		ret = ns_call_int(ns->wdev, NS_SET_GAMING_FAN_SPEED,
				  f->speed_index | (ns->fan_speed[fan] << 8), 8, NULL);
	return ret;
}

static int ns_set_fan_speed(struct ns_gaming *ns, int fan, u8 percent)
{
	int ret = 0;

	lockdep_assert_held(&ns_lock);

	if (ns->fan_behavior[fan] == NS_FAN_CUSTOM)
		ret = ns_call_int(ns->wdev, NS_SET_GAMING_FAN_SPEED,
				  ns_fans[fan].speed_index | (percent << 8), 8, NULL);
	if (!ret)
		ns->fan_speed[fan] = percent;
	return ret;
}

/* --------------------------------------------------------------------------
 * hwmon: temperatures, fan RPM and standard pwm control
 *
 * pwmN_enable follows the hwmon ABI: 0 = full speed, 1 = manual, 2 = automatic
 */

static const u8 ns_temp_sensors[] = { NS_SENSOR_CPU_TEMP, NS_SENSOR_GPU_TEMP, NS_SENSOR_SYS_TEMP };
static const char * const ns_temp_labels[] = { "CPU", "GPU", "System" };

static umode_t ns_hwmon_is_visible(const void *data, enum hwmon_sensor_types type,
				   u32 attr, int channel)
{
	if (type == hwmon_pwm)
		return 0644;
	return 0444;
}

static int ns_hwmon_read(struct device *dev, enum hwmon_sensor_types type, u32 attr,
			 int channel, long *val)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	int ret;

	switch (type) {
	case hwmon_temp:
		ret = ns_get_sensor(ns, ns_temp_sensors[channel], val);
		if (!ret)
			*val *= 1000;
		return ret;
	case hwmon_fan:
		return ns_get_sensor(ns, ns_fans[channel].rpm_sensor, val);
	case hwmon_pwm:
		mutex_lock(&ns_lock);
		if (attr == hwmon_pwm_enable) {
			switch (ns->fan_behavior[channel]) {
			case NS_FAN_MAX:
				*val = 0;
				break;
			case NS_FAN_CUSTOM:
				*val = 1;
				break;
			default:
				*val = 2;
			}
		} else {
			*val = DIV_ROUND_CLOSEST(ns->fan_speed[channel] * 255, 100);
		}
		mutex_unlock(&ns_lock);
		return 0;
	default:
		return -EOPNOTSUPP;
	}
}

static int ns_hwmon_read_string(struct device *dev, enum hwmon_sensor_types type, u32 attr,
				int channel, const char **str)
{
	switch (type) {
	case hwmon_temp:
		*str = ns_temp_labels[channel];
		return 0;
	case hwmon_fan:
		*str = ns_fans[channel].label;
		return 0;
	default:
		return -EOPNOTSUPP;
	}
}

static int ns_hwmon_write(struct device *dev, enum hwmon_sensor_types type, u32 attr,
			  int channel, long val)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	static const u8 enable_to_behavior[] = { NS_FAN_MAX, NS_FAN_CUSTOM, NS_FAN_AUTO };
	int ret;

	if (type != hwmon_pwm)
		return -EOPNOTSUPP;

	mutex_lock(&ns_lock);
	if (attr == hwmon_pwm_enable) {
		if (val < 0 || val > 2)
			ret = -EINVAL;
		else
			ret = ns_set_fan_behavior(ns, channel, enable_to_behavior[val]);
	} else {
		if (val < 0 || val > 255)
			ret = -EINVAL;
		else
			ret = ns_set_fan_speed(ns, channel, DIV_ROUND_CLOSEST(val * 100, 255));
	}
	mutex_unlock(&ns_lock);
	return ret;
}

static const struct hwmon_channel_info * const ns_hwmon_info[] = {
	HWMON_CHANNEL_INFO(temp,
			   HWMON_T_INPUT | HWMON_T_LABEL,
			   HWMON_T_INPUT | HWMON_T_LABEL,
			   HWMON_T_INPUT | HWMON_T_LABEL),
	HWMON_CHANNEL_INFO(fan,
			   HWMON_F_INPUT | HWMON_F_LABEL,
			   HWMON_F_INPUT | HWMON_F_LABEL),
	HWMON_CHANNEL_INFO(pwm,
			   HWMON_PWM_INPUT | HWMON_PWM_ENABLE,
			   HWMON_PWM_INPUT | HWMON_PWM_ENABLE),
	NULL
};

static const struct hwmon_ops ns_hwmon_ops = {
	.is_visible = ns_hwmon_is_visible,
	.read = ns_hwmon_read,
	.read_string = ns_hwmon_read_string,
	.write = ns_hwmon_write,
};

static const struct hwmon_chip_info ns_hwmon_chip = {
	.ops = &ns_hwmon_ops,
	.info = ns_hwmon_info,
};

/* --------------------------------------------------------------------------
 * Gaming sysfs attributes
 */

static int ns_parse_mode(const char *buf)
{
	unsigned int num;
	int i;

	for (i = 0; i < NS_MODE_MAX; i++)
		if (ns_mode_names[i] && sysfs_streq(buf, ns_mode_names[i]))
			return i;
	if (!kstrtouint(buf, 0, &num) && num < NS_MODE_MAX && ns_mode_names[num])
		return num;
	return -EINVAL;
}

static ssize_t operating_mode_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	u8 mode;
	int ret;

	mutex_lock(&ns_lock);
	ret = ns_get_misc(ns, NS_MISC_OPERATING_MODE, &mode);
	mutex_unlock(&ns_lock);
	if (ret)
		return ret;

	if (mode < NS_MODE_MAX && ns_mode_names[mode])
		return sysfs_emit(buf, "%s\n", ns_mode_names[mode]);
	return sysfs_emit(buf, "%u\n", mode);
}

static ssize_t operating_mode_store(struct device *dev, struct device_attribute *attr,
				    const char *buf, size_t count)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	int mode = ns_parse_mode(buf);
	u64 table;
	int ret;

	if (mode < 0)
		return mode;
	if (!(ns->supported_modes & BIT(mode)))
		return -EOPNOTSUPP;

	/*
	 * Same sequence as PSSvc set_operation_mode(): store the mode in the EC,
	 * then select the matching EC fan table (quiet 1, extreme 3, others 2).
	 */
	table = mode == NS_MODE_QUIET ? 1 : mode == NS_MODE_EXTREME ? 3 : 2;

	mutex_lock(&ns_lock);
	ret = ns_call_int(ns->wdev, NS_SET_GAMING_MISC_SETTING,
			  NS_MISC_OPERATING_MODE | (mode << 8), 8, NULL);
	if (!ret)
		ret = ns_call_int(ns->wdev, NS_SET_GAMING_FAN_TABLE, table, 8, NULL);
	mutex_unlock(&ns_lock);

	return ret ?: count;
}
static DEVICE_ATTR_RW(operating_mode);

static ssize_t operating_mode_choices_show(struct device *dev, struct device_attribute *attr,
					   char *buf)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	ssize_t len = 0;
	int i;

	for (i = 0; i < NS_MODE_MAX; i++)
		if (ns_mode_names[i] && (ns->supported_modes & BIT(i)))
			len += sysfs_emit_at(buf, len, "%s%s", len ? " " : "", ns_mode_names[i]);
	len += sysfs_emit_at(buf, len, "\n");
	return len;
}
static DEVICE_ATTR_RO(operating_mode_choices);

/*
 * kb_backlight: "<effect> <speed> <brightness> <direction> <RRGGBB>"
 *   effect     0 static, 1 breathing, 2 neon, 3 wave, 4 shifting, 5 zoom
 *   speed      0-9, brightness 0-100, direction 1 right-to-left, 2 left-to-right
 */
static ssize_t kb_backlight_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	u8 in[4] = { 0 }, out[16];
	int ret;

	mutex_lock(&ns_lock);
	ret = ns_call(ns->wdev, NS_GET_GAMING_KB_BACKLIGHT, in, sizeof(in), out, sizeof(out));
	mutex_unlock(&ns_lock);
	if (ret)
		return ret;
	if (out[0])
		return -EIO;

	return sysfs_emit(buf, "%u %u %u %u %02x%02x%02x\n",
			  out[1], out[2], out[3], out[5], out[6], out[7], out[8]);
}

static ssize_t kb_backlight_store(struct device *dev, struct device_attribute *attr,
				  const char *buf, size_t count)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	unsigned int effect, speed, brightness, direction, rgb;
	u8 in[16], out[4];
	int ret, i;

	if (sscanf(buf, "%u %u %u %u %x", &effect, &speed, &brightness, &direction, &rgb) != 5)
		return -EINVAL;
	if (effect > 5 || speed > 9 || brightness > 100 || direction > 6 || rgb > 0xffffff)
		return -EINVAL;

	in[0] = effect;
	in[1] = speed;
	in[2] = brightness;
	in[3] = effect == NS_KB_EFFECT_WAVE ? NS_KB_WAVE_FLAG : 0;
	in[4] = direction;
	in[5] = rgb >> 16;
	in[6] = rgb >> 8;
	in[7] = rgb;
	/* PSSvc fills the 16-byte array by shifting the 64-bit word modulo 64 */
	for (i = 8; i < 16; i++)
		in[i] = in[i - 8];
	in[9] = 1;

	mutex_lock(&ns_lock);
	ret = ns_call(ns->wdev, NS_SET_GAMING_KB_BACKLIGHT, in, sizeof(in), out, sizeof(out));
	mutex_unlock(&ns_lock);
	if (ret)
		return ret;

	return out[0] ? -EIO : count;
}
static DEVICE_ATTR_RW(kb_backlight);

/* kb_zoneN: static colour of keyboard zone N as RRGGBB */
static ssize_t kb_zone_show(struct device *dev, int zone, char *buf)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	u64 out;
	int ret;

	mutex_lock(&ns_lock);
	ret = ns_call_int(ns->wdev, NS_GET_GAMING_RGB_KB, BIT(zone), 4, &out);
	mutex_unlock(&ns_lock);
	if (ret)
		return ret;

	return sysfs_emit(buf, "%02x%02x%02x\n", (u8)(out >> 8), (u8)(out >> 16), (u8)(out >> 24));
}

static ssize_t kb_zone_store(struct device *dev, int zone, const char *buf, size_t count)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	unsigned int rgb;
	u64 arg;
	int ret;

	ret = kstrtouint(buf, 16, &rgb);
	if (ret)
		return ret;
	if (rgb > 0xffffff)
		return -EINVAL;

	arg = BIT(zone) | ((rgb >> 16) & 0xff) << 8 | ((rgb >> 8) & 0xff) << 16 |
	      (u64)(rgb & 0xff) << 24;

	mutex_lock(&ns_lock);
	ret = ns_call_int(ns->wdev, NS_SET_GAMING_RGB_KB, arg, 4, NULL);
	mutex_unlock(&ns_lock);

	return ret ?: count;
}

#define NS_KB_ZONE_ATTR(n)							\
static ssize_t kb_zone##n##_show(struct device *dev,				\
				 struct device_attribute *attr, char *buf)	\
{										\
	return kb_zone_show(dev, (n) - 1, buf);					\
}										\
static ssize_t kb_zone##n##_store(struct device *dev,				\
				  struct device_attribute *attr,		\
				  const char *buf, size_t count)		\
{										\
	return kb_zone_store(dev, (n) - 1, buf, count);				\
}										\
static DEVICE_ATTR_RW(kb_zone##n)

NS_KB_ZONE_ATTR(1);
NS_KB_ZONE_ATTR(2);
NS_KB_ZONE_ATTR(3);
NS_KB_ZONE_ATTR(4);

/* win_key: 1 = Windows/Menu keys enabled, 0 = locked (gaming mode) */
static ssize_t win_key_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);

	if (ns->win_key < 0)
		return sysfs_emit(buf, "unknown\n");
	return sysfs_emit(buf, "%d\n", ns->win_key);
}

static ssize_t win_key_store(struct device *dev, struct device_attribute *attr,
			     const char *buf, size_t count)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	bool enable;
	int ret;

	ret = kstrtobool(buf, &enable);
	if (ret)
		return ret;

	mutex_lock(&ns_lock);
	ret = ns_call_int(ns->wdev, NS_SET_GAMING_PROFILE, 0x02 | ((u64)enable << 24), 8, NULL);
	if (!ret)
		ns->win_key = enable;
	mutex_unlock(&ns_lock);

	return ret ?: count;
}
static DEVICE_ATTR_RW(win_key);

/* lcd_overdrive: panel overdrive on/off */
static ssize_t lcd_overdrive_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	u64 out;
	int ret;

	mutex_lock(&ns_lock);
	ret = ns_call_int(ns->wdev, NS_GET_GAMING_PROFILE, 0, 4, &out);
	mutex_unlock(&ns_lock);
	if (ret)
		return ret;

	return sysfs_emit(buf, "%d\n", ((out >> 48) & 0xff) == 1);
}

static ssize_t lcd_overdrive_store(struct device *dev, struct device_attribute *attr,
				   const char *buf, size_t count)
{
	struct ns_gaming *ns = dev_get_drvdata(dev);
	bool enable;
	int ret;

	ret = kstrtobool(buf, &enable);
	if (ret)
		return ret;

	mutex_lock(&ns_lock);
	ret = ns_call_int(ns->wdev, NS_SET_GAMING_PROFILE, 0x10 | ((u64)enable << 48), 8, NULL);
	mutex_unlock(&ns_lock);

	return ret ?: count;
}
static DEVICE_ATTR_RW(lcd_overdrive);

static struct attribute *ns_gaming_attrs[] = {
	&dev_attr_operating_mode.attr,
	&dev_attr_operating_mode_choices.attr,
	&dev_attr_kb_backlight.attr,
	&dev_attr_kb_zone1.attr,
	&dev_attr_kb_zone2.attr,
	&dev_attr_kb_zone3.attr,
	&dev_attr_kb_zone4.attr,
	&dev_attr_win_key.attr,
	&dev_attr_lcd_overdrive.attr,
	NULL
};
ATTRIBUTE_GROUPS(ns_gaming);

static int ns_gaming_probe(struct wmi_device *wdev, const void *context)
{
	struct ns_gaming *ns;
	struct device *hwmon;
	u8 modes = 0;
	int i, ret;

	ns = devm_kzalloc(&wdev->dev, sizeof(*ns), GFP_KERNEL);
	if (!ns)
		return -ENOMEM;

	ns->wdev = wdev;
	ns->win_key = -1;
	for (i = 0; i < ARRAY_SIZE(ns_fans); i++) {
		ns->fan_behavior[i] = NS_FAN_AUTO;
		ns->fan_speed[i] = 50;
	}

	mutex_lock(&ns_lock);
	ret = ns_get_misc(ns, NS_MISC_SUPPORTED_MODES, &modes);
	mutex_unlock(&ns_lock);
	if (ret || !modes) {
		dev_warn(&wdev->dev, "cannot read supported operating modes, assuming quiet/default/extreme\n");
		modes = BIT(NS_MODE_QUIET) | BIT(NS_MODE_DEFAULT) | BIT(NS_MODE_EXTREME);
	}
	ns->supported_modes = modes;

	dev_set_drvdata(&wdev->dev, ns);

	hwmon = devm_hwmon_device_register_with_info(&wdev->dev, "nitrosense", ns,
						     &ns_hwmon_chip, NULL);
	if (IS_ERR(hwmon))
		return PTR_ERR(hwmon);

	dev_info(&wdev->dev, "Acer gaming interface ready (modes 0x%02x)\n", modes);
	return 0;
}

/* --------------------------------------------------------------------------
 * APGeAction: CoolBoost and keyboard backlight timeout
 */

static ssize_t coolboost_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct wmi_device *wdev = to_wmi_device(dev);
	u64 out;
	int ret;

	mutex_lock(&ns_lock);
	ret = ns_call_int(wdev, NS_GET_FUNCTION, 0x0207, 4, &out);
	mutex_unlock(&ns_lock);
	if (ret)
		return ret;

	return sysfs_emit(buf, "%d\n", ((out >> 8) & 0xff) == 1);
}

static ssize_t coolboost_store(struct device *dev, struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct wmi_device *wdev = to_wmi_device(dev);
	bool enable;
	int ret;

	ret = kstrtobool(buf, &enable);
	if (ret)
		return ret;

	mutex_lock(&ns_lock);
	ret = ns_call_int(wdev, NS_SET_FUNCTION, 0x07 | ((u64)enable << 16), 8, NULL);
	mutex_unlock(&ns_lock);

	return ret ?: count;
}
static DEVICE_ATTR_RW(coolboost);

/* Keyboard backlight state word: brightness in bits 32-39, timeout (s) in 40-47 */
static int ns_get_kb_state(struct wmi_device *wdev, u64 *out)
{
	return ns_call_int(wdev, NS_GET_FUNCTION, 0x01 | (bk_hotkey & 0xff) << 8 | 0x80000, 4, out);
}

static ssize_t kb_timeout_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct wmi_device *wdev = to_wmi_device(dev);
	u64 out;
	int ret;

	mutex_lock(&ns_lock);
	ret = ns_get_kb_state(wdev, &out);
	mutex_unlock(&ns_lock);
	if (ret)
		return ret;

	return sysfs_emit(buf, "%d\n", ((out >> 40) & 0xff) == 30);
}

static ssize_t kb_timeout_store(struct device *dev, struct device_attribute *attr,
				const char *buf, size_t count)
{
	struct wmi_device *wdev = to_wmi_device(dev);
	u64 state, arg;
	bool enable;
	int ret;

	ret = kstrtobool(buf, &enable);
	if (ret)
		return ret;

	mutex_lock(&ns_lock);
	ret = ns_get_kb_state(wdev, &state);
	if (!ret) {
		/* Keep the current brightness, switch the 30 s auto-off on or off */
		arg = 0x02 | (bk_hotkey & 0xff) << 8 | 0x80000 | (state & (0xffULL << 32)) |
		      (enable ? 30ULL << 40 : 0);
		ret = ns_call_int(wdev, NS_SET_FUNCTION, arg, 8, NULL);
	}
	mutex_unlock(&ns_lock);

	return ret ?: count;
}
static DEVICE_ATTR_RW(kb_timeout);

static struct attribute *ns_generic_attrs[] = {
	&dev_attr_coolboost.attr,
	&dev_attr_kb_timeout.attr,
	NULL
};
ATTRIBUTE_GROUPS(ns_generic);

static int ns_generic_probe(struct wmi_device *wdev, const void *context)
{
	return 0;
}

/* --------------------------------------------------------------------------
 * Module
 */

static const struct wmi_device_id ns_gaming_id_table[] = {
	{ .guid_string = NS_GAMING_GUID },
	{ }
};

static const struct wmi_device_id ns_generic_id_table[] = {
	{ .guid_string = NS_GENERIC_GUID },
	{ }
};

MODULE_DEVICE_TABLE(wmi, ns_gaming_id_table);
MODULE_DEVICE_TABLE(wmi, ns_generic_id_table);

static struct wmi_driver ns_gaming_driver = {
	.driver = {
		.name = "nitrosense",
		.dev_groups = ns_gaming_groups,
		.probe_type = PROBE_PREFER_ASYNCHRONOUS,
	},
	.id_table = ns_gaming_id_table,
	.probe = ns_gaming_probe,
	.no_singleton = true,
};

static struct wmi_driver ns_generic_driver = {
	.driver = {
		.name = "nitrosense-generic",
		.dev_groups = ns_generic_groups,
		.probe_type = PROBE_PREFER_ASYNCHRONOUS,
	},
	.id_table = ns_generic_id_table,
	.probe = ns_generic_probe,
	.no_singleton = true,
};

static int __init ns_init(void)
{
	int ret;

	ret = wmi_driver_register(&ns_gaming_driver);
	if (ret)
		return ret;

	ret = wmi_driver_register(&ns_generic_driver);
	if (ret)
		wmi_driver_unregister(&ns_gaming_driver);
	return ret;
}

static void __exit ns_exit(void)
{
	wmi_driver_unregister(&ns_generic_driver);
	wmi_driver_unregister(&ns_gaming_driver);
}

module_init(ns_init);
module_exit(ns_exit);

MODULE_AUTHOR("Steban Martínez Gutiérrez (Sunshide.com)");
MODULE_DESCRIPTION("Acer Nitro gaming WMI driver (NitroSense for Linux)");
MODULE_LICENSE("GPL");
MODULE_VERSION("1.0.0");
