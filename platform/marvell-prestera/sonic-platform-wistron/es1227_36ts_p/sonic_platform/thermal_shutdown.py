"""Request a host halt without directly powering down the CPLD."""

import os
import tempfile
import time

from sonic_py_common import logger


REQUEST_DIR = "/host/reboot-cause"
REQUEST_PATH = os.path.join(REQUEST_DIR, ".wistron-thermal-halt")
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
REQUEST_TIMEOUT = 5
sonic_logger = logger.Logger('thermal_actions')


def request_thermal_halt():
    """Atomically submit a request for this boot to the host halt service."""
    try:
        os.sync()
    except OSError as error:
        sonic_logger.log_error("thermal shutdown: sync failed: {}".format(error))

    temporary_path = None
    try:
        with open(BOOT_ID_PATH) as boot_id_file:
            boot_id = boot_id_file.read().strip()
        if not boot_id:
            raise ValueError("empty boot ID")
        with tempfile.NamedTemporaryFile(mode='w', dir=REQUEST_DIR,
                                         prefix='.wistron-thermal-halt-',
                                         delete=False) as request_file:
            temporary_path = request_file.name
            request_file.write(boot_id + '\n')
            request_file.flush()
            os.fsync(request_file.fileno())
        os.replace(temporary_path, REQUEST_PATH)
        temporary_path = None
        deadline = time.monotonic() + REQUEST_TIMEOUT
        while os.path.exists(REQUEST_PATH) and time.monotonic() < deadline:
            time.sleep(0.1)
        if os.path.exists(REQUEST_PATH):
            sonic_logger.log_error("thermal shutdown: host did not accept halt request")
            return False
        sonic_logger.log_warning("thermal shutdown: host accepted halt request")
        return True
    except (OSError, ValueError) as error:
        sonic_logger.log_error("thermal shutdown: host halt request failed: {}".format(error))
        return False
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
