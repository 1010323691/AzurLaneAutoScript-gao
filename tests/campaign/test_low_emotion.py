"""Tests for withdrawing after low emotion popup in campaign (no device interaction)."""
import unittest
from datetime import datetime

from module.base.timer import Timer
from module.campaign.campaign_base import CampaignBase
from module.combat.emotion import Emotion
from module.exception import CampaignEnd, ScriptEnd
from tests.campaign.test_fleet_auto_swap import FakeConfig


class FakeDevice:
    def __init__(self):
        self.clicked = []

    def click(self, button):
        self.clicked.append(button.name)


class FakeLowEmotionCampaign(CampaignBase):
    """CampaignBase with screenshots replaced by a list of states."""
    _map_battle = 4

    def __init__(self, config, states, popup=True):
        """
        Args:
            config (FakeConfig):
            states (list[str]): State of each screenshot after popup cancelled,
                in_map, in_stage, fleet_preparation.
            popup (bool): If low emotion popup appears.
        """
        self.config = config
        self.device = FakeDevice()
        self.states = states
        self.state = None
        self.popup = popup
        self.popup_clicked = []
        self.withdrawn = False
        self.cancelled = False
        self.emotion = Emotion(config)

    def loop(self, skip_first=True, timeout=None):
        for state in self.states:
            self.state = state
            yield None
        raise AssertionError('Emotion withdraw not ended')

    def handle_popup_cancel(self, name='', offset=None, interval=2):
        if self.popup:
            self.popup = False
            self.popup_clicked.append(f'cancel {name}')
            return True
        return False

    def handle_popup_confirm(self, name='', offset=None, interval=2):
        if self.popup:
            self.popup = False
            self.popup_clicked.append(f'confirm {name}')
            return True
        return False

    def interval_reset(self, button, interval=None):
        pass

    def handle_story_skip(self, drop=None):
        return False

    def handle_auto_search_exit(self, drop=None):
        return False

    def appear(self, button, *args, **kwargs):
        return self.state == 'fleet_preparation' and button.name == 'FLEET_PREPARATION'

    def is_in_stage(self):
        return self.state == 'in_stage'

    def is_in_map(self):
        return self.state == 'in_map'

    def withdraw(self, skip_first_screenshot=True):
        self.withdrawn = True
        raise CampaignEnd('Withdraw')

    def enter_map_cancel(self, skip_first_screenshot=True):
        self.cancelled = True
        return True


def make(v1=41, v2=51, states=('in_map',), fleet_show_index=2, **kwargs):
    campaign = FakeLowEmotionCampaign(FakeConfig(v1, v2, **kwargs), list(states))
    campaign.low_emotion_withdraw = True
    campaign.low_emotion_in_map = 'in_map' in states or 'battle_preparation' in states
    campaign.fleet_show_index = fleet_show_index
    return campaign


