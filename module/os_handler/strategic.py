import numpy as np

from module.base.timer import Timer
from module.base.utils import color_mask, get_color
from module.logger import logger
from module.os_handler.assets import *
from module.os_handler.map_event import MapEventHandler
from module.ui.scroll import Scroll

STRATEGIC_SEARCH_SCROLL = Scroll(STRATEGIC_SEARCH_SCROLL_AREA, color=(247, 211, 66), name='STRATEGIC_SEARCH_SCROLL')
# Option list in strategic search popup, x from option rows, bottom where the list is clipped
STRATEGIC_SEARCH_LIST_AREA = (380, 277, 900, 468)
STRATEGIC_SEARCH_TICK_COLOR = (156, 255, 82)
# Search ticks within this height below merchant title, before the next option title
MERCHANT_OPTION_HEIGHT = 80


class StrategicSearchHandler(MapEventHandler):
    def strategy_search_enter(self):
        logger.info('Strategic search enter')
        self.interval_clear(STRATEGIC_SEARCH_MAP_OPTION_OFF)
        for _ in self.loop():
            # End
            if self.appear(STRATEGIC_SEARCH_POPUP_CHECK, offset=(20, 20)):
                return True

            if self.handle_map_event():
                continue
            if self.appear(AUTO_SEARCH_REWARD, offset=(50, 50)):
                continue
            if self.match_template_color(STRATEGIC_SEARCH_MAP_OPTION_OFF, offset=(20, 20), interval=2):
                self.device.click(STRATEGIC_SEARCH_MAP_OPTION_OFF)
                continue

    def strategic_search_set_tab(self):
        logger.info('Strategic search set tab')
        for _ in self.loop():
            if get_color(self.device.image, STRATEGIC_SEARCH_TAB_SECURED.area)[2] <= 150:
                self.device.click(STRATEGIC_SEARCH_TAB_SECURED)
                continue
            if get_color(self.device.image, STRATEGIC_SEARCH_TAB_SECURED.area)[2] > 150:
                break

    def _strategy_search_scroll_appear(self):
        """
        Returns:
            bool: If it still exists
        """
        for _ in self.loop(timeout=2):
            if STRATEGIC_SEARCH_SCROLL.appear(main=self):
                return True
            else:
                logger.warning('STRATEGIC_SEARCH_SCROLL disappeared')
        else:
            logger.warning('STRATEGIC_SEARCH_SCROLL disappeared confirm')
            return False

    def _strategy_option_selected(self, button):
        """
        Check if a button is selected
        """
        return self.image_color_count(button.button, color=(156, 255, 82), count=30)

    def _strategy_merchant_tick(self):
        """
        Find the green tick in merchant options, below title STRATEGIC_SEARCH_MERCHANT_CHECK.

        Returns:
            tuple[int, int]: (x, y) of tick center, or None if options are not fully visible.
        """
        if not self.appear(STRATEGIC_SEARCH_MERCHANT_CHECK, offset=(20, 200), similarity=0.75):
            return None
        _, _, _, title_bottom = STRATEGIC_SEARCH_MERCHANT_CHECK.button
        x1, _, x2, list_bottom = STRATEGIC_SEARCH_LIST_AREA
        y1 = title_bottom + 2
        y2 = min(title_bottom + MERCHANT_OPTION_HEIGHT, list_bottom)
        if y2 - y1 < 20:
            return None
        image = self.image_crop((x1, y1, x2, y2), copy=False)
        mask = color_mask(image, STRATEGIC_SEARCH_TICK_COLOR, threshold=30)
        ys, xs = np.nonzero(mask)
        if len(xs) <= 30:
            return None
        # Tick cut by the list bottom
        if ys.max() + y1 >= list_bottom - 3:
            return None
        return int(xs.mean()) + x1, int(ys.mean()) + y1

    def _strategic_search_set_merchant(self):
        """
        Set encounter merchant to stop when merchant options are not at their default position.
        Scroll down until options are visible, then classify the tick by column.

        Returns:
            bool: If success.
        """
        stop_x = (STRATEGIC_SEARCH_MERCHANT_STOP.button[0] + STRATEGIC_SEARCH_MERCHANT_STOP.button[2]) // 2
        continue_x = (STRATEGIC_SEARCH_MERCHANT_CONTINUE.button[0] + STRATEGIC_SEARCH_MERCHANT_CONTINUE.button[2]) // 2
        middle = (stop_x + continue_x) // 2
        scrolled = 0
        clicked = 0
        click_interval = Timer(2)
        for _ in self.loop(timeout=20):
            tick = self._strategy_merchant_tick()
            if tick is None:
                if scrolled >= 4:
                    logger.warning('Merchant options not found after scrolling')
                    return False
                position = STRATEGIC_SEARCH_SCROLL.cal_position(main=self)
                STRATEGIC_SEARCH_SCROLL.drag_threshold = 0.05
                STRATEGIC_SEARCH_SCROLL.set(min(position + 0.15, 1), main=self)
                scrolled += 1
                continue
            x, y = tick
            if x < middle:
                logger.attr('encounter_merchant', 'stop')
                return True
            logger.attr('encounter_merchant', 'continue')
            if click_interval.reached():
                if clicked >= 3:
                    logger.warning('Failed to set encounter merchant to stop')
                    return False
                stop = STRATEGIC_SEARCH_MERCHANT_STOP.move((0, y - STRATEGIC_SEARCH_MERCHANT_STOP.button[1] - 7))
                self.device.click(stop)
                clicked += 1
                click_interval.reset()
        else:
            logger.warning('Set encounter merchant timeout')
            return False

    def strategic_search_set_option(self):
        """
        Returns:
            If success. False if strategic settings closed for unknown reason.
        """
        logger.info('Strategic search set option')
        merchant_hidden = Timer(1, count=2)
        for _ in self.loop():
            if self._strategy_option_selected(STRATEGIC_SEARCH_ZONEMODE_REPEAT) \
                    and self._strategy_option_selected(STRATEGIC_SEARCH_MERCHANT_STOP):
                logger.attr('zone_mode', 'repeat')
                logger.attr('encounter_merchant', 'stop')
                break
            if self._strategy_option_selected(STRATEGIC_SEARCH_ZONEMODE_RANDOM):
                logger.attr('zone_mode', 'random')
                self.device.click(STRATEGIC_SEARCH_ZONEMODE_REPEAT)
                merchant_hidden.clear()
                continue
            if self._strategy_option_selected(STRATEGIC_SEARCH_MERCHANT_CONTINUE):
                logger.attr('encounter_merchant', 'continue')
                self.device.click(STRATEGIC_SEARCH_MERCHANT_STOP)
                merchant_hidden.clear()
                continue
            # JP: zone mode options wrap into two lines, pushing merchant options
            # below the visible list, so neither MERCHANT_STOP nor MERCHANT_CONTINUE appears.
            if self._strategy_option_selected(STRATEGIC_SEARCH_ZONEMODE_REPEAT):
                merchant_hidden.start()
                if merchant_hidden.reached():
                    logger.attr('zone_mode', 'repeat')
                    logger.info('Merchant options not at default position, locate by title')
                    if not self._strategic_search_set_merchant():
                        return False
                    break
            else:
                merchant_hidden.clear()

        STRATEGIC_SEARCH_SCROLL.drag_threshold = 0.1
        STRATEGIC_SEARCH_SCROLL.set(0.5, main=self)
        if not self._strategy_search_scroll_appear():
            return False

        for _ in self.loop():
            self.appear(STRATEGIC_SEARCH_DEVICE_CHECK, offset=(20, 200), similarity=0.7)
            STRATEGIC_SEARCH_DEVICE_STOP.load_offset(STRATEGIC_SEARCH_DEVICE_CHECK)
            STRATEGIC_SEARCH_DEVICE_CONTINUE.load_offset(STRATEGIC_SEARCH_DEVICE_CHECK)

            if self._strategy_option_selected(STRATEGIC_SEARCH_DEVICE_STOP):
                logger.attr('encounter_device', 'stop')
                break
            if self._strategy_option_selected(STRATEGIC_SEARCH_DEVICE_CONTINUE):
                logger.attr('encounter_device', 'continue')
                self.device.click(STRATEGIC_SEARCH_DEVICE_STOP)
                continue

        STRATEGIC_SEARCH_SCROLL.drag_threshold = 0.05
        STRATEGIC_SEARCH_SCROLL.edge_add = (0.5, 0.8)
        STRATEGIC_SEARCH_SCROLL.set_bottom(main=self)
        if not self._strategy_search_scroll_appear():
            return False

        for _ in self.loop():
            self.appear(STRATEGIC_SEARCH_SUBMIT_CHECK, offset=(20, 20), similarity=0.7)
            STRATEGIC_SEARCH_SUBMIT_OFF.load_offset(STRATEGIC_SEARCH_SUBMIT_CHECK)
            STRATEGIC_SEARCH_SUBMIT_ON.load_offset(STRATEGIC_SEARCH_SUBMIT_CHECK)

            if self._strategy_option_selected(STRATEGIC_SEARCH_SUBMIT_ON):
                logger.attr('auto_submit', 'on')
                break
            if self._strategy_option_selected(STRATEGIC_SEARCH_SUBMIT_OFF):
                logger.attr('auto_submit', 'off')
                self.device.click(STRATEGIC_SEARCH_SUBMIT_ON)
                continue

        return True

    def strategic_search_confirm(self):
        logger.info('Strategic search confirm')
        for _ in self.loop():
            if self.appear(STRATEGIC_SEARCH_POPUP_CHECK, offset=(20, 20)) \
                    and self.handle_popup_confirm(offset=(30, 30), name='STRATEGIC_SEARCH'):
                continue
            if self.is_in_map():
                return True

    def strategic_search_start(self):
        """
        Returns:
            If success.

        Pages:
            in: IN_MAP
            out: IN_MAP, with strategic search running
        """
        logger.hr('Strategic search start')
        for _ in range(3):
            self.strategy_search_enter()
            self.strategic_search_set_tab()
            success = self.strategic_search_set_option()
            if not success:
                continue
            self.strategic_search_confirm()
            return True

        logger.warning('Failed to start strategic search')
        return False
