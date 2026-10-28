from datetime import datetime
from time import sleep

import numpy as np

from module.base.decorator import cached_property
from module.base.utils import random_normal_distribution_int
from module.config.config import AzurLaneConfig
from module.config.deep import deep_get
from module.exception import ScriptEnd, ScriptError, RequestHumanTakeover
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
# Fleet order that swaps mob fleet and boss fleet according to emotion before each sortie
FLEET_ORDER_AUTO_SWAP = 'fleet1_mob_fleet2_boss_auto_swap'
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
    def value(self):
        """
        Returns:
            int: 0 to 150
        """
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Value')

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
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Record')

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
        speed = DIC_RECOVER[self.recover]
        if self.oath:
            speed += OATH_RECOVER
        return speed // 10

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
        recover_count = int(int(datetime.now().timestamp()) // 360 - int(self.record.timestamp()) // 360)
        recover_count = max(recover_count, 0)
        self.current = min(max(self.value, 0) + self.speed * recover_count, self.max)

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
    def is_fleet_order_auto_swap(self):
        """
        Returns:
            bool: If user set Fleet.FleetOrder to fleet1_mob_fleet2_boss_auto_swap.
                Fleet_FleetOrder is overridden to a concrete order after the first check_reduce(),
                so also check the raw config of current task.
        """
        if self.config.Fleet_FleetOrder == FLEET_ORDER_AUTO_SWAP:
            return True
        task = self.config.task
        if task is None:
            return False
        return deep_get(self.config.data, keys=f'{task.command}.Fleet.FleetOrder') == FLEET_ORDER_AUTO_SWAP

    def check_reduce(self, battle):
        """
        Check emotion before entering a campaign.
        If Fleet.FleetOrder is fleet1_mob_fleet2_boss_auto_swap, choose the fleet order of this sortie,
        mob fleet and boss fleet are swapped if mob fleet doesn't have enough emotion.

        Args:
            battle (int): Battles in this campaign

        Raise:
            ScriptEnd: Delay current task to prevent emotion control in the future.
        """
        auto_swap = self.is_fleet_order_auto_swap
        if auto_swap and self.config.Fleet_FleetOrder not in FLEET_ORDER_SWAP:
            # First sortie in this task, start with fleet1 mob
            self.config.override(Fleet_FleetOrder='fleet1_mob_fleet2_boss')

        if not self.is_calculate:
            return

        self.update()
        self.record()
        self.show()
        method = self.config.Fleet_FleetOrder
        # When swapping, boss fleet is usually the tired one,
        # allow it to fight boss as long as its emotion is above control limit.
        # Boss battle is the last one, emotion may recover during mob battles,
        # and non-auto-search maps still wait emotion before boss battle.
        recovered = self.get_recovered(method, battle, relax_boss=auto_swap)
        if recovered > datetime.now() and auto_swap and self.config.Fleet_Fleet2:
            swapped = FLEET_ORDER_SWAP[method]
            swapped_recovered = self.get_recovered(swapped, battle, relax_boss=True)
            if swapped_recovered <= datetime.now():
                logger.info(f'Emotion of mob fleet is not enough, swap fleet order: {method} -> {swapped}')
                self.config.override(Fleet_FleetOrder=swapped)
                return
            recovered = min(recovered, swapped_recovered)

        if recovered > datetime.now():
            logger.info('Delay current task to prevent emotion control in the future')
            self.config.task_delay(target=recovered)
            raise ScriptEnd('Emotion control')

    def get_recovered(self, method, battle, relax_boss=False):
        """
        Args:
            method (str): Fleet order
            battle (int): Battles in this campaign
            relax_boss (bool): True to only require boss fleet above control limit,
                not counting the emotion reduce of boss battle.

        Returns:
            datetime.datetime: When will all fleets have enough emotion for this campaign.
                If already recovered, return time in the past.
        """
        boss = 0 if relax_boss else 1
        if method == 'fleet1_mob_fleet2_boss':
            battle = (battle - 1, boss)
        elif method == 'fleet1_boss_fleet2_mob':
            battle = (boss, battle - 1)
        elif method == 'fleet1_all_fleet2_standby':
            battle = (battle, 0)
        elif method == 'fleet1_standby_fleet2_all':
            battle = (0, battle)
        else:
            raise ScriptError(f'Unknown fleet order: {method}')

        battle = tuple(np.array(battle) * self.reduce_per_battle_before_entering)
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
