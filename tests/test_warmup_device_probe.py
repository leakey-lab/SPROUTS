"""The device chip is named without torch: the NVIDIA driver plus the torch build (W3-WARM W1).

:func:`app.inference.device_probe.guess_device` must never claim a device inference will
not use: "GPU" only for a CUDA build of torch with a working driver, a visible device and
a driver new enough for the build; "CPU" when one of those is missing; "unknown" when the
build cannot be read. The driver is a fake library object (its functions write into the
``ctypes.byref`` arguments like the real C API), the torch build a fake ``version.py``.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import types

import pytest

from app import io_utils
from app.inference import device_probe
from app.inference.device_probe import DeviceGuess, DriverInfo, TorchBuild, guess_device


# ------------------------------------------------------------------ a fake driver
def fake_driver(
    *,
    init: int = 0,
    count: int = 1,
    name: bytes = b"Fake RTX 9000",
    version: int = 12080,
    error_names: dict | None = None,
):
    """A stand-in for nvcuda.dll / libcuda.so: the five calls the probe makes."""
    calls = []

    def cuDriverGetVersion(out):  # noqa: N802 - the C API's names
        calls.append("version")
        out._obj.value = version
        return 0

    def cuInit(flags):  # noqa: N802
        calls.append(("init", flags))
        return init

    def cuDeviceGetCount(out):  # noqa: N802
        calls.append("count")
        out._obj.value = count
        return 0

    def cuDeviceGet(out, ordinal):  # noqa: N802
        calls.append(("get", ordinal))
        out._obj.value = 7
        return 0

    def cuDeviceGetName(buffer, size, device):  # noqa: N802
        calls.append(("name", size, device.value if hasattr(device, "value") else device))
        buffer.value = name
        return 0

    lib = types.SimpleNamespace(
        cuDriverGetVersion=cuDriverGetVersion,
        cuInit=cuInit,
        cuDeviceGetCount=cuDeviceGetCount,
        cuDeviceGet=cuDeviceGet,
        cuDeviceGetName=cuDeviceGetName,
        calls=calls,
    )
    if error_names is not None:

        def cuGetErrorName(code, out):  # noqa: N802
            text = error_names.get(code)
            if text is None:
                return 1
            out._obj.value = text
            return 0

        lib.cuGetErrorName = cuGetErrorName
    return lib


CUDA_BUILD = TorchBuild(installed=True, readable=True, version="2.8.0+cu128", cuda="12.8")
CPU_BUILD = TorchBuild(installed=True, readable=True, version="2.8.0+cpu")
ONE_GPU = DriverInfo(found=True, version=12080, count=1, name="Fake RTX 9000")


# ------------------------------------------------------------------ the driver probe
def test_the_driver_names_the_first_visible_device_without_a_context():
    lib = fake_driver(count=2)
    info = device_probe.probe_driver(lambda: lib)
    assert info == DriverInfo(found=True, version=12080, count=2, name="Fake RTX 9000")
    assert ("init", 0) in lib.calls and ("get", 0) in lib.calls  # device 0 = torch's "cuda"
    assert ("name", 256, 7) in lib.calls  # the handle cuDeviceGet returned


def test_no_driver_library_reads_as_no_driver():
    assert device_probe.probe_driver(lambda: None) == DriverInfo(found=False)


def test_a_loader_that_raises_reads_as_no_driver():
    def broken():
        raise OSError("[WinError 126] The specified module could not be found")

    assert device_probe.probe_driver(broken) == DriverInfo(found=False)


@pytest.mark.parametrize("init, count", [(100, 1), (0, 0)])  # CUDA_ERROR_NO_DEVICE / no count
def test_zero_devices_is_a_count_not_an_error(init, count):
    info = device_probe.probe_driver(lambda: fake_driver(init=init, count=count))
    assert info.found and info.count == 0 and info.error is None and info.name is None


def test_an_init_error_is_named_by_the_driver_or_the_table():
    named = device_probe.probe_driver(
        lambda: fake_driver(init=46, error_names={46: b"CUDA_ERROR_DEVICE_UNAVAILABLE"})
    )
    assert named.error == "CUDA_ERROR_DEVICE_UNAVAILABLE"
    assert device_probe.probe_driver(lambda: fake_driver(init=999)).error == "CUDA_ERROR_UNKNOWN"
    assert device_probe.probe_driver(lambda: fake_driver(init=12345)).error == (
        "CUDA driver error 12345"
    )


def test_a_driver_without_cuinit_is_an_error_not_a_crash():
    lib = fake_driver()
    del lib.cuInit
    info = device_probe.probe_driver(lambda: lib)
    assert info.found and info.error and info.count == 0


# ------------------------------------------------------------------ the torch build
VERSION_PY = """from typing import Optional

