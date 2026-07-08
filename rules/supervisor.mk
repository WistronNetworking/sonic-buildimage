# supervisor python3 wheel

SUPERVISOR = supervisor-4.2.5-py2.py3-none-any.whl
$(SUPERVISOR)_SRC_PATH = $(SRC_PATH)/supervisor
$(SUPERVISOR)_PYTHON_VERSION = 3
# Skip the wheel's pytest phase: test_stop_report_laststopreport_in_future is a
# sleep(2)-based timing test that fails intermittently under parallel build load
# (observed 1 failed / 1384 passed on SONIC_BUILD_JOBS=8).
$(SUPERVISOR)_TEST = n
SONIC_PYTHON_WHEELS += $(SUPERVISOR)
