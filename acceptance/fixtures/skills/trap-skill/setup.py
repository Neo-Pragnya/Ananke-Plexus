# Booby trap for the acceptance suite: learning or inspecting a skill must never execute its code.
import os
import pathlib

pathlib.Path(os.environ.get("SANDBOX", "/tmp"), "TRAP_EXECUTED").write_text("executed")
