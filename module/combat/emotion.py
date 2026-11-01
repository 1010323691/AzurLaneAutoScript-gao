from datetime import datetime
from time import sleep

import numpy as np

from module.base.decorator import cached_property
from module.base.utils import random_normal_distribution_int
from module.campaign.sortie_status import (DIC_LIMIT, DIC_RECOVER_MAX, EMOTION_NOT_SHARED, emotion_current,
                                           emotion_slots, emotion_speed, latest_emotion, status_battle,
                                           status_update)
from module.config.config import AzurLaneConfig
from module.exception import ScriptEnd, ScriptError, RequestHumanTakeover
from module.logger import logger

# Swap mob fleet and boss fleet, used when Fleet.FleetOrder is fleet1_mob_fleet2_boss_auto_swap
FLEET_ORDER_SWAP = {
    'fleet1_mob_fleet2_boss': 'fleet1_boss_fleet2_mob',
    'fleet1_boss_fleet2_mob': 'fleet1_mob_fleet2_boss',
}


class FleetEmotion:
    def __init__(self, config, fleet):
        """
        Args:
            config (AzurLaneConfig):
            fleet (int): Fleet index
        """
        self.config = config
        self.fleet = fleet
        self.current = 0

    @property
    def number(self):
        """
        Returns:
            int: Game fleet number of this fleet slot, 0 if unknown.
        """
        return getattr(self.config, f'Fleet_Fleet{self.fleet}', 0)

    def shared_slots(self):
        """
        Returns:
            list[tuple[str, int]]: (task, fleet slot) of other tasks using the same game fleet.
        """
        data, task = self._shared_data()
        if data is None:
            return []
        return emotion_slots(data, self.number, exclude_task=task)

    def _shared_data(self):
        """
        Returns:
            tuple[dict, str]: Config data and current task, (None, None) if emotion is not shared.
        """
        try:
            data = self.config.data
            task = self.config.task.command
        except AttributeError:
            return None, None
        if not isinstance(data, dict) or task in EMOTION_NOT_SHARED:
            return None, None
        return data, task

    def _latest(self):
        """
        Emotion belongs to the game fleet. Tasks using the same game fleet, such as Main and Event,
        share it, the latest record wins.

        Returns:
            tuple[int, datetime.datetime]:
        """
        value = getattr(self.config, f'Emotion_Fleet{self.fleet}Value')
        record = getattr(self.config, f'Emotion_Fleet{self.fleet}Record')
        data, task = self._shared_data()
        if data is not None:
            other = latest_emotion(data, self.number, exclude_task=task)
            if other is not None and other[1] > record:
                return other
        return value, record

    @property
    def value(self):
        """
        Returns:
            int: 0 to 150
        """
        return self._latest()[0]

    @property
    def value_name(self):
        """
        Returns:
            str:
        """
        return f'Emotion_Fleet{self.fleet}Value'

    @property
    def record(self):
        """
        Returns:
            datetime.datetime:
        """
        return self._latest()[1]

    @property
    def recover(self):
        """
        Returns:
            str: not_in_dormitory, dormitory_floor_1, dormitory_floor_2
        """
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Recover')

    @property
    def control(self):
        """
        Returns:
            str: keep_exp_bonus, prevent_green_face, prevent_yellow_face, prevent_red_face
        """
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Control')

    @property
    def oath(self):
        """
        Returns:
            bool: If all ships oath.
        """
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Oath')

    @property
    def speed(self):
        """
        Returns:
            int: Recover speed per 6 min.
        """
        return emotion_speed(self.recover, self.oath)

    @property
    def limit(self):
        """
        Returns:
            int: Minimum emotion value to control
        """
        return DIC_LIMIT[self.control]

    @property
    def max(self):
        """
        Returns:
            int: Maximum emotion value
        """
        return DIC_RECOVER_MAX[self.recover]

    def update(self):
        value, record = self._latest()
        self.current = emotion_current(value, record, self.speed, self.max)

    def get_recovered(self, expected_reduce=0):
        """
        Args:
            expected_reduce (int):

        Returns:
            datetime.datetime: When will emotion >= control limit.
                If already recovered, return time in the past.
        """
        if self.control == 'keep_exp_bonus' and self.recover == 'not_in_dormitory':
            logger.critical(f'Fleet {self.fleet} Emotion Control=\"Keep Happy Bonus\" and '
                            f'Fleet {self.fleet} Recover Location=\"Docks\" can not be used together, '
                            'please check your emotion settings')
            raise RequestHumanTakeover
        # In 14-4 with 2X book, expected emotion reduce is 32, can't keep happy bonus (>120),
        # otherwise will infinite task delay
        if self.control == 'keep_exp_bonus' and expected_reduce >= 29:
            expected_reduce = 29
            logger.info(f'Fleet {self.fleet} expected_reduce is limited to 29 '
                        f'when Emotion Control=\"Keep Happy Bonus\"')

        recover_count = (self.limit + expected_reduce - self.current) // self.speed
        recovered = (int(datetime.now().timestamp()) // 360 + recover_count + 1) * 360
        return datetime.fromtimestamp(recovered)


class Emotion:
    total_reduced = 0
    map_is_2x_book = False

    def __init__(self, config):
        """
        Args:
            config (AzurLaneConfig):
        """
        self.config = config
        self.fleet_1 = FleetEmotion(self.config, fleet=1)
        self.fleet_2 = FleetEmotion(self.config, fleet=2)
        self.fleets = [self.fleet_1, self.fleet_2]

    @property
    def is_calculate(self):
        return 'calculate' in self.config.Emotion_Mode

    @property
    def is_ignore(self):
        return 'ignore' in self.config.Emotion_Mode

    def update(self):
        """
        Update emotion value. This should be called before doing anything.
        """
        for fleet in self.fleets:
            fleet.update()

    def record(self):
        """
        Save current emotion value to config.
        """
        value = {}
        for fleet in self.fleets:
            value[fleet.value_name] = fleet.current

        self.config.set_record(**value)
        self.record_shared()

    def record_shared(self, **kwargs):
        """
        Save current emotion value to other tasks using the same game fleets.

        Args:
            **kwargs: Other emotion settings to save to all of them, such as Fleet1Recover=...,
                keys are named by the fleet slot of current task.
        """
        modified = {}
        record = datetime.now().replace(microsecond=0)
        for fleet in self.fleets:
            for task, slot in fleet.shared_slots():
                modified[f'{task}.Emotion.Fleet{slot}Value'] = fleet.current
                modified[f'{task}.Emotion.Fleet{slot}Record'] = record
                for key, value in kwargs.items():
                    if key.startswith(f'Fleet{fleet.fleet}'):
                        modified[f'{task}.Emotion.Fleet{slot}{key[len("FleetN"):]}'] = value
        if not modified:
            return
        with self.config.multi_set():
            for keys, value in modified.items():
                self.config.cross_set(keys=keys, value=value)

    def show(self):
        for fleet in self.fleets:
            logger.attr(f'Emotion fleet_{fleet.fleet}', fleet.value)

    @property
    def reduce_per_battle(self):
        if self.map_is_2x_book:
            return 4
        else:
            return 2

    @property
    def reduce_per_battle_before_entering(self):
        if self.map_is_2x_book:
            return 4
        elif self.config.Campaign_Use2xBook:
            return 4
        else:
            return 2

    @property
    def is_auto_swap(self):
        """
        Returns:
            bool: If mob fleet and boss fleet can be swapped according to emotion.
        """
        return self.config.FLEET_ORDER_AUTO_SWAP \
            and bool(self.config.Fleet_Fleet2) \
            and self.config.Fleet_FleetOrder in FLEET_ORDER_SWAP

    def check_reduce(self, battle, delay=True):
        """
        Check emotion before entering a campaign.
        If Fleet.FleetOrder is fleet1_mob_fleet2_boss_auto_swap, choose the fleet order of this sortie,
        mob fleet and boss fleet are swapped if mob fleet doesn't have enough emotion.

        Args:
            battle (int): Battles in this campaign
            delay (bool): False to only swap fleet order if needed, without delaying current task.
                Emotion will be waited in map if still not enough.

        Raise:
            ScriptEnd: Delay current task to prevent emotion control in the future.
        """
        if not self.is_calculate:
            return
        if not delay and not self.is_auto_swap:
            return

        self.update()
        self.record()
        self.show()
        method = self.config.Fleet_FleetOrder
        if not self.is_auto_swap:
            recovered = self.get_recovered(method, battle)
        else:
            # Keep current order, otherwise swap.
            # If neither works, allow boss fleet to fight boss as long as its emotion is above control limit,
            # because boss fleet is usually the tired one, boss battle is the last one,
            # emotion may recover during mob battles, and non-auto-search maps wait emotion before boss battle.
            swapped = FLEET_ORDER_SWAP[method]
            recovered = None
            capped = None
            for order, relax in [(method, False), (swapped, False), (method, True), (swapped, True)]:
                recover_time = self.get_recovered(order, battle, relax_boss=relax)
                if recover_time <= datetime.now():
                    if order != method:
                        logger.info(f'Emotion of mob fleet is not enough, swap fleet order: {method} -> {order}')
                        self.config.override(Fleet_FleetOrder=order)
                    return
                # Emotion is capped, the fleet order would never be enough at recover_time
                if not self.is_reachable(order, battle):
                    capped = recover_time if capped is None else min(capped, recover_time)
                    continue
                recovered = recover_time if recovered is None else min(recovered, recover_time)
            if recovered is None:
                recovered = capped
            if not delay:
                logger.info(f'Emotion of both fleet orders is not enough, keep fleet order: {method}')
                return

        if recovered > datetime.now():
            logger.info('Delay current task to prevent emotion control in the future')
            self.config.task_delay(target=recovered)
            status_update(self.config, state='emotion_delay', until=recovered)
            raise ScriptEnd('Emotion control')

    def check_swap(self, battle):
        """
        Only swap fleet order if mob fleet doesn't have enough emotion, without delaying current task.
        Calls Emotion.check_reduce() explicitly, subclasses like GemsEmotion override check_reduce()
        with a different behaviour.

        Args:
            battle (int): Battles in this campaign
        """
        if not self.is_auto_swap:
            return
        Emotion.check_reduce(self, battle, delay=False)

    def check_clear_all(self, battle):
        """
        Check emotion again before fleet preparation, after MAP_CLEAR_ALL_THIS_TIME is known.
        Mob fleet fights all enemies on map, much more than battles until boss.
        Delay current task if emotion is not enough, instead of entering map and waiting emotion in map.

        Args:
            battle (int): Battles to clear all enemies

        Raise:
            ScriptEnd: Delay current task to prevent emotion control in the future.
        """
        if not self.is_calculate:
            return
        # Subclasses like GemsEmotion override check_reduce() with a different behaviour, keep it unchanged
        if type(self).check_reduce is not Emotion.check_reduce:
            self.check_swap(battle)
            return
        method = self.config.Fleet_FleetOrder
        orders = [method, FLEET_ORDER_SWAP[method]] if self.is_auto_swap else [method]
        if not any(self.is_reachable(order, battle) for order in orders):
            # Emotion is capped, delaying would never be enough, wait emotion in map instead
            logger.warning(f'Emotion can never be enough for {battle} battles, enter map and wait in map')
            self.check_swap(battle)
            return
        Emotion.check_reduce(self, battle)

    def expected_reduce(self, method, battle, relax_boss=False):
        """
        Args:
            method (str): Fleet order
            battle (int): Battles in this campaign
            relax_boss (bool): True to only require boss fleet above control limit,
                not counting the emotion reduce of boss battle.
                Not relaxed if boss fleet is set to keep happy bonus, which would be lost.

        Returns:
            tuple[int, int]: Emotion reduce of fleet 1 and fleet 2
        """
        def boss(fleet):
            if relax_boss and fleet.control != 'keep_exp_bonus':
                return 0
            return 1

        if method == 'fleet1_mob_fleet2_boss':
            battle = (battle - 1, boss(self.fleet_2))
        elif method == 'fleet1_boss_fleet2_mob':
            battle = (boss(self.fleet_1), battle - 1)
        elif method == 'fleet1_all_fleet2_standby':
            battle = (battle, 0)
        elif method == 'fleet1_standby_fleet2_all':
            battle = (0, battle)
        else:
            raise ScriptError(f'Unknown fleet order: {method}')

        return tuple(int(b) for b in np.array(battle) * self.reduce_per_battle_before_entering)

    def is_reachable(self, method, battle):
        """
        Args:
            method (str): Fleet order
            battle (int): Battles in this campaign

        Returns:
            bool: If emotion can be enough after recovering to max, with boss fleet relaxed.
        """
        for fleet, reduce in zip(self.fleets, self.expected_reduce(method, battle, relax_boss=True)):
            # Same as FleetEmotion.get_recovered()
            if fleet.control == 'keep_exp_bonus':
                reduce = min(reduce, 29)
            if reduce and fleet.limit + reduce > fleet.max:
                return False
        return True

    def get_recovered(self, method, battle, relax_boss=False):
        """
        Args:
            method (str): Fleet order
            battle (int): Battles in this campaign
            relax_boss (bool): True to only require boss fleet above control limit,
                not counting the emotion reduce of boss battle.
                Not relaxed if boss fleet is set to keep happy bonus, which would be lost.

        Returns:
            datetime.datetime: When will all fleets have enough emotion for this campaign.
                If already recovered, return time in the past.
        """
        battle = self.expected_reduce(method, battle, relax_boss=relax_boss)
        logger.info(f'Expect emotion reduce: {battle} ({method})')

        return max([f.get_recovered(b) for f, b in zip(self.fleets, battle)])

    def wait(self, fleet_index):
        """
        Wait emotion of specific fleet.
        Should be called before entering any battles.

        Args:
            fleet_index (int): 1 or 2.
        """
        self.update()
        self.record()
        self.show()
        fleet = self.fleets[fleet_index - 1]
        recovered = fleet.get_recovered(expected_reduce=self.reduce_per_battle)
        if recovered > datetime.now():
            logger.hr('Emotion wait')
            logger.info(f'Emotion of fleet {fleet_index} will recover to {fleet.limit} at {recovered}')
            status_update(self.config, state='emotion_wait', fleet=fleet_index, until=recovered)

            while 1:
                if datetime.now() > recovered:
                    break

                logger.attr('Wait until', recovered)
                sleep(60)

    def reduce(self, fleet_index):
        """
        Reduce emotion of specific fleet.
        Should be called after battle executing.
        On server side, emotion is reduced once battle loading finished.

        Args:
            fleet_index (int): 1 or 2.
        """
        logger.hr('Emotion reduce')
        self.update()

        fleet = self.fleets[fleet_index - 1]
        fleet.current -= self.reduce_per_battle
        self.total_reduced += self.reduce_per_battle
        self.record()
        self.show()
        status_battle(self.config, fleet_index)

    @cached_property
    def bug_threshold(self):
        """
        Returns:
            int:
        """
        return random_normal_distribution_int(55, 105, n=2)

    def bug_threshold_reset(self):
        """ Call this method after emotion bug triggered. """
        del self.__dict__['bug_threshold']

    def triggered_bug(self):
        """
        Azur Lane client does not calculate emotion correctly, which is a bug.
        After a long run, we have to restart game client and let the client update it.
        """
        logger.attr('Emotion_bug', f'{self.total_reduced}/{self.bug_threshold}')
        if self.total_reduced >= self.bug_threshold:
            logger.info('Azur Lane client does not calculate emotion correctly, which is a bug. '
                        'After a long run, we have to restart game client and let the client update it.')
            self.total_reduced = 0
            self.bug_threshold_reset()
            return True
        else:
            return False