__all__ = ['__version__', 'debug', 'cuda', 'git_version', 'hip']
__version__ = '2.8.0.dev20250325+cu128'
debug = False
cuda: Optional[str] = '12.8'
git_version = '3794824ceb12a9d4396eaa17795bf2147fd9e1c3'
hip: Optional[str] = None
"""


def spec_at(folder: str):
    return types.SimpleNamespace(submodule_search_locations=[folder], origin=None)


def build_from(text: str | None, *, dist: str | None = None) -> TorchBuild:
    def read_text(path: str) -> str:
        if text is None:
            raise FileNotFoundError(path)
        assert path.replace("\\", "/").endswith("site/torch/version.py")
        return text

    return device_probe.torch_build(
        find_spec=lambda name: spec_at("site/torch"),
        read_text=read_text,
        distribution_version=lambda name: dist,
    )


def test_version_py_names_a_cuda_build():
    assert build_from(VERSION_PY) == TorchBuild(
        installed=True, readable=True, version="2.8.0.dev20250325+cu128", cuda="12.8"
    )


def test_version_py_of_a_cpu_build_and_the_old_unannotated_form():
    cpu = build_from("__version__ = '2.1.0+cpu'\ncuda = None\nhip = None\n")
    assert cpu.readable and cpu.cuda is None and cpu.hip is None
    old = build_from("__version__ = '1.13.1+cu117'\ncuda = '11.7'\n")
    assert old.cuda == "11.7"


def test_version_py_of_a_rocm_build():
    rocm = build_from("__version__ = '2.5.1+rocm6.2'\ncuda = None\nhip = '6.2.41133'\n")
    assert rocm.hip == "6.2.41133" and rocm.cuda is None


@pytest.mark.parametrize(
    "dist, cuda, readable",
    [("2.5.1+cu124", "12.4", True), ("2.0.1+cu118", "11.8", True), ("2.5.1+cpu", None, True)],
)
def test_without_version_py_the_wheel_version_decides(dist, cuda, readable):
    build = build_from(None, dist=dist)
    assert (build.cuda, build.readable, build.version) == (cuda, readable, dist)


def test_a_version_without_a_local_tag_is_not_guessed():
    """The default wheel is a CPU build on Windows and a CUDA build on Linux."""
    assert build_from(None, dist="2.5.1").readable is False
    assert build_from(None, dist=None) == TorchBuild(installed=True)


def test_torch_not_installed():
    build = device_probe.torch_build(find_spec=lambda name: None)
    assert build == TorchBuild(installed=False)


def test_the_real_build_is_read_without_importing_torch():
    code = (
        "import sys\n"
        "from app.inference import device_probe\n"
        "build = device_probe.torch_build()\n"
        "guess = device_probe.probe_device()\n"
        "print('torch' in sys.modules, build.installed, isinstance(guess.device, str))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    ).stdout.split()
    assert out == ["False", "True", "True"]


# ------------------------------------------------------------------ the guess
def test_a_cuda_build_with_a_visible_device_is_the_gpu():
    guess = guess_device(ONE_GPU, CUDA_BUILD, environ={})
    assert guess.device == "GPU"
    assert guess.text == "GPU · Fake RTX 9000 · CUDA 12.8"
    assert guess.problem == "" and "driver" in guess.note


def test_a_cpu_only_build_is_the_cpu_even_with_a_gpu_present():
    guess = guess_device(ONE_GPU, CPU_BUILD, environ={})
    assert guess.device == "CPU" and guess.text == "CPU"
    assert "CPU-only build" in guess.note and "Fake RTX 9000 is not used" in guess.note


def test_no_driver_is_the_cpu():
    guess = guess_device(DriverInfo(found=False), CUDA_BUILD, environ={})
    assert (guess.device, guess.text) == ("CPU", "CPU")
    assert "No NVIDIA driver" in guess.note


def test_zero_devices_is_the_cpu():
    guess = guess_device(DriverInfo(found=True, version=12080), CUDA_BUILD, environ={})
    assert guess.device == "CPU" and "No CUDA device is visible" in guess.note


def test_a_driver_error_is_the_cpu_and_named():
    driver = DriverInfo(found=True, version=12080, error="CUDA_ERROR_UNKNOWN")
    guess = guess_device(driver, CUDA_BUILD, environ={})
    assert guess.device == "CPU" and "CUDA_ERROR_UNKNOWN" in guess.note


def test_a_driver_older_than_the_build_is_the_cpu():
    driver = DriverInfo(found=True, version=11040, count=1, name="Old GPU")
    guess = guess_device(driver, CUDA_BUILD, environ={})
    assert guess.device == "CPU"
    assert "supports CUDA 11.4" in guess.note and "needs CUDA 12" in guess.note
    newer_minor = DriverInfo(found=True, version=12020, count=1, name="GPU")
    assert guess_device(newer_minor, CUDA_BUILD, environ={}).device == "GPU"  # 12.x runs 12.8


@pytest.mark.parametrize("value", ["", " ", "-1", "none"])
def test_cuda_visible_devices_that_hides_every_device_is_the_cpu(value):
    """torch reads these as "no device" (device_count() == 0) although the driver on
    Windows ignores an empty value and still reports the GPU."""
    guess = guess_device(ONE_GPU, CUDA_BUILD, environ={"CUDA_VISIBLE_DEVICES": value})
    assert guess.device == "CPU" and "CUDA_VISIBLE_DEVICES" in guess.note


@pytest.mark.parametrize("value", ["0", "0,1", "GPU-3f1c2a", "+0"])
def test_cuda_visible_devices_that_keeps_a_device_leaves_it_to_the_driver(value):
    guess = guess_device(ONE_GPU, CUDA_BUILD, environ={"CUDA_VISIBLE_DEVICES": value})
    assert guess.device == "GPU"


def test_an_unreadable_or_rocm_build_makes_no_claim():
    unreadable = guess_device(ONE_GPU, TorchBuild(installed=True), environ={})
    rocm = guess_device(ONE_GPU, TorchBuild(True, True, "2.5.1+rocm6.2", hip="6.2"), environ={})
    for guess in (unreadable, rocm):
        assert guess.device == "unknown" and guess.text == "" and guess.problem == ""
        assert "once PyTorch has loaded" in guess.note


def test_no_torch_is_a_problem_not_a_device():
    guess = guess_device(ONE_GPU, TorchBuild(installed=False), environ={})
    assert guess.device == "unknown" and "not installed" in guess.problem


def test_probe_device_never_raises(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("probe bug")

    monkeypatch.setattr(device_probe, "probe_driver", broken)
    guess = device_probe.probe_device()
    assert isinstance(guess, DeviceGuess) and guess.device == "unknown"
    assert "probe bug" in guess.note


def test_the_driver_calls_get_c_types():
    """The real library gets argument types (a wrong width would corrupt the stack)."""

    class Function:
        def __init__(self, count: int | None = None):
            self.argtypes = None
            self.restype = None
            self.count = count

        def __call__(self, *args):
            if self.count is not None:
                args[0]._obj.value = self.count
            return 0

    lib = types.SimpleNamespace(
        cuDriverGetVersion=Function(),
        cuInit=Function(),
        cuDeviceGetCount=Function(count=1),
        cuDeviceGet=Function(),
        cuDeviceGetName=Function(),
    )
    device_probe.probe_driver(lambda: lib)
    assert lib.cuInit.argtypes == [ctypes.c_uint] and lib.cuInit.restype is ctypes.c_int
    assert lib.cuDeviceGetName.argtypes == [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]


def test_macos_note_does_not_mention_nvidia():
    guess = guess_device(DriverInfo(found=False), CUDA_BUILD, environ={}, platform="darwin")
    assert (guess.device, guess.text) == ("CPU", "CPU")
    assert "NVIDIA" not in guess.note
    assert "macOS" in guess.note


def test_the_platform_is_read_at_call_time_by_default(monkeypatch):
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "darwin")
    guess = guess_device(DriverInfo(found=False), CUDA_BUILD, environ={})
    assert "macOS" in guess.note
    monkeypatch.setattr(io_utils, "_current_platform", lambda: "linux")
    assert "No NVIDIA driver" in guess_device(DriverInfo(found=False), CUDA_BUILD, environ={}).note
