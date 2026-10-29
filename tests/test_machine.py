import subprocess

from bsa.machine import Gpu, Machine, describe, detect, vram_warning


def _result(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _fake_run(cmd, **kwargs):
    assert isinstance(cmd, list)
    if cmd[0] == "nvidia-smi":
        return _result("NVIDIA GeForce RTX 4070 Ti, 16303, 11800\n")
    raise FileNotFoundError(cmd[0])


def test_nvidia_parsed():
    m = detect(run=_fake_run)
    assert len(m.gpus) == 1
    g = m.gpus[0]
    assert g.name == "NVIDIA GeForce RTX 4070 Ti"
    assert g.vendor == "nvidia"
    assert g.vram_gb == 15.9
    assert g.vram_used_gb == 11.5


def test_vram_warning_when_busy():
    m = Machine("Windows", "cpu", 8, 32.0, [Gpu("RTX", "nvidia", 15.9, 11.5)])
    msg = vram_warning(m, 8.0)
    assert msg is not None
    assert "11.5 GB" in msg
    assert vram_warning(m, 4.0) is None


def test_vram_warning_unknown_usage_is_silent():
    m = Machine("Windows", "cpu", 8, 32.0, [Gpu("X", "amd", 8.0, None)])
    assert vram_warning(m, 20.0) is None


def test_no_gpu_tools():
    def run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    m = detect(run=run)
    assert m.gpus == []


def test_tool_failures_never_raise():
    def run(cmd, **kwargs):
        if cmd[0] == "nvidia-smi":
            return _result("garbage", returncode=9)
        raise subprocess.TimeoutExpired(cmd, 10)

    assert detect(run=run).gpus == []


def test_describe():
    m = Machine("Windows", "Ryzen 7", 8, 32.0, [Gpu("NVIDIA GeForce RTX 4070 Ti", "nvidia", 15.9, 11.5)])
    line = describe(m)
    assert "RTX 4070 Ti" in line
    assert "15.9 GB" in line
    assert "32" in line
    assert "no GPU" in describe(Machine("Linux", "cpu", 4, 8.0, []))


def test_free_gb():
    assert Gpu("RTX", "nvidia", 15.9, 11.5).free_gb == 4.4
    assert Gpu("X", "amd", 8.0, None).free_gb is None
    assert Gpu("X", "amd", None, 1.0).free_gb is None


def test_mac_null_or_odd_displays_never_raise(monkeypatch):
    from bsa import machine

    monkeypatch.setattr(machine.platform, "system", lambda: "Darwin")
    for payload in (
        '{"SPDisplaysDataType": null}',
        '{"SPDisplaysDataType": 3}',
        "[1, 2]",
        '{"SPDisplaysDataType": [5]}',
    ):

        def run(cmd, payload=payload, **kwargs):
            if cmd[0] == "system_profiler":
                return _result(payload)
            raise FileNotFoundError(cmd[0])

        assert detect(run=run).gpus == []


_TABLE = """+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|=========================================================================================|
|    0   N/A  N/A            4321      C   .../llama.cpp/llama-server.exe        N/A      |
|    0   N/A  N/A            5555    C+G   ...t/Edge/Application/msedge.exe      N/A      |
|    0   N/A  N/A             999      C   ...ation/Blender 5.2/blender.exe      N/A      |
+-----------------------------------------------------------------------------------------+
"""


def _smi(apps: str, table: str = _TABLE):
    def run(cmd, **kwargs):
        if any(a.startswith("--query-compute-apps") for a in cmd):
            return _result(apps)
        if cmd == ["nvidia-smi"]:
            return _result(table)
        raise FileNotFoundError(cmd[0])

    return run


BUSY = Machine("Windows", "cpu", 8, 32.0, [Gpu("RTX", "nvidia", 15.9, 11.5)])


def test_vram_warning_names_the_biggest_holder():
    apps = "4321, C:/llama/llama-server.exe, 9800\n777, C:/x/python.exe, 700\n"
    msg = vram_warning(BUSY, 8.0, run=_smi(apps), me=999)
    assert "llama-server.exe is using most of the video memory" in msg
    assert "python.exe" not in msg and "11.5 GB" in msg


def test_vram_warning_lists_compute_processes_when_memory_is_unknown():
    apps = "4321, C:/llama/llama-server.exe, [N/A]\n5555, C:/Edge/msedge.exe, [N/A]\n999, blender.exe, [N/A]\n"
    msg = vram_warning(BUSY, 8.0, run=_smi(apps), me=999)
    assert "llama-server.exe" in msg  # a compute ("C") process, not the caller
    assert "msedge.exe" not in msg and "blender.exe" not in msg  # C+G desktop apps and the caller itself


def test_vram_warning_without_names_when_nvidia_smi_cannot_say():
    def run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    msg = vram_warning(BUSY, 8.0, run=run, me=999)
    assert msg is not None and "using most of the video memory" not in msg
