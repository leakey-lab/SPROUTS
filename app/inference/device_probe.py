"""Name the inference device without importing torch (the status line's device chip).

Importing torch costs 1.3 s, ~150k long-lived Python objects and 0.4 GB, and while it
initialises it holds the interpreter lock (the GUI thread stalls). The chip only needs a
name, so :func:`probe_device` asks two cheap sources instead (about 20 ms, off the GUI
thread; the driver calls release the lock):

* the NVIDIA driver, through its C API loaded with ctypes (``nvcuda.dll`` from System32 on
  Windows, ``libcuda.so.1`` elsewhere): ``cuDriverGetVersion``, ``cuInit``,
  ``cuDeviceGetCount``, ``cuDeviceGet`` and ``cuDeviceGetName``. No context is created;
  ``cuInit`` applies ``CUDA_VISIBLE_DEVICES``, and the values torch itself reads as "no
  device" (empty, ``-1``) are checked too;
* the installed torch build, read from ``torch/version.py`` (located with
  ``importlib.util.find_spec``, which does not import the package), else from its
  distribution's version (``2.8.0+cu128`` / ``+cpu``).

:func:`guess_device` combines them and never claims a device inference will not use: it
says "GPU" only for a CUDA build of torch with a working driver, at least one visible
device and a driver new enough for the build's CUDA major version, "CPU" when any of
those is missing, and "unknown" when the build cannot be read (a ROCm build, a build
without version information): the chip then waits for torch. Once torch has resolved the
runtime (:func:`app.inference.runtime.get_runtime`), its full line replaces the guess.

Stdlib only: no Qt, no torch.
"""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DeviceGuess",
    "DriverInfo",
    "TorchBuild",
    "guess_device",
    "probe_device",
    "probe_driver",
    "torch_build",
]

# CUresult codes the probe tells apart, and names for a driver without cuGetErrorName.
CUDA_SUCCESS = 0
CUDA_ERROR_NO_DEVICE = 100
_ERROR_NAMES = {
    3: "CUDA_ERROR_NOT_INITIALIZED",
    34: "CUDA_ERROR_STUB_LIBRARY",
    46: "CUDA_ERROR_DEVICE_UNAVAILABLE",
    100: "CUDA_ERROR_NO_DEVICE",
    803: "CUDA_ERROR_SYSTEM_DRIVER_MISMATCH",
    999: "CUDA_ERROR_UNKNOWN",
}
_NAME_BYTES = 256


# --------------------------------------------------------------------------- #
#  Results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DriverInfo:
    """What the NVIDIA driver says (see :func:`probe_driver`).

    ``found``: the driver library loaded. ``version``: ``cuDriverGetVersion`` (12080 =
    CUDA 12.8), or None. ``count``: visible devices. ``name``: the first visible device's
    name (the one torch's ``"cuda"`` uses). ``error``: the failing call's error name, or
    None (no device visible is ``count == 0``, not an error).
    """

    found: bool
    version: int | None = None
    count: int = 0
    name: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TorchBuild:
    """The installed torch build (see :func:`torch_build`).

    ``readable`` is False when torch is installed but its build could not be read;
    ``cuda`` / ``hip`` are the toolkit versions it was built for (None: not that kind).
    """

    installed: bool
    readable: bool = False
    version: str | None = None
    cuda: str | None = None
    hip: str | None = None


@dataclass(frozen=True)
class DeviceGuess:
    """The device inference will use, as far as the driver and the torch build tell.

    ``device``: "GPU", "CPU" or "unknown" (no claim). ``note``: one sentence for the
    chip's tooltip. ``problem``: set when inference cannot run at all (no torch).
    """

    device: str
    name: str | None = None
    cuda_version: str | None = None
    note: str = ""
    problem: str = ""

    @property
    def text(self) -> str:
        """The chip's text: ``"GPU · <name> · CUDA 12.8"``, ``"CPU"``, or "" (unknown)."""
        if self.device == "GPU":
            parts = ["GPU", self.name, f"CUDA {self.cuda_version}" if self.cuda_version else None]
            return " · ".join(part for part in parts if part)
        return "CPU" if self.device == "CPU" else ""


# --------------------------------------------------------------------------- #
#  The driver
# --------------------------------------------------------------------------- #
def _load_driver() -> Any:
    """The NVIDIA driver's C library, or None when there is none on this machine."""
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        system32 = os.path.join(root, "System32", "nvcuda.dll")
        names = [system32] if os.path.isfile(system32) else ["nvcuda.dll"]
        factory = ctypes.WinDLL
    elif sys.platform == "darwin":
        return None  # no CUDA driver on macOS
    else:
        names = ["libcuda.so.1", "libcuda.so"]
        factory = ctypes.CDLL
    for name in names:
        try:
            return factory(name)
        except OSError:
            continue
    return None


def _function(lib: Any, name: str, argtypes: list) -> Callable | None:
    """``lib.name`` with its argument types set, or None when the library lacks it."""
    try:
        function = getattr(lib, name)
    except AttributeError:
        return None
    try:
        function.argtypes = argtypes
        function.restype = ctypes.c_int
    except (AttributeError, TypeError):  # a stand-in without ctypes attributes
        pass
    return function