class TestLowEmotionWithdraw(unittest.TestCase):
    def test_withdraw_in_map(self):
        # Real case: auto search fleet 2 met low emotion popup, calculated 41 / 51
        campaign = make()
        with self.assertRaises(ScriptEnd):
            campaign.handle_combat_low_emotion()
        self.assertEqual(campaign.popup_clicked, ['cancel IGNORE_LOW_EMOTION'])
        self.assertTrue(campaign.withdrawn)
        # Only the fleet about to battle is corrected
        self.assertEqual(campaign.config.Emotion_Fleet1Value, 41)
        self.assertEqual(campaign.config.Emotion_Fleet2Value, CampaignBase.LOW_EMOTION_POPUP_VALUE)
        delay = campaign.config.delays[-1]['target']
        self.assertGreater(delay, datetime.now())

    def test_withdraw_in_fleet_preparation(self):
        # Popup when entering map, both fleets may be low
        campaign = make(states=['fleet_preparation'])
        with self.assertRaises(ScriptEnd):
            campaign.handle_combat_low_emotion()
        self.assertTrue(campaign.cancelled)
        self.assertFalse(campaign.withdrawn)
        self.assertEqual(campaign.config.Emotion_Fleet1Value, 30)
        self.assertEqual(campaign.config.Emotion_Fleet2Value, 30)

    def test_battle_preparation_back_to_map(self):
        campaign = make(states=['battle_preparation', 'in_map'])
        campaign.appear = lambda button, *args, **kwargs: \
            campaign.state == 'battle_preparation' and button.name == 'BATTLE_PREPARATION'
        with self.assertRaises(ScriptEnd):
            campaign.handle_combat_low_emotion()
        self.assertEqual(campaign.device.clicked, ['BACK_ARROW'])
        self.assertTrue(campaign.withdrawn)

    def test_lower_emotion_not_raised(self):
        campaign = make(v1=41, v2=20)
        with self.assertRaises(ScriptEnd):
            campaign.handle_combat_low_emotion()
        self.assertEqual(campaign.config.Emotion_Fleet2Value, 20)

    def test_delay_even_if_other_fleet_order_works(self):
        # Single fleet, emotion enough after correction is impossible with control limit >= 30,
        # but fleet order without the popup fleet must still be delayed
        campaign = make(v1=119, v2=119, fleet_show_index=1, order='fleet1_standby_fleet2_all', auto_swap=False)
        with self.assertRaises(ScriptEnd):
            campaign.handle_combat_low_emotion()
        self.assertEqual(campaign.config.Emotion_Fleet1Value, 30)
        self.assertGreater(campaign.config.delays[-1]['target'], datetime.now())

    def test_no_popup(self):
        campaign = make(states=[])
        campaign.popup = False
        self.assertFalse(campaign.handle_combat_low_emotion())
        self.assertEqual(campaign.config.delays, [])

    def test_ignore_mode_confirms(self):
        campaign = make(mode='calculate_ignore')
        self.assertTrue(campaign.handle_combat_low_emotion())
        self.assertEqual(campaign.popup_clicked, ['confirm IGNORE_LOW_EMOTION'])
        self.assertFalse(campaign.withdrawn)

    def test_prevent_red_face_confirms(self):
        campaign = make(control='prevent_red_face')
        self.assertTrue(campaign.handle_combat_low_emotion())
        self.assertEqual(campaign.popup_clicked, ['confirm IGNORE_LOW_EMOTION'])
        self.assertEqual(campaign.config.delays, [])

    def test_prevent_red_face_of_other_fleet_withdraws(self):
        # Fleet 2 in map is protected, fleet 1 allowing red face doesn't matter
        campaign = make()
        campaign.config.Emotion_Fleet1Control = 'prevent_red_face'
        campaign.emotion = Emotion(campaign.config)
        with self.assertRaises(ScriptEnd):
            campaign.handle_combat_low_emotion()
        self.assertEqual(campaign.popup_clicked, ['cancel IGNORE_LOW_EMOTION'])
        self.assertTrue(campaign.withdrawn)

    def test_prevent_red_face_of_battle_fleet_confirms(self):
        campaign = make()
        campaign.config.Emotion_Fleet2Control = 'prevent_red_face'
        campaign.emotion = Emotion(campaign.config)
        self.assertTrue(campaign.handle_combat_low_emotion())
        self.assertEqual(campaign.popup_clicked, ['confirm IGNORE_LOW_EMOTION'])

    def test_prevent_red_face_of_one_fleet_entering_map_withdraws(self):
        # Popup when entering map doesn't tell which fleet is low
        campaign = make(states=['fleet_preparation'])
        campaign.config.Emotion_Fleet2Control = 'prevent_red_face'
        campaign.emotion = Emotion(campaign.config)
        with self.assertRaises(ScriptEnd):
            campaign.handle_combat_low_emotion()
        self.assertEqual(campaign.popup_clicked, ['cancel IGNORE_LOW_EMOTION'])
        self.assertTrue(campaign.cancelled)

    def test_story_skip_popup_not_low_emotion(self):
        # Real case: STORY_SKIP clicked before boss battle, its confirm popup was cancelled as low emotion,
        # then emotion withdraw waited on battle status until GameStuckError
        campaign = make()
        campaign.story_popup_timeout = Timer(10).start()
        self.assertFalse(campaign.handle_combat_low_emotion())
        self.assertEqual(campaign.popup_clicked, [])
        self.assertFalse(campaign.withdrawn)
        self.assertEqual(campaign.config.delays, [])

    def test_low_emotion_after_story_popup_timeout(self):
        campaign = make()
        campaign.story_popup_timeout = Timer(10).start()
        campaign.story_popup_timeout._start -= 11
        with self.assertRaises(ScriptEnd):
            campaign.handle_combat_low_emotion()
        self.assertEqual(campaign.popup_clicked, ['cancel IGNORE_LOW_EMOTION'])
        self.assertTrue(campaign.withdrawn)

    def test_story_popup_check_has_no_side_effect(self):
        campaign = make()
        timer = Timer(10, count=20).start()
        campaign.story_popup_timeout = timer
        self.assertTrue(campaign._story_popup_expected)
        self.assertEqual(timer._access, 0)

    def test_not_running_campaign_keeps_default(self):
        # Coalition and daemon inherit CampaignBase but don't run it, they don't catch ScriptEnd
        campaign = make()
        campaign.low_emotion_withdraw = False
        self.assertFalse(campaign.handle_combat_low_emotion())
        self.assertEqual(campaign.popup_clicked, [])


if __name__ == '__main__':
    unittest.main()
