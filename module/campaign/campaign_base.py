from module.base.decorator import Config, cached_property
from module.campaign.campaign_ui import CampaignUI
from module.combat.auto_search_combat import AutoSearchCombat
from module.exception import CampaignDefeat, CampaignEnd, MapEnemyMoved, ScriptError
from module.logger import logger
from module.map.map import Map
from module.map.map_base import CampaignMap
from module.notify import handle_notify


class CampaignBase(CampaignUI, Map, AutoSearchCombat):
    FUNCTION_NAME_BASE = 'battle_'
    MAP: CampaignMap
    # Minutes to retry after fleet defeated
    DEFEAT_RETRY_MINUTE = 60

    def battle_default(self):
        if self.clear_enemy():
            return True

        logger.warning('No battle executed.')
        return False

    def battle_boss(self):
        if self.brute_clear_boss():
            return True

        logger.warning('No battle executed.')
        return False

    @Config.when(POOR_MAP_DATA=True, MAP_CLEAR_ALL_THIS_TIME=False)
    def battle_function(self):
        logger.info('Using function: battle_with_poor_map_data')
        if self.fleet_2_break_siren_caught():
            return True
        self.clear_all_mystery()

        if self.battle_count >= 3:
            self.pick_up_ammo()

        if self.map.select(is_boss=True):
            if self.brute_clear_boss():
                return True
        else:
            if self.clear_siren():
                return True
            return self.clear_enemy()

        return False

    @Config.when(MAP_CLEAR_ALL_THIS_TIME=True)
    def battle_function(self):
        logger.info('Using function: clear_all')
        if self.fleet_2_break_siren_caught():
            return True
        self.clear_all_mystery()

        if self.battle_count >= 3:
            self.pick_up_ammo()

        remain = self.map.select(is_enemy=True) \
            .add(self.map.select(is_siren=True)) \
            .add(self.map.select(is_fortress=True)) \
            .delete(self.map.select(is_boss=True))
        logger.info(f'Enemy remain: {remain}')
        if remain.count > 0:
            if self.config.MAP_HAS_MOVABLE_NORMAL_ENEMY:
                if self.clear_any_enemy(sort=('cost_2',)):
                    return True
                return self.battle_default()
            else:
                if self.clear_bouncing_enemy():
                    return True
                if self.clear_siren():
                    return True
                self.clear_mechanism()
                return self.battle_default()
        else:
            result = self.battle_boss()
            return result

    @Config.when(MAP_CLEAR_ALL_THIS_TIME=False, POOR_MAP_DATA=False)
    def battle_function(self):
        func = self.FUNCTION_NAME_BASE + 'default'
        for extra_battle in range(10):
            if hasattr(self, self.FUNCTION_NAME_BASE + str(self.battle_count - extra_battle)):
                func = self.FUNCTION_NAME_BASE + str(self.battle_count - extra_battle)
                break

        logger.info(f'Using function: {func}')
        func = self.__getattribute__(func)

        result = func()

        return result

    def execute_a_battle(self):
        logger.hr(f'{self.FUNCTION_NAME_BASE}{self.battle_count}', level=2)
        prev = self.battle_count
        result = False
        for _ in range(10):
            try:
                result = self.battle_function()
                break
            except MapEnemyMoved:
                if self.battle_count > prev:
                    result = True
                    break
                else:
                    continue

        if not result:
            logger.warning('ScriptError, No combat executed.')
            if self.config.Error_HandleError:
                logger.warning('ScriptError, No combat executed, Withdrawing')
                self.withdraw()
            else:
                raise ScriptError('No combat executed.')

        return result

    def run(self):
        logger.hr(self.ENTRANCE, level=2)

        # Enter map
        self.emotion.check_reduce(self._map_battle)
        self.ENTRANCE.area = self.ENTRANCE.button
        self.enter_map(self.ENTRANCE, mode=self.config.Campaign_Mode)

        # Map init
        if not self.map_is_auto_search:
            self.handle_map_fleet_lock()
            self.map_init(self.MAP)
        else:
            self.map = self.MAP
            self.battle_count = 0
            self.lv_reset()
            self.lv_get()

        # Run
        for _ in range(20):
            try:
                if not self.map_is_auto_search:
                    self.execute_a_battle()
                else:
                    self.auto_search_execute_a_battle()
            except CampaignDefeat:
                self.handle_campaign_defeat()
            except CampaignEnd:
                logger.hr('Campaign end')
                return True

        # Exception
        logger.warning('Battle function exhausted.')
        if self.config.Error_HandleError:
            logger.warning('ScriptError, Battle function exhausted, Withdrawing')
            try:
                self.withdraw()
            except CampaignEnd:
                pass
        else:
            raise ScriptError('Battle function exhausted.')

    def handle_campaign_defeat(self):
        """
        Withdraw after fleet defeated and retry later,
        fighting again with the same fleets is likely to be defeated again.

        Pages:
            in: IN_MAP or stage page
            out: stage page

        Raises:
            TaskEnd:
        """
        logger.hr('Campaign defeat')
        logger.warning(f'Fleet defeated in {self.config.Campaign_Name}, '
                       f'withdraw and retry after {self.DEFEAT_RETRY_MINUTE} minutes')
        if self.is_in_map():
            try:
                self.withdraw()
            except CampaignEnd:
                pass
        self.config.task_delay(minute=self.DEFEAT_RETRY_MINUTE)
        handle_notify(
            self.config.Error_OnePushConfig,
            title=f"Alas <{self.config.config_name}> campaign defeated",
            content=f"<{self.config.config_name}> fleet defeated in {self.config.Campaign_Name}, "
                    f"retry after {self.DEFEAT_RETRY_MINUTE} minutes",
        )
        self.config.task_stop()

    def handle_clear_all_emotion(self):
        """
        Emotion was checked before entering map with _map_battle, battles until boss.
        If MAP_CLEAR_ALL_THIS_TIME, all enemies are cleared and mob fleet fights much more battles,
        swap fleet order before fleet preparation if mob fleet doesn't have enough emotion.
        """
        if not self.config.MAP_CLEAR_ALL_THIS_TIME:
            return
        battle = self._map_battle_clear_all
        if not battle:
            return
        logger.info(f'MAP_CLEAR_ALL_THIS_TIME, check emotion again with {battle} battles')
        self.emotion.check_swap(battle)

    @property
    def _map_battle_clear_all(self):
        """
        Returns:
            int: Battles to clear all enemies on this map, 0 if unknown.
        """
        # MAP.spawn_data may still be the loop data of previous run, load_spawn_data() is called later in map_init()
        # Use the same rule as Fleet.map_init(), map_is_clear_mode is known after handle_fast_forward()
        if self.map_is_clear_mode and self.MAP.spawn_data_loop:
            spawn_data = self.MAP.spawn_data_loop
        else:
            spawn_data = self.MAP._spawn_data
        battle = 0
        for data in spawn_data:
            for key in ['enemy', 'siren', 'boss']:
                battle += data.get(key, 0)
        # Mob fleet also fights boss, see FLEET_BOSS = 1 in early chapters,
        # count boss battle in mob fleet's reduce as well
        if battle and self.config.FLEET_BOSS == 1:
            battle += 1
        return battle

    @cached_property
    def _map_battle(self):
        """
        Returns:
            int: Battle on this map.
        """
        for data in self.MAP.spawn_data:
            if 'boss' in data:
                if 'battle' in data:
                    return data['battle'] + 1
                else:
                    logger.warning('No battle count in spawn_data')

        logger.warning('No boss data found in spawn_data')
        return 0

    def auto_search_execute_a_battle(self):
        logger.hr(f'{self.FUNCTION_NAME_BASE}{self.battle_count}', level=2)
        self.auto_search_moving()
        self.auto_search_combat(fleet_index=self.fleet_show_index)
        self.battle_count += 1