def _error_name(lib: Any, code: int) -> str:
    """``CUDA_ERROR_...`` for ``code`` (asks the driver, else a short table)."""
    get_name = _function(lib, "cuGetErrorName", [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)])
    if get_name is not None:
        text = ctypes.c_char_p()
        try:
            if get_name(code, ctypes.byref(text)) == CUDA_SUCCESS and text.value:
                return text.value.decode("ascii", "replace")
        except Exception:  # noqa: BLE001 - only the wording is lost
            pass
    return _ERROR_NAMES.get(code, f"CUDA driver error {code}")


def probe_driver(load: Callable[[], Any] | None = None) -> DriverInfo:
    """Ask the NVIDIA driver for its version and the first visible device (no context).

    ``load`` (a test seam) returns the driver library or None; :func:`_load_driver` by
    default. Never raises.
    """
    try:
        lib = (load or _load_driver)()
    except Exception as exc:  # noqa: BLE001 - a broken install reads as "no driver"
        logger.info("Could not load the NVIDIA driver library: %s", exc)
        lib = None
    if lib is None:
        return DriverInfo(found=False)
    int_p = ctypes.POINTER(ctypes.c_int)
    version = None
    get_version = _function(lib, "cuDriverGetVersion", [int_p])
    if get_version is not None:  # allowed before cuInit
        value = ctypes.c_int(0)
        if get_version(ctypes.byref(value)) == CUDA_SUCCESS and value.value > 0:
            version = value.value
    init = _function(lib, "cuInit", [ctypes.c_uint])
    if init is None:
        return DriverInfo(found=True, version=version, error="no cuInit in the driver library")
    code = init(0)
    if code == CUDA_ERROR_NO_DEVICE:
        return DriverInfo(found=True, version=version, count=0)
    if code != CUDA_SUCCESS:
        return DriverInfo(found=True, version=version, error=_error_name(lib, code))
    get_count = _function(lib, "cuDeviceGetCount", [int_p])
    count = ctypes.c_int(0)
    if get_count is None or get_count(ctypes.byref(count)) != CUDA_SUCCESS:
        return DriverInfo(found=True, version=version, error="the device count is unavailable")
    if count.value < 1:
        return DriverInfo(found=True, version=version, count=0)
    name = None
    get_device = _function(lib, "cuDeviceGet", [int_p, ctypes.c_int])
    get_name = _function(lib, "cuDeviceGetName", [ctypes.c_char_p, ctypes.c_int, ctypes.c_int])
    device = ctypes.c_int(0)
    if get_device is not None and get_device(ctypes.byref(device), 0) == CUDA_SUCCESS:
        buffer = ctypes.create_string_buffer(_NAME_BYTES)
        if get_name is not None and get_name(buffer, _NAME_BYTES, device) == CUDA_SUCCESS:
            name = buffer.value.decode("utf-8", "replace").strip() or None
    return DriverInfo(found=True, version=version, count=count.value, name=name)


# --------------------------------------------------------------------------- #
#  The torch build
# --------------------------------------------------------------------------- #
_ASSIGNMENT = r"^{name}\s*(?::[^=\n]*)?=\s*(?:(['\"])(?P<value>[^'\"]*)\1|None)"
_VERSION_RE = re.compile(_ASSIGNMENT.format(name="__version__"), re.MULTILINE)
_CUDA_RE = re.compile(_ASSIGNMENT.format(name="cuda"), re.MULTILINE)
_HIP_RE = re.compile(_ASSIGNMENT.format(name="hip"), re.MULTILINE)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _distribution_version(name: str) -> str | None:
    """The installed distribution's version (``importlib.metadata``: a fallback only, as
    importing it takes ~40 ms), or None."""
    try:
        from importlib import metadata

        return metadata.version(name)
    except Exception:  # noqa: BLE001 - PackageNotFoundError, a broken dist-info
        return None


def _parse_version_file(text: str) -> TorchBuild | None:
    """The build described by ``torch/version.py``, or None when it names no CUDA line."""
    cuda = _CUDA_RE.search(text)
    if cuda is None:
        return None
    version = _VERSION_RE.search(text)
    hip = _HIP_RE.search(text)
    return TorchBuild(
        installed=True,
        readable=True,
        version=version.group("value") if version else None,
        cuda=cuda.group("value") or None,
        hip=(hip.group("value") or None) if hip else None,
    )


def _parse_local_version(version: str) -> TorchBuild:
    """The build a wheel's local version names (``+cu128``, ``+cpu``, ``+rocm6.2``).

    A version without one is ambiguous (the default wheel is a CPU build on Windows and a
    CUDA build on Linux): unreadable.
    """
    local = version.partition("+")[2].lower()
    cuda = re.fullmatch(r"cu(\d+)(\d)", local)
    if cuda:
        return TorchBuild(True, True, version, cuda=f"{int(cuda.group(1))}.{cuda.group(2)}")
    if local == "cpu":
        return TorchBuild(True, True, version)
    rocm = re.fullmatch(r"rocm([\d.]+)", local)
    if rocm:
        return TorchBuild(True, True, version, hip=rocm.group(1))
    return TorchBuild(True, False, version)


