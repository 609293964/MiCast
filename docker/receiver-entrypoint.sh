#!/bin/sh
set -eu

name="${MICAST_AIRPLAY_NAME:-MiCast}"
protocol="${MICAST_AIRPLAY_PROTOCOL:-auto}"
pcm_port="${MICAST_PCM_PORT:-9001}"

case "$protocol" in
  auto|classic|airplay2) ;;
  *) echo "Unsupported AirPlay protocol: $protocol" >&2; exit 2 ;;
esac

cat >/etc/shairport-sync.conf <<EOF
general = {
  name = "$name";
  interface = "eth0";
  output_backend = "stdout";
  service_type = "$protocol";
  ignore_volume_control = "yes";
  run_this_when_volume_is_set = "/app/scripts/receiver-volume.sh ";
  log_verbosity = 1;
};
sessioncontrol = {
  allow_session_interruption = "yes";
  run_this_before_play_begins = "/app/scripts/receiver-session-start.sh";
  run_this_after_play_ends = "/app/scripts/receiver-session-stop.sh";
};
stdout = {
  output_rate = 48000;
  output_format = "S16_LE";
  output_channels = 2;
};
EOF

rm -f /run/dbus/dbus.pid /run/avahi-daemon/pid
dbus-uuidgen --ensure
dbus-daemon --system
avahi-daemon --daemonize --no-chroot

if [ "$protocol" != "classic" ]; then
  /usr/local/bin/nqptp >/dev/null 2>&1 &
fi

# Keep the PCM listener alive across MiCast restarts and short network drops.
# Without `fork`, socat exits when its first client disconnects while
# Shairport Sync keeps advertising the AirPlay service, leaving a visible but
# unusable receiver behind.
exec sh -c "/usr/local/bin/shairport-sync -c /etc/shairport-sync.conf | socat -u - TCP-LISTEN:${pcm_port},fork,reuseaddr,keepalive"
