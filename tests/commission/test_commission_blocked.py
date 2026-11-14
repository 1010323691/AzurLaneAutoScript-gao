"""Tests for delaying commission when rewards can't be received, e.g. oil maxed (no device interaction)."""
import contextlib
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import module.commission.commission as commission_module
from module.campaign import sortie_status
from module.commission.commission import (
    REWARD_BLOCKED_CLICK, REWARD_BLOCKED_STATUS_KEY, RewardCommission, commission_blocked_until)
from module.config.config import TaskEnd
from module.exception import CommissionRewardBlocked


class AlwaysReached:
    def __init__(self, *args, **kwargs):
        pass

    def reached(self):
        return True

    def reset(self):
        return self


class FakeStat:
    def new(self, *args, **kwargs):
        return contextlib.nullcontext(None)


class FakeDevice:
    image = None

    def __init__(self):
        self.screenshots = 0

    def screenshot(self):
        self.screenshots += 1


class FakeConfig:
    config_name = 'zz_test_commission_blocked'
    DropRecord_CommissionRecord = 'do_not'
    SERVER = 'jp'

    def __init__(self):
        self.delay = None

    def task_delay(self, minute=None, **kwargs):
        self.delay = minute

    @staticmethod
    def task_stop(message=''):
        raise TaskEnd(message)


def make_commission(frames):
    """
    Args:
        frames (list[dict]): Per screenshot: 'reward' (REWARD_1 clickable), 'info_bar', 'items' (GET_ITEMS shown),
            'commission' (arrived page_commission).
    """
    c = RewardCommission.__new__(RewardCommission)
    c.config = FakeConfig()
    c.device = FakeDevice()
    c.stat = FakeStat()
    c.clicks = []

    def frame():
        index = min(c.device.screenshots, len(frames) - 1)
        return frames[index]

    def appear_then_click(button, **kwargs):
        if button.name == 'REWARD_1' and frame().get('reward'):
            c.clicks.append(button.name)
            return True
        return False

    def appear(button, **kwargs):
        return button.name == 'GET_ITEMS_1' and bool(frame().get('items'))

    c.appear_then_click = appear_then_click
    c.appear = appear
    c.ui_page_appear = lambda page, **kwargs: bool(frame().get('commission'))
    c.info_bar_count = lambda: 1 if frame().get('info_bar') else 0
    c.ui_main_appear_then_click = lambda *args, **kwargs: False
    c.ui_additional = lambda: False
    c.ensure_no_info_bar = lambda **kwargs: None
    c.interval_reset = lambda *args: None
    c.handle_info_bar = lambda: False
    c.device.click = lambda button: c.clicks.append(button.name)
    return c


class TestReceiveBlocked(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(commission_module, 'Timer', AlwaysReached)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_blocked_by_info_bar(self):
        # Every click on REWARD_1 only shows an info bar
        c = make_commission([{'reward': True, 'info_bar': True}] * 50)
        with self.assertRaises(CommissionRewardBlocked):
            c._commission_receive()
        self.assertEqual(c.clicks.count('REWARD_1'), REWARD_BLOCKED_CLICK)

    def test_no_info_bar_not_blocked(self):
        # Unknown reason without info bar, keep upstream behaviour (too many click error from device)
        frames = [{'reward': True}] * 20 + [{'commission': True}]
        c = make_commission(frames)
        c._commission_receive()
        self.assertGreater(c.clicks.count('REWARD_1'), REWARD_BLOCKED_CLICK)

    def test_normal_receive(self):
        frames = [{'reward': True}, {'items': True}, {'items': True}, {'commission': True}]
        c = make_commission(frames)
        self.assertTrue(c._commission_receive())
        self.assertEqual(c.clicks.count('REWARD_1'), 1)

    def test_received_items_reset_count(self):
        # Several rounds of rewards, each needs a few clicks with info bars, but rewards are received
        frames = []
        for _ in range(3):
            frames += [{'reward': True, 'info_bar': True}] * (REWARD_BLOCKED_CLICK - 1)
            frames += [{'items': True}]
        frames += [{'commission': True}]
        c = make_commission(frames)
        self.assertTrue(c._commission_receive())


class TestBlockedStatus(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        patcher = patch.object(sortie_status, 'STATUS_FOLDER', os.path.join(self.folder, 'status'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_delay_and_mark(self):
        c = make_commission([{}])
        with self.assertRaises(TaskEnd):
            c.commission_reward_blocked()
        self.assertEqual(c.config.delay, 120)
        until = commission_blocked_until(FakeConfig.config_name)
        self.assertIsNotNone(until)
        self.assertAlmostEqual((until - datetime.now()).total_seconds(), 7200, delta=5)

    def test_expired(self):
        c = make_commission([{}])
        with self.assertRaises(TaskEnd):
            c.commission_reward_blocked()
        later = datetime.now() + timedelta(minutes=121)
        self.assertIsNone(commission_blocked_until(FakeConfig.config_name, now=later))

    def test_cleared(self):
        sortie_status.status_update(FakeConfig(), key=REWARD_BLOCKED_STATUS_KEY,
                                    until=datetime.now() + timedelta(hours=1))
        self.assertIsNotNone(commission_blocked_until(FakeConfig.config_name))
        sortie_status.status_update(FakeConfig(), key=REWARD_BLOCKED_STATUS_KEY, until=None)
        self.assertIsNone(commission_blocked_until(FakeConfig.config_name))

    def test_no_file(self):
        self.assertIsNone(commission_blocked_until('zz_not_exist'))


class FakeCampaign:
    def __init__(self, notice):
        self.notice = notice

    def commission_notice_show_at_campaign(self):
        return self.notice


class FakeRunConfig(FakeConfig):
    def __init__(self):
        super().__init__()
        self.called = []

    def task_call(self, task, force_call=True):
        self.called.append(task)


class TestCommissionNotice(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        patcher = patch.object(sortie_status, 'STATUS_FOLDER', os.path.join(self.folder, 'status'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_run(self):
        from module.campaign.run import CampaignRun
        run = CampaignRun.__new__(CampaignRun)
        run.config = FakeRunConfig()
        run.campaign = FakeCampaign(notice=True)
        return run

    def test_notice_calls_commission(self):
        run = self.make_run()
        with self.assertRaises(TaskEnd):
            run.handle_commission_notice()
        self.assertEqual(run.config.called, ['Commission'])

    def test_notice_ignored_when_blocked(self):
        sortie_status.status_update(FakeConfig(), key=REWARD_BLOCKED_STATUS_KEY,
                                    until=datetime.now() + timedelta(hours=1))
        run = self.make_run()
        run.handle_commission_notice()
        self.assertEqual(run.config.called, [])


if __name__ == '__main__':
    unittest.main()
