"""
Windows service helper for NekoProxy controller and agent.
Uses pywin32 so the .exe can install/start/stop/remove itself as a Windows service.
Only used on Windows; import only when sys.platform == 'win32'.

Subclass and set _run_callback on the class to a callable(stop_event: threading.Event)
that runs your app and returns when stop_event is set.
"""

import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback

if sys.platform != "win32":
    raise RuntimeError("win_service is Windows-only")

import servicemanager
import win32service
import win32serviceutil

logger = logging.getLogger(__name__)


def _install_dir() -> str:
    """Directory the frozen exe lives in (where logs / config / certs belong).

    A Windows service starts with its working directory set to C:\\Windows\\System32,
    so nothing may be written relative to cwd.
    """
    exe = getattr(sys, "executable", None)
    if getattr(sys, "frozen", False) and exe:
        return os.path.dirname(os.path.abspath(exe))
    return os.getcwd()


def setup_service_logging(name: str) -> str:
    """Attach a rotating file handler to the root logger.

    stdout/stderr go nowhere under the Service Control Manager, so without this a
    service that fails to start (bad controller URL, port in use, missing config)
    leaves no trace. Logs land next to the exe in logs\\<name>.log.
    Returns the log file path.
    """
    # A no-console service process has sys.stdout / sys.stderr == None. Anything
    # that touches them crashes the service - notably uvicorn's log formatter,
    # which calls sys.stdout.isatty(). Give them a real (throwaway) stream.
    for _name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        if getattr(sys, _name, None) is None:
            try:
                setattr(sys, _name, open(os.devnull, "w"))
            except OSError:
                pass

    log_dir = os.path.join(_install_dir(), "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        log_dir = _install_dir()
    log_path = os.path.join(log_dir, f"{name}.log")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # A StreamHandler created at import (logging.basicConfig) before the streams
    # were restored will have stream=None and raise on every emit. Repoint it.
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is None:
            h.stream = sys.stderr
    # Don't double-add on service restart within the same process.
    already = any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and getattr(h, "baseFilename", "") == os.path.abspath(log_path)
        for h in root.handlers
    )
    if not already:
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        root.addHandler(handler)
    return log_path


class NekoProxyServiceFramework(win32serviceutil.ServiceFramework):
    """Base for NekoProxy Windows services. Runs _run_callback in a thread; SvcStop sets stop_event.

    The SCM launches the exe as "<exe> service" (see _exe_args_) so __main__ knows to host
    the service rather than re-parse install/start/stop.
    """

    _exe_name_ = sys.executable
    _exe_args_ = "service"  # SCM runs "<exe> service"

    # Subclasses set this to a plain function callable(stop_event). It is looked up
    # off the class (not the instance) so it is never turned into a bound method.
    _run_callback = None

    def __init__(self, args):
        self._stop_event = threading.Event()
        self._run_thread = None
        self._run_failed = False
        win32serviceutil.ServiceFramework.__init__(self, args)

    def _resolve_callback(self):
        cb = type(self).__dict__.get("_run_callback")
        # Walk the MRO in case a subclass didn't define it directly.
        if cb is None:
            for klass in type(self).__mro__:
                if "_run_callback" in klass.__dict__:
                    cb = klass.__dict__["_run_callback"]
                    break
        if isinstance(cb, staticmethod):
            cb = cb.__func__
        if cb is None:
            return lambda e: e.wait()
        return cb

    def SvcDoRun(self):
        servicemanager.LogInfoMsg(f"{self._svc_name_}: starting")
        callback = self._resolve_callback()

        def _run():
            try:
                callback(self._stop_event)
            except Exception:
                self._run_failed = True
                msg = f"{self._svc_name_} crashed:\n{traceback.format_exc()}"
                try:
                    servicemanager.LogErrorMsg(msg)
                except Exception:
                    pass
                logger.error(msg)

        self._run_thread = threading.Thread(target=_run, daemon=False)
        self._run_thread.start()

        # Give an instant failure (bad config, port in use) ~3s to surface so we
        # can report a clean start failure instead of a RUNNING->STOPPED flap.
        self._run_thread.join(timeout=3)

        # Tell the SCM we're up. WITHOUT this the service sticks in
        # START_PENDING and `Start-Service` fails with error 1053 after ~30s.
        if self._run_thread.is_alive():
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            servicemanager.LogInfoMsg(f"{self._svc_name_}: running")
            self._run_thread.join()

        if not self._stop_event.is_set():
            # The app exited on its own (crash, or gave up). Report a non-zero
            # exit so the SCM's configured failure/restart actions kick in.
            self._run_failed = True

        if self._run_failed:
            servicemanager.LogErrorMsg(
                f"{self._svc_name_}: exited unexpectedly - SCM recovery actions apply"
            )
            self.ReportServiceStatus(win32service.SERVICE_STOPPED, win32ExitCode=1)
            # Ensure the process itself returns non-zero for the SCM.
            os._exit(1)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=35000)
        self._stop_event.set()
        deadline = time.time() + 30
        while self._run_thread and self._run_thread.is_alive() and time.time() < deadline:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=10000)
            self._run_thread.join(timeout=3)
