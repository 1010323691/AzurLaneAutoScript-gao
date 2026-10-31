"""Tests for Restart.StuckHandle in AzurLaneAutoScript.run() (no device interaction)."""
import unittest
from unittest.mock import patch

import alas as alas_module
from alas import AzurLaneAutoScript
from module.config.config import TaskEnd
from module.exception import (GamePageUnknownError, GameStuckError, GameTooManyClickError,
                              HardNotSatisfied, RequestHumanTakeover, TaskRequirementNotMet)


class Task:
    def __init__(self, command):
        self.command = command


class FakeConfig:
    def __init__(self, mode, delay='server_update', task='Main'):
        self.data = {'Restart': {'StuckHandle': {'Mode': mode, 'SkipDelay': delay}}}
        self.task = Task(task)
        self.delays = []
        self.calls = []
        self.Error_OnePushConfig = 'provider: null'

    def cross_get(self, keys, default=None):
        d = self.data
        try:
            for k in keys.split('.'):
                d = d[k]
            return d
        except KeyError:
            return default

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)

    def task_call(self, task):
        self.calls.append(task)


class FakeDevice:
    package = 'com.YoStarJP.AzurLane'

    def screenshot(self):
        pass

    def sleep(self, *args):
        pass

    def stuck_record_clear(self):
        pass

    def click_record_clear(self):
        pass


class FakeUI:
    """Replace module.ui.ui.UI, ui_goto_main() raises `error` if set."""
    error = None
    goto_main = 0

    def __init__(self, config, device=None):
        pass

    def ui_goto_main(self):
        FakeUI.goto_main += 1
        if FakeUI.error is not None:
            raise FakeUI.error('test')


def raiser(exc):
    def f():
        raise exc('test')

    return f


