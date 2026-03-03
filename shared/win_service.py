"""
Windows service helper for NekoProxy controller and agent.
Uses pywin32 so the .exe can install/start/stop/remove itself as a Windows service.
Only used on Windows; import only when sys.platform == 'win32'.

Subclass and set _run_callback on the class to a callable(stop_event: threading.Event)
that runs your app and returns when stop_event is set.
"""

import sys
import threading

if sys.platform != "win32":
    raise RuntimeError("win_service is Windows-only")

import win32service
import win32serviceutil


class NekoProxyServiceFramework(win32serviceutil.ServiceFramework):
    """Base for NekoProxy Windows services. Runs _run_callback in a thread; SvcStop sets stop_event.
    Subclasses should set _exe_args_ = " service" so the SCM runs the exe with that arg (we then host the service).
    """

    _exe_name_ = sys.executable
    _exe_args_ = " service"  # so SCM runs "exe service" and we know to host the service

    def __init__(self, args):
        run_callback = getattr(self, "_run_callback", None) or (lambda e: e.wait())
        self._stop_event = threading.Event()
        self._run_callback = run_callback
        self._run_thread = None
        win32serviceutil.ServiceFramework.__init__(self, args)

    def SvcDoRun(self):
        self._run_thread = threading.Thread(target=self._run, daemon=False)
        self._run_thread.start()
        self._run_thread.join()

    def _run(self):
        try:
            self._run_callback(self._stop_event)
        except Exception:
            pass

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._stop_event.set()
        if self._run_thread and self._run_thread.is_alive():
            self._run_thread.join(timeout=30)
