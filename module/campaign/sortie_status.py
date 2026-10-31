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
from datetime import datetime

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
STATUS_FOLDER = './log/status'


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


def status_update(config, reset=False, **kwargs):
    """
    Update runtime status of current task. Never raises, status is only for display.

    Args:
        config (AzurLaneConfig):
        reset (bool): True to drop previous status of current task, used when entering a new map.
        **kwargs: Status to set, datetime values are saved as string.
    """
    try:
        task = config.task.command
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
        os.replace(tmp, file)
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
    # Fleet order after auto swap is only an override in Alas process
    actual = status.get('order')
    if actual in FLEET_ORDER_ROLE:
        order = actual
    if order not in FLEET_ORDER_ROLE:
        order = 'fleet1_mob_fleet2_boss'
    return order, auto_swap


def _fleet_rows(data, status, order, now):
    """
    Returns:
        list[dict]: Status of each fleet.
    """
    fleet = data.get('Fleet', {})
    emotion = data.get('Emotion', {})
    roles = FLEET_ORDER_ROLE[order]
    rows = []
    for index in [1, 2]:
        number = fleet.get(f'Fleet{index}', 0)
        if not number:
            continue
        row = {
            'index': index,
            'number': number,
            'role': roles[index - 1],
            'value': None,
            'max': None,
            'limit': None,
            'fighting': False,
        }
        try:
            recover = emotion[f'Fleet{index}Recover']
            record = _to_datetime(emotion[f'Fleet{index}Record'])
            if record is not None:
                speed = emotion_speed(recover, emotion[f'Fleet{index}Oath'])
                row['max'] = DIC_RECOVER_MAX[recover]
                row['value'] = emotion_current(
                    emotion[f'Fleet{index}Value'], record, speed, row['max'], now=now)
                row['limit'] = DIC_LIMIT[emotion[f'Fleet{index}Control']]
        except (KeyError, TypeError, ValueError):
            pass
        rows.append(row)
    return rows


def _task_state(status, is_running, next_run, now):
    """
    Returns:
        dict: state: running, battle, emotion_wait, emotion_delay, defeat, pending, waiting
            and arguments of the state.
    """
    state = status.get('state')
    until = _to_datetime(status.get('until'))
    if is_running:
        if state == 'emotion_wait' and until is not None and until > now:
            return {'state': 'emotion_wait', 'until': until, 'fleet': status.get('fleet')}
        if state in ['map', 'battle']:
            return {
                'state': 'battle',
                'battle': status.get('battle', 0),
                'battle_total': status.get('battle_total', 0),
                'fleet': status.get('fleet') if state == 'battle' else None,
            }
        return {'state': 'running'}
    # Task delay of this reason is the NextRun, unless NextRun was changed later, e.g. by user
    if state in ['emotion_delay', 'defeat'] and until is not None and next_run is not None \
            and abs((next_run - until).total_seconds()) < 60 and next_run > now:
        return {'state': state, 'until': until}
    if next_run is not None and next_run <= now:
        return {'state': 'pending'}
    return {'state': 'waiting', 'until': next_run}


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
        fleets = _fleet_rows(data, task_status, order, now)
        state = _task_state(task_status, is_running, _to_datetime(data['Scheduler'].get('NextRun')), now)
        if state.get('fleet'):
            for fleet in fleets:
                fleet['fighting'] = fleet['index'] == state['fleet']
        stage = task_status.get('stage') if is_running and task_status.get('stage') \
            else data['Campaign'].get('Name', '')
        rows.append({
            'task': task,
            'stage': str(stage),
            'running': is_running,
            'auto_swap': auto_swap,
            'calculate': 'calculate' in str(data['Emotion'].get('Mode', '')),
            'fleets': fleets,
            'shared': [],
            **state,
        })

    # Tasks using the same game fleets record emotion separately, note it once on the former task
    for index, row in enumerate(rows):
        numbers = set(f['number'] for f in row['fleets'])
        for other in rows[index + 1:]:
            same = sorted(numbers & set(f['number'] for f in other['fleets']))
            if same:
                row['shared'].append((other['task'], same))
    return rows


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