class TestStuckHandle(unittest.TestCase):
    def setUp(self):
        self.exits = []
        self.notify = []
        FakeUI.error = None
        FakeUI.goto_main = 0
        patches = [
            patch.object(alas_module, 'exit', lambda code=0: self.exits.append(code), create=True),
            patch.object(alas_module, 'handle_notify', lambda *a, **kw: self.notify.append(kw)),
            patch('module.ui.ui.UI', FakeUI),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def make(self, mode, delay='server_update', task='Main'):
        a = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        a.config_name = 'test'
        a.is_first_task = False
        a.failure_record = {}
        a.stuck_retry_record = {}
        # cached_property stores into __dict__
        a.__dict__['config'] = FakeConfig(mode, delay, task)
        a.__dict__['device'] = FakeDevice()
        a.save_error_log = lambda: None
        return a

    def test_restart_mode_keeps_original_behavior(self):
        a = self.make('restart')
        a.main = raiser(GameTooManyClickError)
        self.assertFalse(a.run('main', skip_first_screenshot=True))
        self.assertEqual(a.config.calls, ['Restart'])
        self.assertEqual(a.config.delays, [])

    def make_retried(self, *args, **kwargs):
        """Task that has used up stuck retries."""
        a = self.make(*args, **kwargs)
        a.stuck_retry_record['main'] = AzurLaneAutoScript.STUCK_RETRY_LIMIT
        return a

    def test_skip_mode_retries_first(self):
        # Real case: campaign stuck on a misread screen, game restarted but task delayed to the next day
        a = self.make('skip_task')
        a.main = raiser(GameTooManyClickError)
        for n in range(AzurLaneAutoScript.STUCK_RETRY_LIMIT):
            self.assertTrue(a.run('main', skip_first_screenshot=True))
            self.assertEqual(a.config.delays, [], n)
            self.assertEqual(FakeUI.goto_main, n + 1)
        self.assertEqual(self.notify, [])
        self.assertEqual(self.exits, [])
        # Retries used up, delay
        self.assertTrue(a.run('main', skip_first_screenshot=True))
        self.assertEqual(a.config.delays, [{'server_update': True}])
        self.assertEqual(a.stuck_retry_record, {})

    def test_retry_restarts_if_cannot_goto_main(self):
        FakeUI.error = GamePageUnknownError
        a = self.make('skip_task')
        a.main = raiser(GameStuckError)
        self.assertTrue(a.run('main', skip_first_screenshot=True))
        self.assertEqual(a.config.calls, ['Restart'])
        self.assertEqual(a.config.delays, [])

    def test_retry_count_reset_after_task_finished(self):
        a = self.make('skip_task')
        a.main = raiser(GameStuckError)
        a.run('main', skip_first_screenshot=True)
        self.assertEqual(a.stuck_retry_record, {'main': 1})
        a.main = lambda: None
        self.assertTrue(a.run('main', skip_first_screenshot=True))
        self.assertEqual(a.stuck_retry_record, {})
        a.main = raiser(TaskEnd)
        a.stuck_retry_record['main'] = 1
        self.assertTrue(a.run('main', skip_first_screenshot=True))
        self.assertEqual(a.stuck_retry_record, {})

    def test_retry_count_per_task(self):
        a = self.make_retried('skip_task')
        a.config.task = Task('Event')
        a.event = raiser(GameStuckError)
        a.run('event', skip_first_screenshot=True)
        self.assertEqual(a.config.delays, [])
        self.assertEqual(a.stuck_retry_record, {'main': 2, 'event': 1})

    def test_retry_not_counted_as_failure(self):
        # Failures 3 times exits Alas, skip_task mode must not exit
        a = self.make('skip_task')
        a.main = raiser(GameStuckError)
        for _ in range(AzurLaneAutoScript.STUCK_RETRY_LIMIT + 1):
            self.assertTrue(a.run('main', skip_first_screenshot=True))

    def test_skip_mode_delays_and_goes_to_main(self):
        a = self.make_retried('skip_task')
        a.main = raiser(GameTooManyClickError)
        self.assertTrue(a.run('main', skip_first_screenshot=True))
        self.assertEqual(a.config.delays, [{'server_update': True}])
        self.assertEqual(FakeUI.goto_main, 1)
        self.assertEqual(a.config.calls, [])

    def test_skip_mode_notifies(self):
        a = self.make_retried('skip_task')
        a.main = raiser(GameStuckError)
        a.run('main', skip_first_screenshot=True)
        self.assertEqual(len(self.notify), 1)
        self.assertIn('Main', self.notify[0]['content'])
        self.assertIn('GameStuckError', self.notify[0]['content'])

    def test_skip_mode_restarts_if_cannot_goto_main(self):
        for error in [GamePageUnknownError, GameStuckError, GameTooManyClickError]:
            FakeUI.error = error
            a = self.make_retried('skip_task')
            a.main = raiser(GameStuckError)
            self.assertTrue(a.run('main', skip_first_screenshot=True))
            self.assertEqual(a.config.calls, ['Restart'], error)
            self.assertEqual(self.exits, [], error)

    def test_skip_delay_options(self):
        for delay, expected in [('1h', {'minute': 60}), ('3h', {'minute': 180}), ('6h', {'minute': 360}),
                                ('server_update', {'server_update': True})]:
            a = self.make_retried('skip_task', delay=delay)
            a.main = raiser(GameStuckError)
            a.run('main', skip_first_screenshot=True)
            self.assertEqual(a.config.delays, [expected], delay)

    def test_restart_mode_requirement_not_met_exits(self):
        a = self.make('restart')
        a.main = raiser(TaskRequirementNotMet)
        a.run('main', skip_first_screenshot=True)
        self.assertEqual(self.exits, [1])

    def test_skip_mode_requirement_not_met_delays_to_server_update(self):
        # Ignore SkipDelay, requirements like daily stage limit won't change in hours
        a = self.make('skip_task', delay='1h')
        a.main = raiser(HardNotSatisfied)
        self.assertTrue(a.run('main', skip_first_screenshot=True))
        self.assertEqual(a.config.delays, [{'server_update': True}])
        self.assertEqual(self.exits, [])
        # Not retried
        self.assertEqual(a.stuck_retry_record, {})

    def test_skip_mode_plain_human_takeover_still_exits(self):
        a = self.make('skip_task')
        a.main = raiser(RequestHumanTakeover)
        a.run('main', skip_first_screenshot=True)
        self.assertEqual(self.exits, [1])
        self.assertEqual(a.config.delays, [])

    def test_restart_task_not_skipped(self):
        a = self.make('skip_task', task='Restart')
        a.restart = raiser(GameStuckError)
        self.assertFalse(a.run('restart', skip_first_screenshot=True))
        self.assertEqual(a.config.calls, ['Restart'])
        self.assertEqual(a.config.delays, [])

    def test_goto_main_during_wait_not_skipped(self):
        a = self.make('skip_task', task='Main')
        a.goto_main = raiser(GameStuckError)
        self.assertFalse(a.run('goto_main', skip_first_screenshot=True))
        self.assertEqual(a.config.delays, [])

    def test_missing_config_falls_back_to_restart(self):
        a = self.make('skip_task')
        a.config.data = {}
        a.main = raiser(GameTooManyClickError)
        self.assertFalse(a.run('main', skip_first_screenshot=True))
        self.assertEqual(a.config.calls, ['Restart'])


if __name__ == '__main__':
    unittest.main()
