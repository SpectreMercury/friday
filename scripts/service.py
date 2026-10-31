"""Install/control this user's login service. Secrets stay in .env."""
import argparse
import os
import plistlib
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "local.qwen.telegram"
DOMAIN = f"gui/{os.getuid()}"
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["install", "start", "stop", "restart", "status"])
    action = p.parse_args().action
    if action == "install":
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        (ROOT / "runtime").mkdir(exist_ok=True)
        config = {
            "Label": LABEL,
            "ProgramArguments": ["/usr/bin/caffeinate", "-is", str(ROOT / ".venv/bin/python"), "-m", "local_ai.bot"],
            "WorkingDirectory": str(ROOT),
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONUNBUFFERED": "1"},
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
            "StandardOutPath": str(ROOT / "runtime/bot.log"),
            "StandardErrorPath": str(ROOT / "runtime/bot.log"),
        }
        with PLIST.open("wb") as f:
            plistlib.dump(config, f)
        PLIST.chmod(0o600)
        subprocess.run(["launchctl", "bootout", DOMAIN + "/" + LABEL], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True)
        print("Installed:", PLIST)
    elif action == "start":
        if subprocess.run(["launchctl", "print", DOMAIN + "/" + LABEL], capture_output=True).returncode == 0:
            print("服务已在运行；需要重启请用 restart。")
            return
        subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True)
    elif action == "stop":
        subprocess.run(["launchctl", "bootout", DOMAIN + "/" + LABEL], check=True)
    elif action == "restart":
        # Allow the bot's SIGTERM handler to stop its GPU worker before restarting.
        subprocess.run(["launchctl", "bootout", DOMAIN + "/" + LABEL], check=True)
        command = ["launchctl", "bootstrap", DOMAIN, str(PLIST)]
        # bootout can return while launchd is still unregistering the service.
        # Retry that transient EIO briefly instead of leaving the bot stopped.
        for attempt in range(20):
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                break
            if result.returncode != 5 or attempt == 19:
                raise RuntimeError(result.stderr.strip() or "服务重新启动失败。")
            time.sleep(.5)
    else:
        subprocess.run(["launchctl", "print", DOMAIN + "/" + LABEL], check=True)


if __name__ == "__main__":
    main()
