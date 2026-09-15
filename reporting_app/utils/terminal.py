"""Cross-platform utility for launching commands in an external terminal window."""

import logging
import os
import platform
import shutil
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


def launch_in_terminal(command: str, title: str = "Terminal", cwd: Optional[str] = None) -> bool:
    """Launch a command in an external interactive terminal window across platforms.

    Supports Linux (konsole, gnome-terminal, xfce4-terminal, alacritty, kitty,
    x-terminal-emulator, xterm, xdg-terminal-exec), macOS (Terminal.app), and
    Windows (cmd.exe / start).
    """
    system = platform.system()
    work_dir = cwd or os.getcwd()

    try:
        if system == "Windows":
            cmd = f'start "{title}" cmd /k "{command}"'
            subprocess.Popen(cmd, shell=True, cwd=work_dir)
            return True

        if system == "Darwin":
            escaped = command.replace('"', '\\"')
            script = f'tell application "Terminal" to do script "{escaped}"\ntell application "Terminal" to activate'
            subprocess.Popen(["osascript", "-e", script], cwd=work_dir)
            return True

        if system == "Linux":
            wrapped_cmd = f"{command}; echo; read -p 'Press Enter to close...' -r"

            terminals: List[List[str]] = [
                ["konsole", "-e", "bash", "-c", wrapped_cmd],
                ["alacritty", "-e", "bash", "-c", wrapped_cmd],
                ["gnome-terminal", "--", "bash", "-c", wrapped_cmd],
                ["xfce4-terminal", "-e", f'bash -c "{wrapped_cmd}"'],
                ["x-terminal-emulator", "-e", f'bash -c "{wrapped_cmd}"'],
                ["kitty", "-e", "bash", "-c", wrapped_cmd],
                ["terminator", "-e", f'bash -c "{wrapped_cmd}"'],
                ["xterm", "-e", "bash", "-c", wrapped_cmd],
            ]

            for term_args in terminals:
                binary = term_args[0]
                if shutil.which(binary):
                    logger.info(f"Launching terminal using {binary}")
                    subprocess.Popen(term_args, cwd=work_dir)
                    return True

            if shutil.which("xdg-terminal-exec"):
                subprocess.Popen(["xdg-terminal-exec", "bash", "-c", wrapped_cmd], cwd=work_dir)
                return True

            logger.warning("No standard GUI terminal emulator found. Launching in background bash.")
            subprocess.Popen(["bash", "-c", command], cwd=work_dir)
            return True

        subprocess.Popen(["bash", "-c", command], cwd=work_dir)
        return True

    except Exception as e:
        logger.error(f"Failed to launch command in terminal: {e}")
        return False
