"""
Fleet presets, saved settings of fleets, emotion and submarine that can be switched at once.
Main and Event always share the same settings, a preset is applied to both of them.

Presets are saved in ./config/preset/<config_name>.json, not in user config,
because ConfigUpdater drops keys that are not in the template.

This module is imported by GUI process, don't import modules using PIL or cv2.
"""
import json
import os

from module.logger import logger

PRESET_FOLDER = './config/preset'
# Tasks sharing the same fleet settings
PRESET_TASKS = ['Main', 'Event']
# Emotion values and record times are runtime state shared by game fleets, not settings
EMOTION_SETTINGS = ['Control', 'Recover', 'Oath']


def preset_file(config_name):
    return os.path.join(PRESET_FOLDER, f'{config_name}.json')


def preset_read(config_name):
    """
    Returns:
        dict: Key: preset name, value: dict of `Group.Arg` to value. Empty dict if file not exists or broken.
    """
    try:
        with open(preset_file(config_name), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: preset for name, preset in data.items() if isinstance(preset, dict)}


def preset_write(config_name, presets):
    """
    Args:
        config_name (str):
        presets (dict): Result of preset_read()
    """
    file = preset_file(config_name)
    os.makedirs(os.path.dirname(file), exist_ok=True)
    tmp = f'{file}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(presets, f, ensure_ascii=False, indent=2)
    os.replace(tmp, file)


def preset_keys(args, task):
    """
    Args:
        args (dict): Argument definitions, args.json
        task (str):

    Returns:
        list[str]: `Group.Arg` saved in presets.
    """
    keys = []
    task_args = args.get(task, {})
    for group in ['Fleet', 'Submarine']:
        for arg in task_args.get(group, {}):
            keys.append(f'{group}.{arg}')
    emotion = task_args.get('Emotion', {})
    for arg in emotion:
        if arg == 'Mode' or any(arg.endswith(setting) for setting in EMOTION_SETTINGS):
            keys.append(f'Emotion.{arg}')
    return keys


def _get(data, keys, default=None):
    for key in keys.split('.'):
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


def preset_capture(config_data, args, task=PRESET_TASKS[0]):
    """
    Args:
        config_data (dict): User config
        args (dict): Argument definitions
        task (str): Task to save settings from

    Returns:
        dict: `Group.Arg` to value
    """
    preset = {}
    for key in preset_keys(args, task):
        value = _get(config_data, f'{task}.{key}')
        if value is not None:
            preset[key] = value
    return preset


def _valid(args, task, key, value):
    arg = _get(args, f'{task}.{key}')
    if not isinstance(arg, dict):
        return False
    option = arg.get('option')
    if option and value not in option:
        return False
    return True


def preset_modified(config_data, args, preset):
    """
    Args:
        config_data (dict): User config
        args (dict): Argument definitions
        preset (dict): `Group.Arg` to value

    Returns:
        dict: `Task.Group.Arg` to value, settings to change in PRESET_TASKS.
            Settings not in this version or with invalid values are skipped.
    """
    modified = {}
    for task in PRESET_TASKS:
        keys = preset_keys(args, task)
        for key, value in preset.items():
            if key not in keys:
                continue
            if not _valid(args, task, key, value):
                logger.warning(f'Invalid preset value {value} for {task}.{key}, skip')
                continue
            if _get(config_data, f'{task}.{key}') != value:
                modified[f'{task}.{key}'] = value
    return modified


def preset_current(config_data, presets, tasks=None):
    """
    Args:
        config_data (dict): User config
        presets (dict): Result of preset_read()
        tasks (list[str]): Tasks to compare, PRESET_TASKS by default.

    Returns:
        str: Name of the preset that current settings of all `tasks` are the same as, None if no such preset.
    """
    if tasks is None:
        tasks = PRESET_TASKS
    for name, preset in presets.items():
        if preset and all(_get(config_data, f'{task}.{key}') == value
                          for task in tasks for key, value in preset.items()):
            return name
    return None
