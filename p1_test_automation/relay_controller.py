import subprocess
from typing import Optional


class RelayController:
    def __init__(self, relay_number, relay_id: Optional[str] = None, cmd_binary_path="hidusb-relay-cmd") -> None:
        self.relay_number = relay_number
        self.relay_id = relay_id
        self.cmd_binary_path = cmd_binary_path

    def send_cmd(self, on_state: bool):
        cmd = [self.cmd_binary_path]
        if self.relay_id:
            cmd.append(f'ID={self.relay_id}')
        cmd.append('on' if on_state else 'off')
        cmd.append(str(self.relay_number))
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(
                f'HID relay command failed.\n{result.args}:\n{result.stderr}')
