#!/usr/bin/env bash
# OCI recreates device nodes after Docker's --tmpfs mounts; hide them afterward.
set -euo pipefail

directories=(/dev/snd /dev/input /dev/bus/usb)
case ${MT5_TEST_GPU:-0} in
	0) directories+=(/dev/dri) ;;
	1) ;;
	*) printf 'MT5_TEST_GPU must be 0 or 1\n' >&2; exit 2 ;;
esac
for directory in "${directories[@]}"; do
	mkdir -p "$directory"
	mount -t tmpfs -o nosuid,nodev,mode=755 tmpfs "$directory"
done
for device in /dev/hidraw*; do
	[[ -c $device ]] || continue
	mount --bind /dev/null "$device"
done
if [[ -f /run/mt5-test/ChartInput.ex5 ]]; then
	mql5=/home/plasma/.wine/drive_c/MT5/MQL5
	fixture_gid=$(id -g plasma)
	install -d -o plasma -g "$fixture_gid" "$mql5" "$mql5/Experts" "$mql5/Files"
	install -m 0644 -o plasma -g "$fixture_gid" /run/mt5-test/ChartInput.ex5 "$mql5/Experts/ChartInput.ex5"
fi
# Docker's --entrypoint clears CMD; preserve these images' normal init defaults.
if (($# == 0)); then set -- /usr/sbin/init --system; fi
exec /init.d/init.sh "$@"
