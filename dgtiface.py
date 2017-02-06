# Copyright (C) 2013-2017 Jean-Francois Romang (jromang@posteo.de)
#                         Shivkumar Shivaji ()
#                         Jürgen Précour (LocutusOfPenguin@posteo.de)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from utilities import *
from threading import Timer, Thread, Lock


class DgtIface(DisplayDgt, Thread):
    def __init__(self, dgttranslate, msg_lock, dgtboard=None):
        super(DgtIface, self).__init__()

        self.dgtboard = dgtboard
        self.dgttranslate = dgttranslate

        self.enable_dgt_3000 = False
        self.enable_ser_clock = False
        self.time_left = None
        self.time_right = None

        self.maxtimer = None
        self.maxtimer_running = False
        self.clock_running = False
        self.time_factor = 1  # This is for testing the duration - remove it lateron!
        # delayed task array
        self.tasks = []
        self.do_process = True
        self.msg_lock = msg_lock

        self.display_hash = None  # Hash value of clock's display

    def display_text_on_clock(self, message):
        raise NotImplementedError()

    def display_move_on_clock(self, message):
        raise NotImplementedError()

    def display_time_on_clock(self, message):
        raise NotImplementedError()

    def light_squares_revelation_board(self, squares):
        raise NotImplementedError()

    def clear_light_revelation_board(self):
        raise NotImplementedError()

    def stop_clock(self, devs):
        raise NotImplementedError()

    def _resume_clock(self, side):
        raise NotImplementedError()

    def start_clock(self, time_left, time_right, side, devs):
        raise NotImplementedError()

    def _stopped_maxtimer(self):
        self.maxtimer_running = False
        if self.clock_running:
            logging.debug('showing the running clock again')
            self.show(Dgt.DISPLAY_TIME(force=False, wait=True, devs={'ser', 'i2c', 'web'}))
        else:
            logging.debug('clock not running - ignored maxtime')
        if self.tasks:
            logging.debug('processing delayed tasks: {}'.format(self.tasks))
        while self.tasks:
            message = self.tasks.pop(0)
            self._process_message(message)
            if self.maxtimer_running:  # run over the task list until a maxtime command was processed
                break

    def _handle_message(self, message):
        for case in switch(message):
            if case(DgtApi.DISPLAY_MOVE):
                self.display_move_on_clock(message)
                break
            if case(DgtApi.DISPLAY_TEXT):
                self.display_text_on_clock(message)
                break
            if case(DgtApi.DISPLAY_TIME):
                self.display_time_on_clock(message)
                break
            if case(DgtApi.LIGHT_CLEAR):
                self.clear_light_revelation_board()
                break
            if case(DgtApi.LIGHT_SQUARES):
                self.light_squares_revelation_board(message.uci_move)
                break
            if case(DgtApi.CLOCK_STOP):
                if self.clock_running:
                    self.stop_clock(message.devs)
                else:
                    logging.debug('clock is already stopped')
                break
            if case(DgtApi.CLOCK_START):
                # log times
                l_hms = hours_minutes_seconds(message.time_left)
                r_hms = hours_minutes_seconds(message.time_right)
                logging.debug('last time received from clock l:{} r:{}'.format(self.time_left, self.time_right))
                logging.debug('sending time to clock l:{} r:{}'.format(l_hms, r_hms))
                self.start_clock(message.time_left, message.time_right, message.side, message.devs)
                break
            if case(DgtApi.CLOCK_VERSION):
                text = self.dgttranslate.text('Y20_picochess', devs={message.dev})
                text.rd = ClockIcons.DOT
                self.show(text)
                self.show(Dgt.DISPLAY_TIME(force=True, wait=True, devs={message.dev}))
                if message.dev != 'i2c':
                    self.enable_ser_clock = True
                    if message.main == 2:
                        self.enable_dgt_3000 = True
                break
            if case(DgtApi.CLOCK_TIME):
                logging.debug('({}) clock: received time from clock l:{} r:{}'.format(message.dev, message.time_left, message.time_right))
                self.time_left = message.time_left
                self.time_right = message.time_right
                break
            if case():  # Default
                pass

    def _process_message(self, message):
        do_handle = True
        if repr(message) in (DgtApi.CLOCK_START, DgtApi.CLOCK_STOP, DgtApi.CLOCK_TIME):
            self.display_hash = None  # Cant know the clock display if command changing the running status
        else:
            if repr(message) in (DgtApi.DISPLAY_MOVE, DgtApi.DISPLAY_TEXT):
                if self.display_hash == hash(message) and not message.beep:
                    do_handle = False
                else:
                    self.display_hash = hash(message)

        if do_handle:
            logging.debug("handle DgtApi: {} at {}".format(message, self.__class__.__name__))
            if hasattr(message, 'maxtime') and message.maxtime > 0:
                self.maxtimer = Timer(message.maxtime * self.time_factor, self._stopped_maxtimer)
                self.maxtimer.start()
                logging.debug('showing {} for {} secs'.format(message, message.maxtime * self.time_factor))
                self.maxtimer_running = True
            # logging.info('wait for msg_lock at {}'.format(self.__class__.__name__))
            with self.msg_lock:
                # logging.info('done for msg_lock at {}'.format(self.__class__.__name__))
                self._handle_message(message)
                # logging.info('handled at {}'.format(self.__class__.__name__))
        else:
            logging.debug("ignore DgtApi: {} at {}".format(message, self.__class__.__name__))

    def run(self):
        logging.info('dgt_queue ready')
        while True:
            # Check if we have something to display
            try:
                message = self.dgt_queue.get()
                logging.debug("received command from dgt_queue: %s", message)

                self.do_process = True
                if self.maxtimer_running:
                    if hasattr(message, 'wait'):
                        if message.wait:
                            self.tasks.append(message)
                            logging.debug('tasks delayed: {}'.format(self.tasks))
                            self.do_process = False
                        else:
                            logging.debug('ignore former maxtime')
                            self.maxtimer.cancel()
                            self.maxtimer_running = False
                            if self.tasks:
                                logging.debug('delete following tasks: {}'.format(self.tasks))
                                self.tasks = []
                    else:
                        logging.debug('command doesnt change the clock display => no need to interrupt max timer')
                else:
                    logging.debug('max timer not running')

                if self.do_process:
                    self._process_message(message)
                else:
                    logging.debug('task delayed: {}'.format(message))
            except queue.Empty:
                pass
