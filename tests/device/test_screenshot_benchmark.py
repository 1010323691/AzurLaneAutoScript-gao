"""Tests for the screenshot benchmark at Alas startup (no device interaction)."""
import collections
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from module.daemon.benchmark import Benchmark
from module.device.device import Device
from module.exception import GameTooManyClickError


class FakeConfig:
    Emulator_ScreenshotMethod = 'auto'

    @contextmanager
    def multi_set(self):
        yield


class FakeBenchmark:
    error = None

    def __init__(self, config, device):
        device.disable_stuck_detection()

    def run_simple_screenshot_benchmark(self):
        if self.error is not None:
            raise self.error
        return 'ADB_nc'


class FailedBenchmark(FakeBenchmark):
    error = ValueError('benchmark failed')


def get_device():
    device = Device.__new__(Device)
    device.config = FakeConfig()
    device.click_record = collections.deque(maxlen=15)
    device.resolution_check_uiautomator2 = lambda: None
    return device


class TestStuckDetection(unittest.TestCase):
    def assert_enabled(self, device):
        for _ in range(12):
            device.click_record.append('BUTTON')
        with self.assertRaises(GameTooManyClickError):
            device.click_record_check()

    def assert_disabled(self, device):
        for _ in range(12):
            device.click_record.append('BUTTON')
        self.assertFalse(device.click_record_check())
        self.assertFalse(device.stuck_record_check())

    def test_restored_after_benchmark(self):
        device = get_device()
        with patch('module.daemon.benchmark.Benchmark', FakeBenchmark):
            device.run_simple_screenshot_benchmark()
        self.assertEqual(device.config.Emulator_ScreenshotMethod, 'ADB_nc')
        self.assert_enabled(device)

    def test_restored_when_benchmark_failed(self):
        device = get_device()
        with patch('module.daemon.benchmark.Benchmark', FailedBenchmark):
            with self.assertRaises(ValueError):
                device.run_simple_screenshot_benchmark()
        self.assert_enabled(device)

    def test_daemon_stays_disabled(self):
        # Benchmark may run in the middle of daemon tasks, see Device.screenshot()
        device = get_device()
        device.disable_stuck_detection()
        with patch('module.daemon.benchmark.Benchmark', FakeBenchmark):
            device.run_simple_screenshot_benchmark()
        self.assert_disabled(device)

    def test_disable_and_enable(self):
        device = get_device()
        device.disable_stuck_detection()
        self.assert_disabled(device)
        device.enable_stuck_detection()
        self.assert_enabled(device)


class FakeDevice:
    is_chinac_phone_cloud = False

    def __init__(self, sdk_ver, is_bluestacks_air=False):
        self.sdk_ver = sdk_ver
        self.is_bluestacks_air = is_bluestacks_air

    def nemu_ipc_available(self):
        return False

    def ldopengl_available(self):
        return False


class TestBenchmarkMethods(unittest.TestCase):
    def get_methods(self, device):
        bench = Benchmark.__new__(Benchmark)
        bench.device = device
        with patch.object(Benchmark, 'benchmark', return_value=('ADB', None)) as benchmark:
            bench.run_simple_screenshot_benchmark()
        return benchmark.call_args[0][0]

    def test_android_12_has_droidcast(self):
        self.assertIn('DroidCast_raw', self.get_methods(FakeDevice(sdk_ver=32)))

    def test_android_13_has_no_droidcast(self):
        # Device.method_check() rejects it, choosing it means benchmark again on every start
        methods = self.get_methods(FakeDevice(sdk_ver=33))
        self.assertNotIn('DroidCast', methods)
        self.assertNotIn('DroidCast_raw', methods)
        self.assertIn('ADB_nc', methods)

    def test_android_5_has_no_droidcast(self):
        self.assertNotIn('DroidCast_raw', self.get_methods(FakeDevice(sdk_ver=22)))

    def test_bluestacks_air_has_no_droidcast(self):
        methods = self.get_methods(FakeDevice(sdk_ver=32, is_bluestacks_air=True))
        self.assertNotIn('DroidCast', methods)
        self.assertNotIn('DroidCast_raw', methods)


if __name__ == '__main__':
    unittest.main()
