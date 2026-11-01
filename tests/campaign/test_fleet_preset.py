"""Tests for fleet presets shared by Main and Event (no device interaction)."""
import copy
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import module.campaign.fleet_preset as fleet_preset
from module.campaign.fleet_preset import (preset_capture, preset_current, preset_keys, preset_modified, preset_read,
                                          preset_write)
from module.config.utils import filepath_args, read_file

ARGS = read_file(filepath_args())
TEMPLATE = read_file('./config/template.json')


def config_data():
    data = copy.deepcopy({task: TEMPLATE[task] for task in ['Main', 'Event', 'Main2']})
    for task in ['Main', 'Event']:
        data[task]['Fleet'].update(Fleet1=1, Fleet2=2, Fleet3=3, FleetOrder='fleet1_mob_fleet2_boss_fleet3_rotate')
        data[task]['Emotion'].update(Fleet1Value=65, Fleet1Recover='dormitory_floor_2',
                                     Fleet3Recover='dormitory_floor_1')
        data[task]['Submarine'].update(Fleet=1, Mode='boss_only')
    return data


class TestPresetKeys(unittest.TestCase):
    def test_keys(self):
        keys = preset_keys(ARGS, 'Main')
        self.assertIn('Fleet.Fleet3', keys)
        self.assertIn('Fleet.FleetOrder', keys)
        self.assertIn('Submarine.Mode', keys)
        self.assertIn('Emotion.Mode', keys)
        self.assertIn('Emotion.Fleet3Recover', keys)
        # Emotion values are runtime state
        self.assertNotIn('Emotion.Fleet1Value', keys)
        self.assertNotIn('Emotion.Fleet1Record', keys)
        self.assertNotIn('Campaign.Name', keys)


class TestPreset(unittest.TestCase):
    def test_capture(self):
        preset = preset_capture(config_data(), ARGS)
        self.assertEqual(preset['Fleet.Fleet3'], 3)
        self.assertEqual(preset['Fleet.FleetOrder'], 'fleet1_mob_fleet2_boss_fleet3_rotate')
        self.assertEqual(preset['Emotion.Fleet1Recover'], 'dormitory_floor_2')
        self.assertEqual(preset['Submarine.Mode'], 'boss_only')
        self.assertNotIn('Emotion.Fleet1Value', preset)

    def test_apply_to_main_and_event(self):
        data = config_data()
        preset = preset_capture(data, ARGS)
        # Another preset: two fleets, auto swap
        for task in ['Main', 'Event']:
            data[task]['Fleet'].update(Fleet3=0, FleetOrder='fleet1_mob_fleet2_boss_auto_swap')
            data[task]['Emotion']['Fleet2Recover'] = 'dormitory_floor_1'
        modified = preset_modified(data, ARGS, preset)
        self.assertEqual(modified['Main.Fleet.Fleet3'], 3)
        self.assertEqual(modified['Event.Fleet.FleetOrder'], 'fleet1_mob_fleet2_boss_fleet3_rotate')
        self.assertEqual(modified['Event.Emotion.Fleet2Recover'], 'not_in_dormitory')
        # Unchanged settings and other tasks are not written
        self.assertNotIn('Main.Fleet.Fleet1', modified)
        self.assertFalse(any(key.startswith('Main2.') for key in modified))

    def test_invalid_and_unknown_skipped(self):
        preset = {'Fleet.Fleet1': 9, 'Fleet.Fleet2': 4, 'Fleet.Unknown': 1, 'Emotion.Fleet1Value': 10}
        modified = preset_modified(config_data(), ARGS, preset)
        self.assertEqual(modified, {'Main.Fleet.Fleet2': 4, 'Event.Fleet.Fleet2': 4})

    def test_current(self):
        data = config_data()
        presets = {'two': {'Fleet.Fleet3': 0}, 'three': preset_capture(data, ARGS)}
        self.assertEqual(preset_current(data, presets), 'three')
        # Emotion values change all the time, still the same preset
        data['Main']['Emotion']['Fleet1Value'] = 10
        self.assertEqual(preset_current(data, presets), 'three')
        data['Main']['Fleet']['Fleet3'] = 0
        self.assertIsNone(preset_current(data, {'three': presets['three']}))
        self.assertEqual(preset_current(data, presets), 'two')


class TestPresetFile(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        patcher = patch.object(fleet_preset, 'PRESET_FOLDER', os.path.join(folder, 'preset'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_read_write(self):
        self.assertEqual(preset_read('zz'), {})
        preset_write('zz', {'三队': {'Fleet.Fleet3': 3}})
        self.assertEqual(preset_read('zz'), {'三队': {'Fleet.Fleet3': 3}})

    def test_broken_file(self):
        os.makedirs(fleet_preset.PRESET_FOLDER)
        with open(fleet_preset.preset_file('zz'), 'w', encoding='utf-8') as f:
            f.write('{broken')
        self.assertEqual(preset_read('zz'), {})


if __name__ == '__main__':
    unittest.main()
