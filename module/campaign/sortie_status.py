"""
Sortie status shared by Alas process and GUI process.

Alas process writes runtime status of campaign tasks into log/status/<config_name>.json,
such as fleet order after auto swap, fleet in battle and the reason of task delay,
which are not saved in user config.
GUI process reads it with user config to show which stage is running, which fleet is fighting,
and emotion of both fleets.

This module is imported by GUI process, don't import modules using PIL or cv2.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta

from module.logger import logger

DIC_LIMIT = {
    'keep_exp_bonus': 120,
    'prevent_green_face': 40,
    'prevent_yellow_face': 30,
    'prevent_red_face': 2,
}
DIC_RECOVER = {
    'not_in_dormitory': 20,
    'dormitory_floor_1': 40,
    'dormitory_floor_2': 50,
}
DIC_RECOVER_MAX = {
    'not_in_dormitory': 119,
    'dormitory_floor_1': 150,
    'dormitory_floor_2': 150,
}
OATH_RECOVER = 10
# Fleet roles of each fleet order, (fleet_1, fleet_2)
FLEET_ORDER_ROLE = {
    'fleet1_mob_fleet2_boss': ('mob', 'boss'),
    'fleet1_boss_fleet2_mob': ('boss', 'mob'),
    'fleet1_all_fleet2_standby': ('all', 'standby'),
    'fleet1_standby_fleet2_all': ('standby', 'all'),
}
AUTO_SWAP_SUFFIX = '_auto_swap'
# Fleet 1 and fleet 3 take turns as mob fleet, fleet 2 is boss fleet
FLEET_ORDER_ROTATE = 'fleet1_mob_fleet2_boss_fleet3_rotate'
STATUS_FOLDER = './log/status'
# Tasks that don't share emotion with other tasks using the same game fleet
EMOTION_NOT_SHARED = ['GemsFarming']
# Status key of the last dorm emotion sync
EMOTION_SYNC_KEY = '_EmotionSync'


def emotion_speed(recover, oath):
    """
    Args:
        recover (str): not_in_dormitory, dormitory_floor_1, dormitory_floor_2
        oath (bool): If all ships oath.

    Returns:
        int: Recover speed per 6 min.
    """
    speed = DIC_RECOVER[recover]
    if oath:
        speed += OATH_RECOVER
    return speed // 10


def emotion_current(value, record, speed, max_value, now=None):
    """
    Args:
        value (int): Emotion value saved in config.
        record (datetime): Time when value was saved.
        speed (int): Recover speed per 6 min.
        max_value (int): Maximum emotion value.
        now (datetime):

    Returns:
        int: Calculated current emotion value.
    """
    if now is None:
        now = datetime.now()
    recover_count = int(int(now.timestamp()) // 360 - int(record.timestamp()) // 360)
    recover_count = max(recover_count, 0)
    return min(max(value, 0) + speed * recover_count, max_value)


def status_file(config_name):
    return os.path.join(STATUS_FOLDER, f'{config_name}.json')


def _to_str(value):
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return value


def _to_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def emotion_synced_recently(status, numbers, minutes, now=None):
    """
    Args:
        status (dict): Result of status_read()
        numbers (list[int]): Game fleet numbers to check
        minutes (int):
        now (datetime):

    Returns:
        bool: If all the game fleets were synced from dorm within `minutes`.
    """
    sync = status.get(EMOTION_SYNC_KEY, {})
    if not isinstance(sync, dict):
        return False
    time = _to_datetime(sync.get('time'))
    fleets = sync.get('fleets', {})
    if time is None or not isinstance(fleets, dict) or not numbers:
        return False
    if now is None:
        now = datetime.now()
    if not timedelta(0) <= now - time <= timedelta(minutes=minutes):
        return False
    return all(str(number) in fleets for number in numbers)


def is_rotate(fleet):
    """
    Args:
        fleet (dict): Fleet group of a task in user config

    Returns:
        bool: If mob fleet is rotated between fleet 1 and fleet 3, same conditions as AzurLaneConfig.bind()
    """
    if fleet.get('FleetOrder') != FLEET_ORDER_ROTATE:
        return False
    numbers = [fleet.get('Fleet1'), fleet.get('Fleet2'), fleet.get('Fleet3')]
    return all(numbers) and len(set(numbers)) == 3


def fleet_keys(fleet):
    """
    Args:
        fleet (dict): Fleet group of a task in user config

    Returns:
        list[int]: Fleet.Fleet{key} in use. Fleet 3 is only used when rotating mob fleet.
    """
    if is_rotate(fleet):
        return [1, 2, 3]
    return [1, 2]


def rotate_tasks(config_data, numbers, exclude_task=None):
    """
    Dorm holds the mob fleets of a task rotating mob fleet, instead of a mob fleet and a boss fleet.

    Args:
        config_data (dict): User config, AzurLaneConfig.data
        numbers (list[int]): Game fleet numbers
        exclude_task (str):

    Returns:
        list[str]: Enabled tasks rotating mob fleet, which use any of the game fleets.
    """
    tasks = []
    for task, data in config_data.items():
        if task == exclude_task or not isinstance(data, dict) or not isinstance(data.get('Fleet'), dict):
            continue
        if not data.get('Scheduler', {}).get('Enable', False) or not is_rotate(data['Fleet']):
            continue
        if any(data['Fleet'].get(f'Fleet{key}') in numbers for key in [1, 2, 3]):
            tasks.append(task)
    return tasks


def emotion_slots(config_data, number, exclude_task=None):
    """
    Emotion belongs to the game fleet, tasks using the same game fleet share it.

    Args:
        config_data (dict): User config, AzurLaneConfig.data
        number (int): Game fleet number, such as Fleet.Fleet1
        exclude_task (str):

    Returns:
        list[tuple[str, int]]: (task, fleet slot) using this game fleet.
    """
    slots = []
    if not number:
        return slots
    for task, data in config_data.items():
        if task == exclude_task or not isinstance(data, dict):
            continue
        # GemsFarming changes ships in its fleet and records emotion of the new ships
        if task in EMOTION_NOT_SHARED:
            continue
        if not isinstance(data.get('Emotion'), dict) or not isinstance(data.get('Fleet'), dict):
            continue
        for slot in fleet_keys(data['Fleet']):
            if data['Fleet'].get(f'Fleet{slot}') == number:
                slots.append((task, slot))
    return slots


def latest_emotion(config_data, number, exclude_task=None):
    """
    Returns:
        tuple[int, datetime]: Emotion value and record time of the game fleet, the latest record
            among tasks using it. None if no task records it.
    """
    latest = None
    for task, slot in emotion_slots(config_data, number, exclude_task=exclude_task):
        emotion = config_data[task]['Emotion']
        record = _to_datetime(emotion.get(f'Fleet{slot}Record'))
        value = emotion.get(f'Fleet{slot}Value')
        if record is None or not isinstance(value, int):
            continue
        if latest is None or record > latest[1]:
            latest = (value, record)
    return latest


def status_read(config_name):
    """
    Returns:
        dict: Key: task name, value: status of the task. Empty dict if file not exists or broken.
    """
    try:
        with open(status_file(config_name), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def status_update(config, reset=False, key=None, **kwargs):
    """
    Update runtime status of current task. Never raises, status is only for display.

    Args:
        config (AzurLaneConfig):
        reset (bool): True to drop previous status of current task, used when entering a new map.
        key (str): Status key, current task name by default.
        **kwargs: Status to set, datetime values are saved as string.
    """
    try:
        task = config.task.command if key is None else key
        name = config.config_name
    except AttributeError:
        return
    try:
        data = status_read(name)
        status = {} if reset else data.get(task, {})
        if not isinstance(status, dict):
            status = {}
        for key, value in kwargs.items():
            status[key] = _to_str(value)
        status['time'] = _to_str(datetime.now().replace(microsecond=0))
        data[task] = status

        file = status_file(name)
        os.makedirs(os.path.dirname(file), exist_ok=True)
        tmp = f'{file}.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # On Windows, replace fails if GUI process is reading the file
        for retry in range(3):
            try:
                os.replace(tmp, file)
                break
            except PermissionError:
                if retry >= 2:
                    raise
                time.sleep(0.1)
    except Exception as e:
        logger.warning(f'Failed to update campaign status: {e}')


def status_battle(config, fleet_index):
    """
    Record a battle started by fleet_index, battle count increases.
    """
    try:
        status = status_read(config.config_name).get(config.task.command, {})
        battle = int(status.get('battle', 0)) + 1
    except (AttributeError, TypeError, ValueError):
        battle = 1
    status_update(config, state='battle', fleet=fleet_index, battle=battle, until=None)


def _fleet_order(order, status):
    """
    Returns:
        tuple[str, bool]: Fleet order of this sortie, and if it's auto swap.
    """
    auto_swap = isinstance(order, str) and order.endswith(AUTO_SWAP_SUFFIX)
    if auto_swap:
        order = order[:-len(AUTO_SWAP_SUFFIX)]
    if order == FLEET_ORDER_ROTATE:
        order = 'fleet1_mob_fleet2_boss'
    # Fleet order after auto swap is only an override in Alas process
    actual = status.get('order')
    if actual in FLEET_ORDER_ROLE:
        order = actual
    if order not in FLEET_ORDER_ROLE:
        order = 'fleet1_mob_fleet2_boss'
    return order, auto_swap


def _fleet_rows(config_data, data, order, now, mob=None):
    """
    Args:
        mob (int): Game fleet in sortie slot 1 of the running task, when rotating mob fleet.

    Returns:
        list[dict]: Status of each fleet.
            `slot` is the sortie slot, None for the resting mob fleet or unknown.
    """
    fleet = data.get('Fleet', {})
    emotion = data.get('Emotion', {})
    roles = FLEET_ORDER_ROLE[order]
    rotate = is_rotate(fleet)
    rows = []
    for index in fleet_keys(fleet):
        number = fleet.get(f'Fleet{index}', 0)
        if not number:
            continue
        # Fleet 3 takes slot 1 when rotating
        slot = 1 if index == 3 else index
        role = roles[slot - 1]
        if rotate and index != 2:
            if mob is None:
                # Not running, mob fleet of the next sortie is unknown
                slot = None
            elif number == mob:
                slot = 1
            else:
                role, slot = 'rest', None
        row = {
            'index': index,
            'slot': slot,
            'number': number,
            'role': role,
            'value': None,
            'max': None,
            'limit': None,
            'speed': None,
            'fighting': False,
        }
        try:
            recover = emotion[f'Fleet{index}Recover']
            speed = emotion_speed(recover, emotion[f'Fleet{index}Oath'])
            row['speed'] = speed * 10
            row['max'] = DIC_RECOVER_MAX[recover]
            row['limit'] = DIC_LIMIT[emotion[f'Fleet{index}Control']]
            # Shared by tasks using the same game fleet
            latest = latest_emotion(config_data, number)
            if latest is not None:
                row['value'] = emotion_current(latest[0], latest[1], speed, row['max'], now=now)
        except (KeyError, TypeError, ValueError):
            pass
        rows.append(row)
    return rows

def _task_state(status, is_running, next_run, now):
    """
    Returns:
        dict: state: running, battle, emotion_wait, emotion_delay, defeat, oil_limit, pending, waiting
            and arguments of the state.
    """
    state = status.get('state')
    until = _to_datetime(status.get('until'))
    if is_running:
        if state == 'emotion_wait' and until is not None and until > now:
            return {'state': 'emotion_wait', 'until': until, 'fleet': status.get('fleet')}
        # Emotion waited, going to battle
        if state in ['map', 'battle', 'emotion_wait']:
            return {
                'state': 'battle',
                'battle': status.get('battle', 0),
                'battle_total': status.get('battle_total', 0),
                'fleet': status.get('fleet') if state != 'map' else None,
            }
        return {'state': 'running'}
    # Task delay of this reason is the NextRun, unless NextRun was changed later, e.g. by user
    if state in ['emotion_delay', 'defeat', 'oil_limit'] and until is not None and next_run is not None \
            and abs((next_run - until).total_seconds()) < 60 and next_run > now:
        if state == 'oil_limit':
            return {'state': state, 'until': until, 'oil': status.get('oil'), 'oil_limit': status.get('oil_limit')}
        return {'state': state, 'until': until}
    if next_run is not None and next_run <= now:
        return {'state': 'pending'}
    return {'state': 'waiting', 'until': next_run}


def stage_name(stage):
    """
    Campaign_Name is the map file name in Alas process, convert it back to the stage name.

    Args:
        stage (str): Such as `campaign_7_4`, `7-4`, `t5`

    Returns:
        str: Such as `7-4`, `T5`
    """
    stage = str(stage)
    res = re.match(r'^campaign_(\d+)_(\d+)$', stage, re.IGNORECASE)
    if res:
        return f'{res.group(1)}-{res.group(2)}'
    return stage.upper()


def build_rows(config_data, status, running_task=None, now=None):
    """
    Args:
        config_data (dict): User config, AzurLaneConfig.data
        status (dict): Result of status_read()
        running_task (str): Task name that Alas is running, None if not running.
        now (datetime):

    Returns:
        list[dict]: Status of each enabled campaign task.
    """
    if now is None:
        now = datetime.now()
    rows = []
    for task, data in config_data.items():
        if not isinstance(data, dict):
            continue
        if 'Campaign' not in data or 'Emotion' not in data or 'Fleet' not in data:
            continue
        if not data.get('Scheduler', {}).get('Enable', False):
            continue
        task_status = status.get(task, {})
        if not isinstance(task_status, dict):
            task_status = {}
        is_running = task == running_task
        order, auto_swap = _fleet_order(data['Fleet'].get('FleetOrder'), task_status if is_running else {})
        mob = task_status.get('mob') if is_running else None
        fleets = _fleet_rows(config_data, data, order, now, mob=mob)
        state = _task_state(task_status, is_running, _to_datetime(data['Scheduler'].get('NextRun')), now)
        if state.get('fleet'):
            for fleet in fleets:
                fleet['fighting'] = fleet['slot'] == state['fleet']
        stage = task_status.get('stage') if is_running and task_status.get('stage') \
            else data['Campaign'].get('Name', '')
        rows.append({
            'task': task,
            'stage': stage_name(stage),
            'running': is_running,
            'auto_swap': auto_swap,
            'rotate': is_rotate(data['Fleet']),
            'calculate': 'calculate' in str(data['Emotion'].get('Mode', '')),
            'fleets': fleets,
            **state,
        })
    return rows


def build_groups(config_data, status, running_task=None, now=None):
    """
    Tasks using the same game fleets are shown together, such as Main and Event,
    because emotion belongs to the game fleet.

    Args:
        config_data (dict): User config, AzurLaneConfig.data
        status (dict): Result of status_read()
        running_task (str): Task name that Alas is running, None if not running.
        now (datetime):

    Returns:
        list[dict]: Groups of `tasks` (rows of build_rows()), `fleets`, `synced` time of dorm emotion sync,
            `reversed` if dorm floors of mob fleet and boss fleet may be reversed,
            and `dorm`: 3 if fleet 1 and 3 should be in dorm (rotating mob fleet),
            2 if mob fleet and boss fleet should be, None if no hint.
    """
    rows = build_rows(config_data, status, running_task=running_task, now=now)
    sync = status.get(EMOTION_SYNC_KEY, {})
    if not isinstance(sync, dict):
        sync = {}
    synced_fleets = sync.get('fleets', {})
    if not isinstance(synced_fleets, dict):
        synced_fleets = {}

    groups = []
    for row in rows:
        numbers = sorted(f['number'] for f in row['fleets'])
        for group in groups:
            if group['numbers'] == numbers:
                break
            # A task rotating mob fleet uses fleet 3 besides the fleet 1 and 2 shared with other tasks
            if row['rotate'] or group['dorm'] == 3:
                if set(numbers) <= set(group['numbers']) or set(numbers) >= set(group['numbers']):
                    break
        else:
            group = {'numbers': numbers, 'tasks': [], 'fleets': row['fleets'], 'synced': None,
                     'reversed': False, 'dorm': None, '_widest': row['fleets']}
            groups.append(group)
        group['tasks'].append(row)
        if len(numbers) > len(group['numbers']):
            group['numbers'] = numbers
            group['_widest'] = row['fleets']
        # Fleet roles and the fighting fleet of the running task
        if row['running']:
            group['fleets'] = row['fleets']
        elif not any(task['running'] for task in group['tasks']):
            group['fleets'] = group['_widest']
        roles = {fleet['role'] for fleet in row['fleets']}
        if row['rotate']:
            group['dorm'] = 3
        elif group['dorm'] is None and 'boss' in roles and 'mob' in roles:
            group['dorm'] = 2
    for group in groups:
        # Each fleet is shown once, fleets not used by the running task are shown as well
        shown = [fleet['number'] for fleet in group['fleets']]
        group['fleets'] = group['fleets'] + [f for f in group.pop('_widest') if f['number'] not in shown]
        if any(str(number) in synced_fleets for number in group['numbers']):
            group['synced'] = _to_datetime(sync.get('time'))
            # Both dorm tabs are fleets of this group
            tabs = [synced_fleets.get(str(number), {}).get('tab') for number in group['numbers']]
            # Not shown if any task switches fleet roles, by auto swap or rotating mob fleet
            switching = any(row['auto_swap'] or row['rotate'] for row in group['tasks'])
            group['reversed'] = (group['synced'] is not None and sync.get('reversed') is True
                                 and 'training' in tabs and 'rest' in tabs and not switching)
    return groups


def format_time(time, now=None):
    """
    Returns:
        str: HH:MM if today, otherwise MM-DD HH:MM
    """
    if time is None:
        return ''
    if now is None:
        now = datetime.now()
    if time.date() == now.date():
        return time.strftime('%H:%M')
    return time.strftime('%m-%d %H:%M')
