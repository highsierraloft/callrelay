"""Render private Asterisk configuration without putting credentials in process arguments."""

import ipaddress
import json
import os
import re
from pathlib import Path


def render(config: dict[str, str], destination: Path) -> None:
    address = str(ipaddress.IPv4Address(config["external_address"]))
    peer = str(ipaddress.IPv4Address(config["phone_address"]))
    for key in ("sip_password", "ari_password", "media_password"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", config[key]):
            raise ValueError("Invalid private service credential format")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = {
        "asterisk.conf": f"""[directories]
astetcdir => {destination}
astmoddir => /usr/lib/asterisk/modules
astvarlibdir => /var/lib/asterisk
astdbdir => /tmp/asterisk
astkeydir => /tmp/asterisk
astdatadir => /var/lib/asterisk
astagidir => /var/lib/asterisk/agi-bin
astspooldir => /var/spool/asterisk
astrundir => /tmp/asterisk
astlogdir => /tmp/asterisk/log
[options]
verbose = 0
debug = 0
nocolor = yes
""",
        "modules.conf": """[modules]
autoload=yes
noload=chan_iax2.so
noload=chan_console.so
noload=chan_motif.so
noload=res_phoneprov.so
""",
        "http.conf": "[general]\nenabled=yes\nbindaddr=0.0.0.0\nbindport=8088\n",
        "ari.conf": f"""[general]
enabled=yes
pretty=no
[agent-call]
type=user
read_only=no
password={config["ari_password"]}
""",
        "manager.conf": "[general]\nenabled=no\n",
        "logger.conf": "[general]\n[logfiles]\nconsole=warning,error\n",
        "cdr.conf": "[general]\nenable=no\n",
        "cel.conf": "[general]\nenable=no\n",
        "rtp.conf": "[general]\nrtpstart=12000\nrtpend=12019\nstrictrtp=yes\n",
        "extensions.conf": "[from-android]\nexten => _.,1,Stasis(agent-call,inbound,${CALLERID(num-pres)})\nsame => n,Hangup(21)\n",
        "acl.conf": f"[phone-only]\ndeny=0.0.0.0/0\npermit={peer}/32\n",
        "pjsip.conf": f"""[global]
type=global
user_agent=AgentCall
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060
external_signaling_address={address}
external_media_address={address}
local_net=10.0.0.0/8
local_net=172.16.0.0/12
[android]
type=endpoint
transport=transport-udp
context=from-android
auth=android-auth
aors=android
identify_by=username
trust_id_inbound=yes
disallow=all
allow=alaw,ulaw
dtmf_mode=rfc4733
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
acl=phone-only
[android-auth]
type=auth
auth_type=userpass
username=android
password={config["sip_password"]}
[android]
type=aor
max_contacts=1
remove_existing=yes
qualify_frequency=15
default_expiration=60
minimum_expiration=30
maximum_expiration=60
""",
        "websocket_client.conf": f"""[agent-call]
type=websocket_client
connection_type=per_call_config
uri=ws://agent-call:8000/phone-media
protocols=media
username=asterisk
password={config["media_password"]}
connection_timeout=5000
reconnect_attempts=0
enable_pingpongs=yes
pingpong_interval=10
pingpong_probes=2
""",
        "chan_websocket.conf": "[general]\ncontrol_message_format=json\n",
    }
    for name, content in files.items():
        target = destination / name
        target.write_text(content)
        target.chmod(0o600)


def main() -> None:
    os.umask(0o077)
    config = json.loads(Path("/run/secrets/asterisk.json").read_text())
    destination = Path("/tmp/asterisk/config")
    (destination.parent / "log").mkdir(parents=True, exist_ok=True)
    Path("/var/spool/asterisk/recording").mkdir(parents=True, exist_ok=True)
    render(config, destination)
    os.execv(
        "/usr/sbin/asterisk", ["asterisk", "-f", "-n", "-C", str(destination / "asterisk.conf")]
    )


if __name__ == "__main__":
    main()
