import re
from datetime import datetime

from module.base.button import ButtonGrid
from module.base.timer import Timer
from module.campaign.sortie_status import AUTO_SWAP_SUFFIX, EMOTION_SYNC_KEY, FLEET_ORDER_ROLE, status_update
from module.dorm.assets import DORM_TAB_REST, DORM_TAB_TRAINING, DORM_TRAINING_CLOSE, DORM_TRAINING_ENTER
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.ui.page import page_dorm
from module.ui.ui import UI

# Last two rows of the 6 ship cards in dorm training popup
CONDITION_GRID = ButtonGrid(origin=(240, 577), delta=(170, 0), button_shape=(54, 18), grid_shape=(6, 1),
                            name='DORM_CONDITION')
RECOVER_GRID = ButtonGrid(origin=(236, 605), delta=(170, 0), button_shape=(58, 18), grid_shape=(6, 1),
                          name='DORM_RECOVER')


def ocr_ships(image):
    """
    OCR models are chosen by server when Ocr objects are created, create them after server is set.
    With the CN model, `40/h` is read as `4/`.

    Returns:
        list[tuple[int, int]]: (condition, recover speed per hour) of ships on the cards.
    """
    condition = Digit(CONDITION_GRID.buttons, letter=(255, 255, 255), threshold=128, name='OCR_DORM_CONDITION')
    # Recover speed is like `50/h`, `h` is not in the alphabet of OCR model, digits before `/` are used
    recover = Ocr(RECOVER_GRID.buttons, letter=(255, 255, 255), threshold=128, alphabet='0123456789/',
                  name='OCR_DORM_RECOVER')
    return parse_ships(condition.ocr(image), recover.ocr(image))
# Recover speed per hour -> (Emotion.Fleet*Recover, Emotion.Fleet*Oath)
SPEED_TO_RECOVER = {
    20: ('not_in_dormitory', False),
    30: ('not_in_dormitory', True),
    40: ('dormitory_floor_1', False),
    50: ('dormitory_floor_2', False),
    60: ('dormitory_floor_2', True),
}
# Dorm tab of each fleet role in Fleet.FleetOrder. Boss fleet is in training, mob fleet consumes more and rests.
ROLE_TO_TAB = {
    'mob': 'rest',
    'boss': 'training',
}


def parse_ships(conditions, recovers):
    """
    Args:
        conditions (list[int]): Condition of each card.
        recovers (list[str]): Recover speed of each card, OCR result of `50/h`.

    Returns:
        list[tuple[int, int]]: (condition, recover speed per hour) of each ship.
            Empty or locked cards have no recover speed, they are skipped.
    """
    ships = []
    for condition, recover in zip(conditions, recovers):
        res = re.match(r'(\d+)/', recover.replace(' ', ''))
        if not res:
            continue
        ships.append((condition, int(res.group(1))))
    return ships


def fleet_lowest(ships):
    """
    Emotion of a fleet is limited by its lowest ship.

    Returns:
        tuple[int, int]: (condition, recover speed per hour), or None if no ship.
    """
    if not ships:
        return None
    return min(ships, key=lambda ship: ship[0])


def dorm_floor_reversed(result):
    """
    Mob fleet consumes more emotion, so boss fleet in training (floor 1) is expected to be higher than
    mob fleet in rest (floor 2). Lower means user put the fleets on the wrong floors.

    Args:
        result (dict): Result of EmotionSync.emotion_read()

    Returns:
        bool:
    """
    training = result.get('training')
    rest = result.get('rest')
    if training is None or rest is None:
        return False
    return training[0] < rest[0]


