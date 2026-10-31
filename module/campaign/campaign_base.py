from datetime import datetime, timedelta

from module.base.decorator import Config, cached_property
from module.campaign.sortie_status import status_update
from module.campaign.campaign_ui import CampaignUI
from module.combat.assets import BATTLE_PREPARATION
from module.combat.auto_search_combat import AutoSearchCombat
from module.exception import CampaignDefeat, CampaignEnd, MapEnemyMoved, ScriptEnd, ScriptError
from module.logger import logger
from module.handler.assets import AUTO_SEARCH_MAP_OPTION_OFF
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_HARD
from module.map.map import Map
from module.map.map_base import CampaignMap
from module.notify import handle_notify
from module.ui.assets import BACK_ARROW


class CampaignBase(CampaignUI, Map, AutoSearchCombat):
    FUNCTION_NAME_BASE = 'battle_'
    MAP: CampaignMap
    # Minutes to retry after fleet defeated
    DEFEAT_RETRY_MINUTE = 60
    # Game shows low emotion popup when emotion is below this value (yellow face)
    LOW_EMOTION_POPUP_VALUE = 30
    # Set in run(), low emotion popup is withdrawn only when running a campaign map.
    # Other subclasses (coalition, daemon) don't catch ScriptEnd, keep the default behaviour.
    low_emotion_withdraw = False
    # Set in run() after entering map, low emotion popup in map is about fleet_show_index only
    low_emotion_in_map = False

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
        self.low_emotion_withdraw = True
        self.low_emotion_in_map = False
        self.emotion.check_reduce(self._map_battle)
        self.ENTRANCE.area = self.ENTRANCE.button
        self.enter_map(self.ENTRANCE, mode=self.config.Campaign_Mode)
        self.low_emotion_in_map = True
        self.sortie_status_map_entered()

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
                status_update(self.config, state='end')
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

    def sortie_status_map_entered(self):
        """
        Show stage and fleet order of this sortie in GUI.
        Fleet order may be swapped before fleet preparation, so it's recorded after entering map.
        """
        try:
            status = dict(stage=self.config.Campaign_Name, order=self.config.Fleet_FleetOrder,
                          battle_total=self._map_battle)
        except Exception as e:
            logger.warning(f'Failed to get sortie status: {e}')
            status = {}
        status_update(self.config, reset=True, state='map', battle=0, **status)

    def handle_campaign_defeat(self):
        """
        Withdraw after fleet defeated and retry later,
        fighting again with the same fleets is likely to be defeated again.

        Pages:
            in: IN_MAP, stage page, or auto search menu
            out: stage page

        Raises:
            TaskEnd:
        """
        logger.hr('Campaign defeat')
        logger.warning(f'Fleet defeated in {self.config.Campaign_Name}, '
                       f'withdraw and retry after {self.DEFEAT_RETRY_MINUTE} minutes')
        # All fleets defeated in auto search
        self.ensure_auto_search_exit()
        if self.is_in_map():
            try:
                self.withdraw()
            except CampaignEnd:
                pass
        self.config.task_delay(minute=self.DEFEAT_RETRY_MINUTE)
        status_update(self.config, state='defeat',
                      until=datetime.now().replace(microsecond=0) + timedelta(minutes=self.DEFEAT_RETRY_MINUTE))
        handle_notify(
            self.config.Error_OnePushConfig,
            title=f"Alas <{self.config.config_name}> campaign defeated",
            content=f"<{self.config.config_name}> fleet defeated in {self.config.Campaign_Name}, "
                    f"retry after {self.DEFEAT_RETRY_MINUTE} minutes",
        )
        self.config.task_stop()

    def handle_combat_low_emotion(self):
        """
        Overwrite info_handler.handle_combat_low_emotion()
        If not ignoring low emotion, cancel the popup instead of waiting until GameStuckError.
        The popup means calculated emotion is higher than the real one,
        usually because other tasks or manual play used the same fleets.
        """
        if not self.low_emotion_withdraw or self.emotion.is_ignore:
            return super().handle_combat_low_emotion()
        if self._story_popup_expected:
            # Popup to confirm story skip has the same buttons, leave it to handle_story_skip()
            return False
        if max(fleet.limit for fleet in self._low_emotion_fleets) < self.LOW_EMOTION_POPUP_VALUE:
            # Emotion control is prevent_red_face, fighting with low emotion is allowed.
            # Popup when entering map doesn't tell which fleet is low, all fleets have to allow it.
            result = self.handle_popup_confirm('IGNORE_LOW_EMOTION')
            if result:
                # Avoid clicking AUTO_SEARCH_MAP_OPTION_OFF
                self.interval_reset(AUTO_SEARCH_MAP_OPTION_OFF)
            return result
        if not self.handle_popup_cancel('IGNORE_LOW_EMOTION'):
            return False
        self.handle_low_emotion_withdraw()
        return True

    @property
    def _story_popup_expected(self):
        """
        Returns:
            bool: If STORY_SKIP was just clicked and story_skip() is waiting for its confirm popup.
        """
        # Don't call reached(), it counts as an access of story_skip()
        timer = self.story_popup_timeout
        return timer.started() and timer.current_time() <= timer.limit

    @property
    def _low_emotion_fleets(self):
        """
        Returns:
            list[FleetEmotion]: Fleets that may trigger the low emotion popup.
        """
        if self.low_emotion_in_map:
            return [self.emotion.fleets[self.fleet_show_index - 1]]
        else:
            return self.emotion.fleets

    def handle_low_emotion_withdraw(self):
        """
        Withdraw after low emotion popup cancelled, correct calculated emotion,
        and delay current task until emotion recovered.

        Pages:
            in: low emotion popup cancelled
            out: stage page

        Raises:
            ScriptEnd: Emotion control, task delayed.
        """
        logger.hr('Emotion withdraw')
        in_preparation = False
        for _ in self.loop():
            if self.handle_story_skip():
                continue
            if self.handle_popup_cancel('IGNORE_LOW_EMOTION'):
                continue

            if self.appear(BATTLE_PREPARATION, offset=(20, 20), interval=2):
                self.device.click(BACK_ARROW)
                continue
            if self.handle_auto_search_exit():
                continue

            # End
            if self.is_in_stage():
                break
            if self.is_in_map():
                try:
                    self.withdraw()
                except CampaignEnd:
                    pass
                break
            if self.appear(FLEET_PREPARATION, offset=(20, 50)) \
                    or self.appear(MAP_PREPARATION, offset=(20, 20)) \
                    or self.appear(MAP_PREPARATION_HARD, offset=(20, 20)):
                in_preparation = True
                self.enter_map_cancel()
                break

        # Popup shown when entering map checks both fleets, otherwise it's the fleet about to battle
        if in_preparation:
            fleets = self.emotion.fleets
        else:
            fleets = [self.emotion.fleets[self.fleet_show_index - 1]]
        self.emotion.update()
        for fleet in fleets:
            if fleet.current > self.LOW_EMOTION_POPUP_VALUE:
                logger.warning(f'Low emotion popup, correct emotion of fleet {fleet.fleet}: '
                               f'{fleet.current} -> {self.LOW_EMOTION_POPUP_VALUE}')
                fleet.current = self.LOW_EMOTION_POPUP_VALUE
        self.emotion.record()

        # Delay until emotion is enough for this map, raise ScriptEnd
        self.emotion.check_reduce(self._map_battle)
        # Still enough for some fleet order, but fleets in popup need to recover anyway
        recovered = max(fleet.get_recovered(self.emotion.reduce_per_battle) for fleet in fleets)
        logger.info('Delay current task to prevent emotion control in the future')
        self.config.task_delay(target=recovered)
        status_update(self.config, state='emotion_delay', until=recovered)
        raise ScriptEnd('Emotion control')

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