def torch_build(
    find_spec: Callable[[str], Any] = importlib.util.find_spec,
    read_text: Callable[[str], str] = _read_text,
    distribution_version: Callable[[str], str | None] = _distribution_version,
) -> TorchBuild:
    """Read which torch build is installed, without importing torch (see the module doc).

    The arguments are test seams. Never raises.
    """
    try:
        spec = find_spec("torch")
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return TorchBuild(installed=False)
    folders = list(getattr(spec, "submodule_search_locations", None) or [])
    origin = getattr(spec, "origin", None)
    if not folders and origin:
        folders = [os.path.dirname(origin)]
    for folder in folders:
        try:
            build = _parse_version_file(read_text(os.path.join(folder, "version.py")))
        except (OSError, UnicodeDecodeError):
            continue
        if build is not None:
            return build
    version = distribution_version("torch")
    if version:
        return _parse_local_version(version)
    return TorchBuild(installed=True)


# --------------------------------------------------------------------------- #
#  Together
# --------------------------------------------------------------------------- #
def _major(version: str | None) -> int | None:
    try:
        return int(str(version).split(".")[0])
    except (TypeError, ValueError):
        return None


def _driver_cuda(version: int) -> str:
    """12080 -> "12.8"."""
    return f"{version // 1000}.{(version % 1000) // 10}"


def _hides_every_device(value: str | None) -> bool:
    """True when torch reads ``CUDA_VISIBLE_DEVICES=value`` as "no device".

    torch parses the variable itself for ``device_count()``: an empty value, or one whose
    first entry is not an ordinal (``-1``, ``none``), leaves no device, although the
    driver may still report one (on Windows it ignores an empty value). Other values are
    left to the driver, which applies them in ``cuInit``.
    """
    if value is None:
        return False
    first = value.split(",")[0].strip()
    if first.startswith(("GPU-", "MIG-")):
        return False
    return re.match(r"\+?\d", first) is None


def guess_device(
    driver: DriverInfo, build: TorchBuild, environ: Mapping[str, str] | None = None
) -> DeviceGuess:
    """The device the first inference will run on, as far as ``driver`` and ``build`` tell
    (``torch.cuda.is_available()`` needs the same things; see the module doc).

    ``environ`` (``os.environ`` when None) supplies ``CUDA_VISIBLE_DEVICES``.
    """
    if not build.installed:
        return DeviceGuess("unknown", problem="PyTorch is not installed: Generate cannot run.")
    later = "the device is named once PyTorch has loaded."
    if not build.readable:
        return DeviceGuess("unknown", note=f"The PyTorch build could not be read: {later}")
    if build.hip:  # ROCm builds drive AMD GPUs; the NVIDIA driver cannot speak for them
        return DeviceGuess("unknown", note=f"PyTorch {build.version} is a ROCm build: {later}")
    cpu = "inference runs on the CPU"
    if build.cuda is None:
        unused = f" ({driver.name} is not used)" if driver.name else ""
        return DeviceGuess(
            "CPU", note=f"PyTorch {build.version} is a CPU-only build: {cpu}{unused}."
        )
    if not driver.found:
        return DeviceGuess("CPU", note=f"No NVIDIA driver found: {cpu}.")
    if driver.error:
        return DeviceGuess("CPU", note=f"The NVIDIA driver reported {driver.error}: {cpu}.")
    visible = (os.environ if environ is None else environ).get("CUDA_VISIBLE_DEVICES")
    if driver.count < 1 or _hides_every_device(visible):
        why = f" (CUDA_VISIBLE_DEVICES={visible!r})" if visible is not None else ""
        return DeviceGuess("CPU", note=f"No CUDA device is visible{why}: {cpu}.")
    need = _major(build.cuda)
    if driver.version is not None and need is not None and driver.version // 1000 < need:
        return DeviceGuess(
            "CPU",
            note=(
                f"The NVIDIA driver supports CUDA {_driver_cuda(driver.version)} but PyTorch "
                f"{build.version} needs CUDA {need}: {cpu}."
            ),
        )
    return DeviceGuess(
        "GPU",
        name=driver.name,
        cuda_version=build.cuda,
        note="Named by the NVIDIA driver; the precision shows once PyTorch has loaded (Generate).",
    )


def probe_device() -> DeviceGuess:
    """:func:`guess_device` from this machine's driver and torch build (~20 ms; call it off
    the GUI thread). Never raises."""
    try:
        return guess_device(probe_driver(), torch_build())
    except Exception as exc:  # noqa: BLE001 - the chip then waits for torch
        logger.exception("Could not probe the inference device")
        return DeviceGuess("unknown", note=f"The device probe failed ({exc}).")