class EmotionSync(UI):
    def _training_enter(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_dorm
            out: DORM_TRAINING_CLOSE

        Returns:
            bool: If entered.
        """
        timeout = Timer(15, count=30).start()
        for _ in self.loop(skip_first=skip_first_screenshot):
            if timeout.reached():
                logger.warning('Dorm training popup not found')
                return False
            # End
            if self.appear(DORM_TRAINING_CLOSE, offset=(20, 20)):
                return True

            if self.appear_then_click(DORM_TRAINING_ENTER, offset=(20, 20), interval=3):
                continue
            if self.ui_additional(get_ship=False):
                continue

    def _training_close(self, skip_first_screenshot=True):
        """
        Pages:
            in: DORM_TRAINING_CLOSE
            out: page_dorm
        """
        timeout = Timer(10, count=20).start()
        for _ in self.loop(skip_first=skip_first_screenshot):
            if timeout.reached():
                logger.warning('Failed to close dorm training popup')
                break
            # End, DORM_TRAINING_ENTER is still visible below the popup
            if self.appear(DORM_TRAINING_ENTER, offset=(20, 20)) \
                    and not self.appear(DORM_TRAINING_CLOSE, offset=(20, 20)):
                break

            if self.appear_then_click(DORM_TRAINING_CLOSE, offset=(20, 20), interval=2):
                continue

    def _tab_read(self, tab, skip_first_screenshot=True):
        """
        Args:
            tab (Button): DORM_TAB_TRAINING or DORM_TAB_REST

        Returns:
            list[tuple[int, int]]: (condition, recover speed per hour) of ships in this tab.
        """
        timeout = Timer(10, count=20).start()
        # Cards have an entry animation after popup shown or tab switched, wait before reading
        settle = Timer(1, count=2).start()
        click_timer = Timer(2)
        prev = None
        for _ in self.loop(skip_first=skip_first_screenshot):
            if timeout.reached():
                logger.warning(f'Failed to read dorm ships in {tab}')
                return []
            # Tab is selected if its template matches, unselected tabs have inverted colors
            if not self.appear(tab, offset=(10, 10)):
                # Click interval, avoid GameTooManyClickError if tab never matches
                if not click_timer.started() or click_timer.reached():
                    self.device.click(tab)
                    click_timer.reset()
                prev = None
                settle.reset()
                continue
            if not settle.reached():
                continue
            ships = ocr_ships(self.device.image)
            if ships and ships == prev:
                logger.attr(f'Dorm ships {tab}', ships)
                return ships
            prev = ships

    def emotion_read(self):
        """
        Pages:
            in: Any
            out: page_dorm

        Returns:
            dict: Key: `training` or `rest`, value: (condition, recover speed per hour) of the lowest ship,
                or None if unable to read.
        """
        logger.hr('Emotion read', level=2)
        self.ui_ensure(page_dorm)
        if not self._training_enter():
            return {}
        result = {
            'training': fleet_lowest(self._tab_read(DORM_TAB_TRAINING)),
            'rest': fleet_lowest(self._tab_read(DORM_TAB_REST)),
        }
        self._training_close()
        logger.attr('Dorm emotion', result)
        return result

    def emotion_sync(self, emotion):
        """
        Read real emotion of the mob fleet and boss fleet in dorm, save them to config.

        Args:
            emotion (Emotion):

        Returns:
            bool: If any fleet synced.
        """
        logger.hr('Emotion sync', level=1)
        result = self.emotion_read()
        return emotion_apply(emotion, result)


def emotion_apply(emotion, result):
    """
    Save real emotion to the fleet slots using it, and other tasks using the same game fleets.

    Args:
        emotion (Emotion): Its config is used, which is a copy in CampaignRun.campaign
        result (dict): Result of EmotionSync.emotion_read()

    Returns:
        bool: If any fleet synced.
    """
    config = emotion.config
    # Fleet order in user config, not the one swapped in this task
    order = config.cross_get(f'{config.task.command}.Fleet.FleetOrder', default='')
    if order.endswith(AUTO_SWAP_SUFFIX):
        order = order[:-len(AUTO_SWAP_SUFFIX)]
    roles = FLEET_ORDER_ROLE.get(order)
    if roles is None or any(role not in ROLE_TO_TAB for role in roles):
        logger.warning(f'Emotion sync requires mob fleet and boss fleet, but fleet order is {order}')
        return False

    emotion.update()
    settings = {}
    synced = {}
    for fleet in emotion.fleets:
        if not fleet.number:
            continue
        tab = ROLE_TO_TAB[roles[fleet.fleet - 1]]
        ship = result.get(tab)
        if ship is None:
            continue
        condition, speed = ship
        logger.info(f'Emotion sync fleet {fleet.fleet} (game fleet {fleet.number}, {tab}): '
                    f'{fleet.current} -> {condition}, {speed}/h')
        fleet.current = condition
        synced[str(fleet.number)] = {'value': condition, 'speed': speed, 'tab': tab}
        if speed in SPEED_TO_RECOVER:
            recover, oath = SPEED_TO_RECOVER[speed]
            settings[f'Fleet{fleet.fleet}Recover'] = recover
            settings[f'Fleet{fleet.fleet}Oath'] = oath
        else:
            logger.warning(f'Unknown recover speed {speed}/h, keep emotion recover settings')
    if not synced:
        logger.warning('Emotion sync failed, keep calculated emotion')
        return False

    reversed_ = dorm_floor_reversed(result)
    if reversed_:
        logger.warning(f'Boss fleet in training ({result["training"][0]}) is lower than '
                       f'mob fleet in rest ({result["rest"][0]}), dorm floors may be reversed. '
                       f'Put boss fleet on floor 1 (training) and mob fleet on floor 2 (rest)')

    with config.multi_set():
        for key, value in settings.items():
            setattr(config, f'Emotion_{key}', value)
        emotion.record()
        emotion.record_shared(**settings)
    status_update(config, reset=True, key=EMOTION_SYNC_KEY, time=datetime.now().replace(microsecond=0),
                  fleets=synced, reversed=reversed_)
    return True


if __name__ == '__main__':
    self = EmotionSync('alas', task='Main')
    self.device.screenshot()
    print(self.emotion_read())
